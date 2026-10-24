/**
 * 链记 LinkNote — 实时连接客户端（WebSocket 推送）
 *
 * 契约：docs/realtime-protocol.md（已冻结）
 * - 手机：POST /api/v1/realtime/ticket → GET /api/v1/ws?ticket=...
 * - 断线指数退避 1/2/4/8/15s；ticket 过期自动重新取（每次重连都取新 ticket）
 * - 心跳：每 25s 发 ping；70s 没有收到任何消息视为假死，主动重连
 * - presence / task_updated / note / note_unavailable 以事件形式分发给页面
 * - note 写本地缓存（IndexedDB 优先）后发 ack_note
 *
 * 兼容：Relay 尚未实现 /realtime/ticket（404/405/501）时进入 legacy 模式，
 * 页面退回按需 REST 快照（不再 3s 轮询），保证旧 Relay / UI 冒烟可用。
 */
import {
  API_PREFIX,
  REALTIME_BACKOFF_MS,
  REALTIME_HEARTBEAT_MS,
  REALTIME_LEGACY_PROBE_MS,
  REALTIME_SILENCE_MS,
  REALTIME_TICKET_TIMEOUT_MS,
} from './config.js';
import { api, isAuthError } from './api.js';
import { auth, noteCache, settings, taskCache } from './store.js';

export const REALTIME_STATES = {
  IDLE: 'idle',
  CONNECTING: 'connecting',
  OPEN: 'open',
  RECONNECTING: 'reconnecting',
  LEGACY: 'legacy',
  STOPPED: 'stopped',
};

const bus = new EventTarget();

let state = REALTIME_STATES.IDLE;
let mode = 'unknown'; // 'ws' | 'legacy' | 'unknown'
let started = false;
let socket = null;
let attempt = 0;
let retryTimer = null;
let retryAt = 0;
let heartbeatTimer = null;
let lastMessageAt = 0;
let presenceOnline = null;
let presenceLastSeen = null;
let presenceUpdatedAt = 0;
let agentId = null;
let lastLegacyProbeAt = 0;
let globalListenersInstalled = false;
/** 连续「刚取到 ticket 就 1006」的次数：≥2 时视为 Relay 不可用（不清 token） */
let immediateFailures = 0;
let connectStartedAt = 0;

function emit(type, detail = {}) {
  try {
    bus.dispatchEvent(new CustomEvent(type, { detail }));
  } catch (error) {
    // 老环境没有 CustomEvent 构造器时忽略
  }
}

/**
 * 订阅实时事件。
 * @param {'state'|'presence'|'hello'|'task_updated'|'note'|'note_unavailable'|'legacy'|'reconnect-scheduled'|'auth-error'} type
 * @param {(detail: any) => void} handler
 * @returns {() => void}
 */
export function onRealtime(type, handler) {
  if (typeof handler !== 'function') return () => {};
  const listener = (event) => handler(event.detail);
  bus.addEventListener(type, listener);
  return () => bus.removeEventListener(type, listener);
}

export function getRealtimeSnapshot() {
  return {
    state,
    mode,
    agentId,
    attempt,
    immediateFailures,
    retryIn: retryAt ? Math.max(0, retryAt - Date.now()) : 0,
    presence: {
      online: presenceOnline,
      lastSeen: presenceLastSeen,
      updatedAt: presenceUpdatedAt,
    },
  };
}

export function isRealtimeOpen() {
  return Boolean(socket && socket.readyState === 1 && state === REALTIME_STATES.OPEN);
}

export function isLegacyMode() {
  return mode === 'legacy';
}

/** 给顶部状态胶囊用的文案：在线/离线/连接中/重连中/兼容模式 */
export function describeRealtime(snapshot = getRealtimeSnapshot()) {
  if (snapshot.mode === 'legacy') {
    return {
      label: snapshot.presence.online === false ? '电脑离线' : snapshot.presence.online === true ? '电脑在线' : '兼容模式',
      tone: snapshot.presence.online === false ? 'offline' : snapshot.presence.online === true ? 'online' : 'legacy',
      detail: '当前 Relay 不支持实时推送，使用按需刷新',
    };
  }
  switch (snapshot.state) {
    case REALTIME_STATES.OPEN:
      if (snapshot.presence.online === false) return { label: '电脑离线', tone: 'offline', detail: '电脑上的 Agent 没有连接' };
      return { label: '电脑在线', tone: 'online', detail: '实时连接已建立' };
    case REALTIME_STATES.CONNECTING:
      return { label: '连接中', tone: 'connecting', detail: '正在建立实时连接' };
    case REALTIME_STATES.RECONNECTING:
      return { label: '重连中', tone: 'reconnecting', detail: '实时连接断开，正在自动重连' };
    case REALTIME_STATES.LEGACY:
      return { label: '兼容模式', tone: 'legacy', detail: '当前 Relay 不支持实时推送，使用按需刷新' };
    case REALTIME_STATES.STOPPED:
    case REALTIME_STATES.IDLE:
    default:
      return { label: '未连接', tone: 'offline', detail: '实时连接未建立' };
  }
}

/* ------------------------------------------------------------------ */
/* 内部：状态 / 工具                                                    */
/* ------------------------------------------------------------------ */

function setState(next) {
  state = next;
  emit('state', getRealtimeSnapshot());
}

function setPresence(online, lastSeen) {
  const nextOnline = typeof online === 'boolean' ? online : presenceOnline;
  const nextSeen = lastSeen || presenceLastSeen;
  const changed = nextOnline !== presenceOnline || nextSeen !== presenceLastSeen;
  presenceOnline = nextOnline;
  presenceLastSeen = nextSeen;
  presenceUpdatedAt = Date.now();
  emit('presence', { online: presenceOnline, lastSeen: presenceLastSeen, changed });
  if (changed) emit('state', getRealtimeSnapshot());
}

function currentSession() {
  const base = settings.getApiBase();
  if (!base) return null;
  const entry = auth.get(base);
  if (!entry || !entry.token) return null;
  return { base, token: entry.token };
}

function buildWsUrl(base, ticket) {
  const url = new URL(base);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = `${API_PREFIX}/ws`;
  url.search = `?ticket=${encodeURIComponent(ticket)}`;
  url.hash = '';
  return url.toString();
}

function isRealtimeUnsupported(error) {
  if (!error) return false;
  const status = Number(error.status) || 0;
  if ([404, 405, 501].includes(status)) return true;
  return ['not_found', 'not_implemented', 'unsupported', 'http_404', 'http_405', 'http_501'].includes(
    error.code,
  );
}

function clearRetry() {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  retryAt = 0;
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function closeSocket() {
  const ws = socket;
  socket = null;
  if (!ws) return;
  try {
    if (ws.readyState === 0 || ws.readyState === 1) ws.close(1000, 'client closed');
  } catch (error) {
    // 忽略
  }
}

function send(payload) {
  if (!socket || socket.readyState !== 1) return false;
  try {
    socket.send(JSON.stringify(payload));
    return true;
  } catch (error) {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* 内部：连接 / 重连                                                    */
/* ------------------------------------------------------------------ */

function openSocket(base, ticket) {
  closeSocket();
  stopHeartbeat();
  let ws;
  try {
    ws = new WebSocket(buildWsUrl(base, ticket));
  } catch (error) {
    scheduleReconnect();
    return;
  }
  socket = ws;
  connectStartedAt = Date.now();
  lastMessageAt = Date.now();

  ws.onopen = () => {
    if (socket !== ws) return;
    lastMessageAt = Date.now();
    startHeartbeat(ws);
  };
  ws.onmessage = (event) => {
    if (socket !== ws) return;
    handleMessage(event.data);
  };
  ws.onerror = () => {
    // 浏览器随后会触发 close，统一在 close 里重连。
  };
  ws.onclose = (event) => {
    if (socket !== ws) return;
    const wasOpen = state === REALTIME_STATES.OPEN;
    const code = event ? event.code : 0;
    socket = null;
    stopHeartbeat();
    if (!started) return;

    // Relay 用 HTTP 握手拒绝非法/过期 ticket 时，浏览器只能看到 1006：
    // 每次重连都会重新取 ticket，连续两次「连上 instant 1006」就提示稍后重试（不清 token）。
    const immediate = !wasOpen && Date.now() - connectStartedAt < 5000;
    if (immediate) {
      immediateFailures += 1;
      if (immediateFailures >= 2) emit('ticket-rejected', { failures: immediateFailures, code });
    } else if (wasOpen) {
      immediateFailures = 0;
    }

    emit('disconnected', { code, reason: event ? event.reason : '' });
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (!started) return;
  clearRetry();
  const index = Math.min(attempt, REALTIME_BACKOFF_MS.length - 1);
  const delay = REALTIME_BACKOFF_MS[index] + Math.floor(Math.random() * 250);
  attempt += 1;
  retryAt = Date.now() + delay;
  setState(REALTIME_STATES.RECONNECTING);
  emit('reconnect-scheduled', { attempt, delayMs: delay });
  retryTimer = setTimeout(() => {
    retryTimer = null;
    retryAt = 0;
    void connect();
  }, delay);
}

async function connect() {
  if (!started) return;
  clearRetry();
  const session = currentSession();
  if (!session) {
    stopRealtime();
    return;
  }
  setState(attempt > 0 ? REALTIME_STATES.RECONNECTING : REALTIME_STATES.CONNECTING);

  let data;
  try {
    data = await api.getRealtimeTicket({ base: session.base, timeout: REALTIME_TICKET_TIMEOUT_MS });
  } catch (error) {
    if (!started) return;
    if (error && error.code === 'aborted') return;
    if (isAuthError(error)) {
      emit('auth-error', { error });
      stopRealtime();
      return;
    }
    if (isRealtimeUnsupported(error)) {
      mode = 'legacy';
      lastLegacyProbeAt = Date.now();
      setState(REALTIME_STATES.LEGACY);
      emit('legacy', { error });
      return;
    }
    scheduleReconnect();
    return;
  }

  if (!started) return;
  const ticket = data && typeof data.ticket === 'string' ? data.ticket : '';
  if (!ticket) {
    scheduleReconnect();
    return;
  }
  if (data.agent_id) agentId = data.agent_id;
  // relay-dev 的增量字段：首屏在 hello 之前就能画出真实在线态。
  if (typeof data.agent_online === 'boolean') setPresence(data.agent_online, null);
  openSocket(session.base, ticket);
}

function startHeartbeat(ws) {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (socket !== ws) return;
    if (Date.now() - lastMessageAt > REALTIME_SILENCE_MS) {
      // 假死（网络断开但 close 没触发）：主动关闭走重连。
      try {
        ws.close(4000, 'heartbeat timeout');
      } catch (error) {
        // 忽略
      }
      return;
    }
    send({ type: 'ping', ts: Date.now() });
  }, REALTIME_HEARTBEAT_MS);
}

/* ------------------------------------------------------------------ */
/* 内部：消息分发                                                       */
/* ------------------------------------------------------------------ */

function handleMessage(raw) {
  lastMessageAt = Date.now();
  let message;
  try {
    message = JSON.parse(raw);
  } catch (error) {
    return;
  }
  if (!message || typeof message.type !== 'string') return;

  switch (message.type) {
    case 'hello': {
      attempt = 0;
      immediateFailures = 0;
      if (typeof message.agent_id === 'string' && message.agent_id) agentId = message.agent_id;
      const online = typeof message.online === 'boolean' ? message.online : true;
      setState(REALTIME_STATES.OPEN);
      setPresence(online, null);
      emit('hello', {
        agentId,
        online,
        serverTime: message.server_time || null,
      });
      break;
    }
    case 'presence': {
      setPresence(message.online, message.last_seen || null);
      break;
    }
    case 'task_updated': {
      if (message.task && typeof message.task.id === 'string' && message.task.id) {
        const task = normalizeRealtimeTask(message.task);
        const base = settings.getApiBase();
        if (base) taskCache.upsert(base, task);
        emit('task_updated', { task });
      }
      break;
    }
    case 'note': {
      if (typeof message.task_id === 'string' && typeof message.markdown === 'string') {
        void handleNote(message);
      }
      break;
    }
    case 'note_unavailable': {
      if (typeof message.task_id === 'string') {
        emit('note_unavailable', {
          taskId: message.task_id,
          reason: typeof message.reason === 'string' ? message.reason : 'agent_offline',
        });
      }
      break;
    }
    case 'ping': {
      send({ type: 'pong', ts: typeof message.ts === 'number' ? message.ts : Date.now() });
      break;
    }
    case 'pong': {
      break;
    }
    default:
      break;
  }
}

/** WS task_updated → 页面任务摘要：补齐 error 字段、把 Agent 的 completed 映射成 succeeded */
function normalizeRealtimeTask(task) {
  const next = { ...task };
  if (next.error && !next.error_message) next.error_message = next.error;
  if (next.status === 'completed') next.status = 'succeeded';
  if (next.status === 'succeeded') {
    next.progress = 100;
    next.stage = next.stage || 'done';
  }
  if (typeof next.updated_at !== 'string' || !next.updated_at) {
    next.updated_at = new Date().toISOString();
  }
  return next;
}

/** 收到正文：先写本地缓存，再 ack，最后通知页面渲染（协议 §2.2/§2.3） */
async function handleNote(message) {
  const base = settings.getApiBase();
  let cached = false;
  try {
    cached = await noteCache.put(base, message.task_id, {
      title: typeof message.title === 'string' ? message.title : '',
      markdown: message.markdown,
    });
  } catch (error) {
    cached = false;
  }
  emit('note', {
    taskId: message.task_id,
    markdown: message.markdown,
    title: typeof message.title === 'string' ? message.title : '',
    cached,
  });
  // 无论缓存是否成功都 ack，避免 Agent 无限重发；cached=false 时页面会提示本地缓存失败。
  send({ type: 'ack_note', task_id: message.task_id });
}

/* ------------------------------------------------------------------ */
/* 公共 API                                                            */
/* ------------------------------------------------------------------ */

function installGlobalListeners() {
  if (globalListenersInstalled || typeof window === 'undefined') return;
  globalListenersInstalled = true;

  window.addEventListener('online', () => {
    if (!started) return;
    if (mode === 'legacy') {
      maybeProbeLegacy();
      return;
    }
    if (!isRealtimeOpen()) {
      clearRetry();
      if (attempt > 1) attempt = 1;
      void connect();
    }
  });

  window.addEventListener('offline', () => {
    if (!started || mode === 'legacy') return;
    if (!isRealtimeOpen()) setState(REALTIME_STATES.RECONNECTING);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || !started) return;
    if (mode === 'legacy') {
      maybeProbeLegacy();
      return;
    }
    if (!isRealtimeOpen()) {
      clearRetry();
      void connect();
      return;
    }
    send({ type: 'ping', ts: Date.now() });
  });
}

function maybeProbeLegacy() {
  if (mode !== 'legacy' || !started) return;
  if (Date.now() - lastLegacyProbeAt < REALTIME_LEGACY_PROBE_MS) return;
  lastLegacyProbeAt = Date.now();
  mode = 'unknown';
  attempt = 0;
  void connect();
}

/** 建立/恢复实时连接（已连接时幂等） */
export function startRealtime({ force = false } = {}) {
  installGlobalListeners();
  const session = currentSession();
  if (!session) {
    stopRealtime();
    return;
  }
  if (!force && started) {
    if (socket) return;
    if (retryTimer) return;
    if (mode === 'legacy') return;
  }
  if (force) teardown();
  started = true;
  attempt = 0;
  mode = 'unknown';
  void connect();
}

function teardown() {
  started = false;
  clearRetry();
  closeSocket();
  stopHeartbeat();
  attempt = 0;
}

/** 主动断开（解除配对 / 切换 Relay / 401） */
export function stopRealtime() {
  teardown();
  mode = 'unknown';
  agentId = null;
  presenceOnline = null;
  presenceLastSeen = null;
  presenceUpdatedAt = 0;
  setState(REALTIME_STATES.IDLE);
}

/** 切换 Relay / 手动刷新实时连接 */
export function restartRealtime() {
  teardown();
  startRealtime({ force: true });
}

/** 设置页「刷新实时连接」：legacy 模式下也允许重新探测 */
export function probeRealtime() {
  if (mode === 'legacy') {
    lastLegacyProbeAt = 0;
    maybeProbeLegacy();
    return;
  }
  if (!started) {
    startRealtime({ force: true });
    return;
  }
  if (!isRealtimeOpen()) {
    clearRetry();
    attempt = 0;
    void connect();
  }
}

/** 打开未缓存笔记：向 Relay/Agent 请求正文 */
export function requestNote(taskId) {
  if (!taskId) return false;
  return send({ type: 'note_request', task_id: taskId });
}

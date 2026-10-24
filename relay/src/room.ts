/**
 * AgentRoom v0.3：每个 agent 一个 Durable Object（SQLite 后端 + WebSocket Hibernation）。
 *
 * 职责：
 * - Agent socket（每台电脑最多 1 条）与手机 socket 的连接、鉴权转发（Worker 校验 token/ticket 后才转发进来）；
 * - 在线状态：agent socket 存在即 online（探活失败可临时标 offline）；close/error 立即 offline；
 * - 实时事件：任务推 Agent、task_update 更新 D1 并推手机、task_note 转发手机（正文绝不落 D1/DO SQLite）、
 *   note_request 转给 Agent、ack_note（含 persisted）转给 Agent、readiness（agent_ready）透传给手机；
 * - 内部事件（Worker 的 REST 流程调用）：task_created / task_snapshot / note_forward / device_revoked。
 *
 * v0.3 成本约束（docs/v0.3-protocol-delta.md §1）：
 * - 只有存在手机 socket 时才 setAlarm / 主动 ping；没有手机时不设 alarm、不 ping、不写 room_meta SQL；
 * - last_seen 只放内存，online / last_seen_at / ready / issues 只在跃迁或变化时写 room_meta；
 * - 手机连接时若 agent 上次活动超过 15s：ping+probe + 3s 超时探活，无响应则 presence offline（不强行 close）；
 * - 兼容旧 Agent：JSON ping 照常回 pong；旧手机 ack 缺 persisted 视为 true。
 *
 * 隔离硬约束：所有 D1 查询都带 `agent_id = ?`（来自 DO 实例自身绑定的 agent_id，绝不来自客户端消息）。
 * 多租户隔离：DO 实例按 `idFromName(agent_id)` 划分，A 的 socket 收不到 B 的事件。
 */

import { bytesOf, isoAfter, isoNow, json } from './lib/http';
import { TASK_SUMMARY_COLUMNS, toTaskSummary } from './lib/rows';
import type { AgentIssue, AgentPushTask, Env, TaskSummary } from './types';

const ROLE_AGENT = 'agent';
const ROLE_PHONE = 'phone';

const WS_CLOSE_REPLACED = 4001;
const WS_CLOSE_TIMEOUT = 4002;

/** 与 PC Agent / PWA 的本地校验保持一致（contract §10）。 */
const MAX_NOTE_BYTES = 1_572_864;
const MAX_PENDING_TASKS_ON_CONNECT = 50;
const MAX_ISSUES = 20;

const PROGRESS_STAGES = new Set([
  'pending',
  'parsing',
  'downloading',
  'transcribing',
  'summarizing',
  'formatting',
  'saving',
  'done',
  'failed',
]);

interface SocketAttachment {
  role: 'agent' | 'phone';
  /** 单条连接的唯一 id；用于区分「被新连接顶掉的旧 socket」与真正的断开。 */
  conn: string;
  device_id?: string;
  /** 仅手机使用：最后一次收到消息的时间（内存 attachment，不写 SQL）。 */
  last_seen: number;
  superseded?: boolean;
}

interface InternalEventBody {
  type?: unknown;
  task?: unknown;
  task_id?: unknown;
  markdown?: unknown;
  title?: unknown;
  device_id?: unknown;
}

function intVar(raw: string | undefined, fallback: number, min: number, max: number): number {
  // 注意：不能用 `Number(raw || '')` —— 未配置时 Number('') === 0 会被 clamp 到 min，
  // 导致默认值（如 alarm 30s）失效（v0.2 线上曾因此变成 1s alarm）。
  // 未配置或空串必须回落到 fallback。
  if (raw == null || raw === '') return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(n)));
}

function cleanTitle(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const title = value.replace(/[\r\n\t]+/g, ' ').trim();
  return title ? title.slice(0, 200) : null;
}

function shortText(value: unknown, max: number): string | null {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  return text ? text.slice(0, max) : null;
}

function asTaskId(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const id = value.trim();
  return /^[A-Za-z0-9_-]{8,80}$/.test(id) ? id : null;
}

/** readiness issues：最多 20 项，code ≤64、label ≤200；非法项丢弃。 */
function sanitizeIssues(value: unknown): AgentIssue[] {
  if (!Array.isArray(value)) return [];
  const issues: AgentIssue[] = [];
  for (const item of value.slice(0, MAX_ISSUES)) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    const code = shortText(row.code, 64);
    if (!code) continue;
    const label = shortText(row.label, 200) || code;
    issues.push({ code, label });
  }
  return issues;
}

function normalizePushTask(task: unknown): AgentPushTask | null {
  if (!task || typeof task !== 'object') return null;
  const row = task as Record<string, unknown>;
  const id = asTaskId(row.id);
  if (!id || typeof row.url !== 'string') return null;
  return {
    id,
    url: row.url.slice(0, 2048),
    platform: typeof row.platform === 'string' ? row.platform : 'unknown',
    status: typeof row.status === 'string' ? row.status : 'queued',
    stage: typeof row.stage === 'string' ? row.stage : undefined,
    progress: typeof row.progress === 'number' ? row.progress : undefined,
    title: cleanTitle(row.title),
    created_at: typeof row.created_at === 'string' ? row.created_at : isoNow(),
  };
}

function parseMessage(raw: string | ArrayBuffer): Record<string, unknown> | null {
  let text: string;
  if (typeof raw === 'string') {
    text = raw;
  } else {
    try {
      text = new TextDecoder().decode(raw);
    } catch {
      return null;
    }
  }
  if (!text || text.length > 8 * 1024 * 1024) return null;
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    return value as Record<string, unknown>;
  } catch {
    return null;
  }
}

export class AgentRoom {
  private readonly ctx: DurableObjectState;
  private readonly env: Env;
  /** 串行处理 socket 消息：DO 的事件处理在 await 处会交错，按到达顺序串起来才能保证重放/结束的顺序。 */
  private processing: Promise<void> = Promise.resolve();
  private agentId: string | null = null;
  /** 持久化的 online 跃迁状态（只在跃迁时写 room_meta）。 */
  private online = false;
  /** 内存 last_seen（ms）；构造时从 room_meta 的上次跃迁时间恢复，之后只在内存更新。 */
  private agentLastSeen = 0;
  private realtime = false;
  /** readiness（内存；变化时写 room_meta）。null = 未知（旧 Agent 不上报）。 */
  private ready: boolean | null = null;
  private issues: AgentIssue[] = [];
  /** 手机连接时对 agent 的探活状态。 */
  private probePending = false;
  private probeUnreachable = false;
  /** alarm 判定 stale 时:探活超时后需要关闭 agent socket。 */
  private probeCloseOnTimeout = false;
  private probeTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    this.ctx = ctx;
    this.env = env;
    ctx.blockConcurrencyWhile(async () => {
      const sql = this.ctx.storage.sql;
      sql.exec(
        `CREATE TABLE IF NOT EXISTS room_meta (
           key   TEXT PRIMARY KEY,
           value TEXT NOT NULL
         )`,
      );
      // 只记「正文已收到但未送达」的 task_id，绝不存正文。
      sql.exec(
        `CREATE TABLE IF NOT EXISTS undelivered_notes (
           task_id     TEXT PRIMARY KEY,
           received_at TEXT NOT NULL
         )`,
      );
      this.agentId = this.getMeta('agent_id');
      this.online = this.getMeta('online') === '1';
      const lastSeenIso = this.getMeta('last_seen_at');
      const lastSeenMs = lastSeenIso ? Date.parse(lastSeenIso) : Number.NaN;
      this.agentLastSeen = Number.isFinite(lastSeenMs) ? lastSeenMs : 0;
      this.realtime = this.getMeta('realtime') === '1';
      const readyRaw = this.getMeta('ready');
      this.ready = readyRaw === '1' ? true : readyRaw === '0' ? false : null;
      if (this.ready !== null) {
        try {
          this.issues = sanitizeIssues(JSON.parse(this.getMeta('issues') || '[]'));
        } catch {
          this.issues = [];
        }
      }
    });
  }

  // ------------------------------------------------------------ storage

  private getMeta(key: string): string | null {
    const rows = this.ctx.storage.sql
      .exec<{ value: string }>('SELECT value FROM room_meta WHERE key = ?', key)
      .toArray();
    return rows.length ? rows[0].value : null;
  }

  /** 多条 meta 合并成 1 条 SQL 写入（跃迁/readiness 变化时才调用，禁止每条消息写）。 */
  private persistMeta(entries: Array<[string, string]>): void {
    if (!entries.length) return;
    const placeholders = entries.map(() => '(?, ?)').join(', ');
    const values = entries.flatMap(([key, value]) => [key, value]);
    this.ctx.storage.sql.exec(
      `INSERT INTO room_meta (key, value) VALUES ${placeholders}
       ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
      ...values,
    );
  }

  private requireAgentId(): string {
    if (!this.agentId) throw new Error('agent room is not bound to an agent_id');
    return this.agentId;
  }

  private bindAgentId(headerValue: string | null): string {
    if (!headerValue || headerValue.length > 64) throw new Error('missing x-agent-id header');
    if (this.agentId && this.agentId !== headerValue) {
      throw new Error('agent_id mismatch for this Durable Object');
    }
    if (!this.agentId) {
      this.agentId = headerValue;
      this.setMeta('agent_id', headerValue);
    }
    return this.agentId;
  }

  private setMeta(key: string, value: string): void {
    this.ctx.storage.sql.exec(
      `INSERT INTO room_meta (key, value) VALUES (?, ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
      key,
      value,
    );
  }

  private alarmMs(): number {
    return intVar(this.env.WS_ALARM_SECONDS, 30, 1, 300) * 1000;
  }

  private agentStaleMs(): number {
    return intVar(this.env.WS_AGENT_STALE_SECONDS, 60, 2, 3600) * 1000;
  }

  private phoneStaleMs(): number {
    return intVar(this.env.WS_PHONE_STALE_SECONDS, 90, 2, 3600) * 1000;
  }

  private probeStaleMs(): number {
    return intVar(this.env.WS_PROBE_STALE_SECONDS, 15, 1, 3600) * 1000;
  }

  private probeTimeoutMs(): number {
    return intVar(this.env.WS_PROBE_TIMEOUT_SECONDS, 3, 1, 60) * 1000;
  }

  private leaseSeconds(): number {
    const n = Number(this.env.TASK_LEASE_SECONDS || '1800');
    return Number.isFinite(n) ? Math.min(7200, Math.max(300, Math.trunc(n))) : 1800;
  }

  // ------------------------------------------------------------ entry

  async fetch(request: Request): Promise<Response> {
    try {
      return await this.handleFetch(request);
    } catch (error) {
      console.error('agent-room fetch failed', (error as Error)?.message || error);
      return json({ ok: false, error: { code: 'internal', message: '房间处理失败' } }, 500);
    }
  }

  private async handleFetch(request: Request): Promise<Response> {
    const secret = request.headers.get('x-internal-secret') || '';
    if (!this.env.INTERNAL_SECRET || secret !== this.env.INTERNAL_SECRET) {
      return json({ ok: false, error: { code: 'forbidden', message: '内部凭证无效' } }, 403);
    }
    const url = new URL(request.url);
    const agentId = this.bindAgentId(request.headers.get('x-agent-id'));
    const upgrade = (request.headers.get('upgrade') || '').toLowerCase();
    if (upgrade === 'websocket') {
      return this.handleUpgrade(request, agentId);
    }
    if (url.pathname === '/internal/presence') {
      return json({
        ok: true,
        data: {
          online: this.isAgentOnline(),
          last_seen_at: this.lastSeenIso(),
          realtime: this.realtime,
          ready: this.ready,
          issues: this.ready === null ? null : this.issues,
        },
      });
    }
    if (url.pathname === '/internal/event' && request.method === 'POST') {
      let body: InternalEventBody = {};
      try {
        const parsed: unknown = await request.json();
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          body = parsed as InternalEventBody;
        }
      } catch {
        return json({ ok: false, error: { code: 'bad_request', message: '事件格式错误' } }, 400);
      }
      await this.handleInternalEvent(body, agentId);
      return json({ ok: true });
    }
    return json({ ok: false, error: { code: 'not_found', message: '房间接口不存在' } }, 404);
  }

  // ------------------------------------------------------------ upgrade

  private async handleUpgrade(request: Request, agentId: string): Promise<Response> {
    const roleHeader = request.headers.get('x-role');
    if (roleHeader !== ROLE_AGENT && roleHeader !== ROLE_PHONE) {
      return json({ ok: false, error: { code: 'bad_request', message: '角色无效' } }, 400);
    }
    const role = roleHeader;
    const deviceId = (request.headers.get('x-device-id') || '').slice(0, 64);
    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    const attachment: SocketAttachment = {
      role,
      conn: crypto.randomUUID(),
      last_seen: Date.now(),
      ...(role === ROLE_PHONE ? { device_id: deviceId || undefined } : {}),
    };
    server.serializeAttachment(attachment);

    if (role === ROLE_AGENT) {
      for (const old of this.ctx.getWebSockets(ROLE_AGENT)) {
        const oldAttachment = old.deserializeAttachment() as SocketAttachment | null;
        if (oldAttachment) old.serializeAttachment({ ...oldAttachment, superseded: true });
        try {
          old.close(WS_CLOSE_REPLACED, 'replaced by newer agent connection');
        } catch {
          // 旧 socket 已关闭
        }
      }
      this.ctx.acceptWebSocket(server, [ROLE_AGENT]);
      this.online = true;
      this.realtime = true;
      this.agentLastSeen = Date.now();
      this.probeUnreachable = false;
      this.probePending = false;
      this.probeCloseOnTimeout = false;
      this.clearProbeTimer();
      this.ready = null;
      this.issues = [];
      // 连接跃迁：唯一允许的 room_meta 写入点之一（合并为 1 条 SQL）。
      this.persistMeta([
        ['online', '1'],
        ['last_seen_at', this.lastSeenIso() || isoNow()],
        ['realtime', '1'],
        ['ready', ''],
        ['issues', '[]'],
      ]);
      this.send(server, { type: 'hello', agent_id: agentId, server_time: isoNow() });
      this.ctx.waitUntil(this.pushPendingTasks(server));
      this.broadcastPhones(this.presenceMessage());
    } else {
      this.ctx.acceptWebSocket(server, [ROLE_PHONE]);
      this.sendToAgent({ type: 'phone_online', online: true });

      const agentSocket = this.isAgentSocketConnected();
      const needsProbe =
        agentSocket &&
        (!this.agentLastSeen || Date.now() - this.agentLastSeen > this.probeStaleMs());

      this.send(server, {
        type: 'hello',
        agent_id: agentId,
        online: agentSocket && !this.probeUnreachable,
        ready: this.ready,
        issues: this.ready === null ? null : this.issues,
        server_time: isoNow(),
      });

      if (!agentSocket) {
        // 没有 agent：直接告诉手机离线（alarm 仍要调度，用于手机心跳/清理）。
        this.send(server, this.presenceMessage());
      } else if (needsProbe) {
        this.beginProbe();
      } else {
        this.send(server, this.presenceMessage());
      }
      // 只有存在手机时才设置/刷新 alarm（v0.3 成本核心）。
      this.ctx.waitUntil(this.ensureAlarm());
    }

    return new Response(null, { status: 101, webSocket: client });
  }

  // ------------------------------------------------------------ 探活（手机连上时）

  private beginProbe(closeOnTimeout = false): void {
    if (this.probePending) {
      this.probeCloseOnTimeout = this.probeCloseOnTimeout || closeOnTimeout;
      return;
    }
    this.probePending = true;
    this.probeCloseOnTimeout = closeOnTimeout;
    const now = Date.now();
    this.sendToAgent({ type: 'probe', ts: now });
    this.sendToAgent({ type: 'ping', ts: now });
    this.startProbeTimer();
  }

  private startProbeTimer(): void {
    this.clearProbeTimer();
    this.probeTimer = setTimeout(() => {
      this.probeTimer = null;
      if (!this.probePending) return;
      this.probePending = false;
      if (!this.isAgentSocketConnected()) {
        this.probeCloseOnTimeout = false;
        return;
      }
      // 超时内没有任何 pong/probe_ack/其他 Agent 消息：标记 offline。
      this.probeUnreachable = true;
      if (this.probeCloseOnTimeout) {
        // alarm 场景（可能 hibernation 后内存 last_seen 丢失）：确认无响应才关闭，避免误杀活连接。
        this.probeCloseOnTimeout = false;
        for (const ws of this.ctx.getWebSockets(ROLE_AGENT)) {
          this.closeSocket(ws, WS_CLOSE_TIMEOUT, 'heartbeat timeout');
        }
        this.markAgentOffline();
      } else {
        // 手机连接场景：只标记 offline，不强行 close（Agent 可能只是忙/GC）。
        this.broadcastPhones(this.presenceMessage());
      }
    }, this.probeTimeoutMs());
  }

  private clearProbeTimer(): void {
    if (this.probeTimer !== null) {
      clearTimeout(this.probeTimer);
      this.probeTimer = null;
    }
  }

  /** 任意 Agent 消息都视为存活证据：刷新内存 last_seen，并结束/恢复探活状态。 */
  private noteAgentActivity(): void {
    this.agentLastSeen = Date.now();
    if (this.probePending || this.probeUnreachable) {
      this.probePending = false;
      this.probeUnreachable = false;
      this.probeCloseOnTimeout = false;
      this.clearProbeTimer();
      this.broadcastPhones(this.presenceMessage());
    }
  }

  // ------------------------------------------------------------ events

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    const run = this.processing
      .then(() => this.processSocketMessage(ws, message))
      .catch((error: unknown) => {
        console.warn('agent-room message failed', (error as Error)?.message || error);
      });
    this.processing = run;
    return run;
  }

  private async processSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    const attachment = ws.deserializeAttachment() as SocketAttachment | null;
    if (!attachment) {
      this.closeSocket(ws, 1008, 'missing attachment');
      return;
    }
    attachment.last_seen = Date.now();
    ws.serializeAttachment(attachment);

    const body = parseMessage(message);
    if (!body) {
      this.send(ws, { type: 'error', code: 'bad_json', message: '消息不是合法的 JSON 对象' });
      return;
    }
    const type = typeof body.type === 'string' ? body.type : '';

    if (attachment.role === ROLE_AGENT) {
      // 只更新内存 last_seen；禁止每条消息写 SQL（v0.3）。
      this.noteAgentActivity();
      await this.onAgentMessage(ws, type, body);
    } else {
      await this.onPhoneMessage(ws, type, body);
    }
  }

  async webSocketClose(ws: WebSocket, code: number, _reason: string, _wasClean: boolean): Promise<void> {
    const attachment = ws.deserializeAttachment() as SocketAttachment | null;
    try {
      ws.close(code, 'durable object closed');
    } catch {
      // already closed
    }
    if (!attachment) return;
    if (attachment.role === ROLE_AGENT) {
      if (attachment.superseded) return;
      if (!this.hasOtherSocket(ROLE_AGENT, attachment.conn)) this.markAgentOffline();
    } else if (!this.hasOtherSocket(ROLE_PHONE, attachment.conn)) {
      this.sendToAgent({ type: 'phone_online', online: false });
    }
    if (!this.ctx.getWebSockets(ROLE_PHONE).length) {
      this.clearProbeTimer();
      this.probePending = false;
      this.probeCloseOnTimeout = false;
      await this.deleteAlarm();
    }
  }

  async webSocketError(ws: WebSocket, error: unknown): Promise<void> {
    console.warn('agent-room websocket error', (error as Error)?.message || String(error));
    const attachment = ws.deserializeAttachment() as SocketAttachment | null;
    if (attachment && attachment.role === ROLE_AGENT && !attachment.superseded) {
      if (!this.hasOtherSocket(ROLE_AGENT, attachment.conn)) this.markAgentOffline();
    }
    this.closeSocket(ws, 1011, 'websocket error');
    if (!this.ctx.getWebSockets(ROLE_PHONE).length) {
      this.clearProbeTimer();
      this.probePending = false;
      this.probeCloseOnTimeout = false;
      await this.deleteAlarm();
    }
  }

  async alarm(): Promise<void> {
    const phones = this.ctx.getWebSockets(ROLE_PHONE);
    if (!phones.length) {
      // 没有手机不应该有 alarm：清掉，不主动 ping、不写 SQL。
      this.clearProbeTimer();
      this.probePending = false;
      await this.deleteAlarm();
      return;
    }

    const now = Date.now();
    const agents = this.ctx.getWebSockets(ROLE_AGENT);
    for (const ws of agents) this.send(ws, { type: 'ping', ts: now });
    for (const ws of phones) this.send(ws, { type: 'ping', ts: now });

    if (agents.length) {
      if (!this.agentLastSeen) this.agentLastSeen = now;
      if (now - this.agentLastSeen > this.agentStaleMs()) {
        // 先 ping+probe 观察 3s 再决定是否关闭：
        // DO hibernation 后内存 last_seen 会回落到上次跃迁值，直接 close 会误杀活连接。
        this.beginProbe(true);
      }
    }

    const phoneStale = this.phoneStaleMs();
    for (const ws of phones) {
      const attachment = ws.deserializeAttachment() as SocketAttachment | null;
      const lastSeen = attachment?.last_seen ?? 0;
      if (!lastSeen || now - lastSeen > phoneStale) {
        this.closeSocket(ws, WS_CLOSE_TIMEOUT, 'heartbeat timeout');
      }
    }

    if (this.ctx.getWebSockets(ROLE_PHONE).length) {
      await this.ensureAlarm(true);
    } else {
      await this.deleteAlarm();
    }
  }

  // ------------------------------------------------------------ agent messages

  private async onAgentMessage(ws: WebSocket, type: string, body: Record<string, unknown>): Promise<void> {
    switch (type) {
      case 'ping':
        // 兼容旧 Agent 的 JSON ping：照常回 pong。
        this.send(ws, { type: 'pong', ts: typeof body.ts === 'number' ? body.ts : Date.now() });
        return;
      case 'pong':
        return;
      case 'probe_ack':
        // probe_ack 可携带最新 readiness（可选，老客户端只回 ts）。
        if (body.ready !== undefined || body.issues !== undefined) {
          this.applyReady(body.ready, body.issues);
        }
        return;
      case 'agent_ready':
        this.applyReady(body.ready, body.issues);
        return;
      case 'task_update':
        await this.handleTaskUpdate(ws, body);
        return;
      case 'task_note':
        await this.handleTaskNote(ws, body);
        return;
      default:
        return;
    }
  }

  /** readiness 变化：写一次 room_meta + 向在线手机广播 presence。 */
  private applyReady(readyRaw: unknown, issuesRaw: unknown): void {
    const ready = typeof readyRaw === 'boolean' ? readyRaw : null;
    const issues = sanitizeIssues(issuesRaw);
    const same = ready === this.ready && JSON.stringify(issues) === JSON.stringify(this.issues);
    if (same) return;
    this.ready = ready;
    this.issues = issues;
    this.persistMeta([
      ['ready', ready === null ? '' : ready ? '1' : '0'],
      ['issues', JSON.stringify(issues)],
    ]);
    if (this.ctx.getWebSockets(ROLE_PHONE).length) {
      this.broadcastPhones(this.presenceMessage());
    }
  }

  private async handleTaskUpdate(ws: WebSocket, body: Record<string, unknown>): Promise<void> {
    const taskId = asTaskId(body.task_id);
    if (!taskId) {
      this.send(ws, { type: 'error', code: 'bad_request', message: 'task_id 无效' });
      return;
    }
    const agentId = this.requireAgentId();
    const current = await this.env.DB.prepare(
      'SELECT id, status, stage, progress, title FROM tasks WHERE id = ? AND agent_id = ?',
    )
      .bind(taskId, agentId)
      .first<{ id: string; status: string; stage: string; progress: number; title: string | null }>();
    if (!current) {
      this.send(ws, { type: 'error', code: 'task_not_found', message: '任务不存在' });
      return;
    }

    const rawStatus = typeof body.status === 'string' ? body.status : 'running';
    const status =
      rawStatus === 'completed' || rawStatus === 'succeeded'
        ? 'succeeded'
        : rawStatus === 'failed'
          ? 'failed'
          : 'running';
    const now = isoNow();
    const stage =
      typeof body.stage === 'string' && PROGRESS_STAGES.has(body.stage)
        ? body.stage
        : status === 'succeeded'
          ? 'done'
          : status === 'failed'
            ? 'failed'
            : current.stage || 'parsing';
    const progress = Math.min(
      100,
      Math.max(0, Math.trunc(typeof body.progress === 'number' ? body.progress : current.progress || 0)),
    );
    const title = cleanTitle(body.title);
    const message = shortText(body.message, 200);
    const lease = isoAfter(this.leaseSeconds());

    if (status === 'running') {
      if (current.status === 'succeeded' || current.status === 'failed') {
        this.send(ws, { type: 'error', code: 'invalid_state', message: '任务已经结束' });
        return;
      }
      await this.env.DB.prepare(
        `UPDATE tasks
            SET status = 'running', stage = ?, progress = ?, message = COALESCE(?, message),
                title = COALESCE(?, title), updated_at = ?, lease_expires_at = ?,
                claimed_at = COALESCE(claimed_at, ?),
                attempts = attempts + CASE WHEN status = 'queued' THEN 1 ELSE 0 END
          WHERE id = ? AND agent_id = ? AND status IN ('queued', 'running')`,
      )
        .bind(stage, Math.min(progress, 99), message, title, now, lease, now, taskId, agentId)
        .run();
    } else if (status === 'succeeded') {
      await this.env.DB.prepare(
        `UPDATE tasks
            SET status = 'succeeded', stage = 'done', progress = 100, message = '已完成',
                title = COALESCE(?, title), error_code = NULL, error_message = NULL,
                lease_expires_at = NULL, completed_at = COALESCE(completed_at, ?), updated_at = ?
          WHERE id = ? AND agent_id = ?`,
      )
        .bind(title, now, now, taskId, agentId)
        .run();
    } else {
      const errorMessage = shortText(body.error, 1000) || shortText(body.message, 1000) || '电脑处理失败';
      const errorCode = shortText(body.error_code, 64)?.replace(/[^A-Za-z0-9_-]/g, '_') || 'agent_error';
      await this.env.DB.prepare(
        `UPDATE tasks
            SET status = 'failed', stage = 'failed', progress = 0,
                error_code = ?, error_message = ?, title = COALESCE(?, title),
                lease_expires_at = NULL, completed_at = COALESCE(completed_at, ?), updated_at = ?
          WHERE id = ? AND agent_id = ?`,
      )
        .bind(errorCode, errorMessage, title, now, now, taskId, agentId)
        .run();
    }

    const summary = await this.loadSummary(taskId, agentId);
    if (summary) {
      this.broadcastPhones({ type: 'task_updated', task: summary, server_time: now });
    }
  }

  private async handleTaskNote(ws: WebSocket, body: Record<string, unknown>): Promise<void> {
    const taskId = asTaskId(body.task_id);
    if (!taskId) {
      this.send(ws, { type: 'error', code: 'bad_request', message: 'task_id 无效' });
      return;
    }
    const markdown = typeof body.markdown === 'string' ? body.markdown : '';
    if (!markdown.trim()) {
      this.send(ws, { type: 'error', code: 'bad_request', message: 'markdown 不能为空' });
      return;
    }
    if (bytesOf(markdown) > MAX_NOTE_BYTES) {
      this.send(ws, { type: 'error', code: 'note_too_large', message: '笔记超过 1.5 MiB' });
      return;
    }
    const agentId = this.requireAgentId();
    const row = await this.env.DB.prepare('SELECT id, status, title FROM tasks WHERE id = ? AND agent_id = ?')
      .bind(taskId, agentId)
      .first<{ id: string; status: string; title: string | null }>();
    if (!row) {
      this.send(ws, { type: 'error', code: 'task_not_found', message: '任务不存在' });
      return;
    }

    const now = isoNow();
    const title = cleanTitle(body.title) || row.title || '未命名笔记';
    let metadataChanged = false;
    if (row.status !== 'succeeded') {
      await this.env.DB.prepare(
        `UPDATE tasks
            SET status = 'succeeded', stage = 'done', progress = 100, message = '已完成',
                title = ?, error_code = NULL, error_message = NULL, lease_expires_at = NULL,
                completed_at = COALESCE(completed_at, ?), updated_at = ?
          WHERE id = ? AND agent_id = ?`,
      )
        .bind(title, now, now, taskId, agentId)
        .run();
      metadataChanged = true;
    } else if (!row.title) {
      await this.env.DB.prepare('UPDATE tasks SET title = ?, updated_at = ? WHERE id = ? AND agent_id = ?')
        .bind(title, now, taskId, agentId)
        .run();
      metadataChanged = true;
    }

    this.deliverNote(taskId, title, markdown);

    if (metadataChanged) {
      const summary = await this.loadSummary(taskId, agentId);
      if (summary) this.broadcastPhones({ type: 'task_updated', task: summary, server_time: now });
    }
  }

  // ------------------------------------------------------------ phone messages

  private async onPhoneMessage(ws: WebSocket, type: string, body: Record<string, unknown>): Promise<void> {
    switch (type) {
      case 'ping':
        this.send(ws, { type: 'pong', ts: typeof body.ts === 'number' ? body.ts : Date.now() });
        return;
      case 'pong':
        return;
      case 'note_request':
        await this.handleNoteRequest(ws, body);
        return;
      case 'ack_note': {
        const taskId = asTaskId(body.task_id);
        if (!taskId) return;
        // v0.3：区分"收到"与"已保存"；缺省 true 兼容旧客户端。
        const persisted = body.persisted !== false;
        if (persisted) {
          this.ctx.storage.sql.exec('DELETE FROM undelivered_notes WHERE task_id = ?', taskId);
        }
        this.sendToAgent({ type: 'ack_note', task_id: taskId, persisted });
        return;
      }
      default:
        return;
    }
  }

  private async handleNoteRequest(ws: WebSocket, body: Record<string, unknown>): Promise<void> {
    const taskId = asTaskId(body.task_id);
    if (!taskId) {
      this.send(ws, { type: 'error', code: 'bad_request', message: 'task_id 无效' });
      return;
    }
    const agentId = this.requireAgentId();
    const row = await this.env.DB.prepare('SELECT id FROM tasks WHERE id = ? AND agent_id = ?')
      .bind(taskId, agentId)
      .first<{ id: string }>();
    if (!row) {
      this.send(ws, { type: 'error', code: 'task_not_found', message: '任务不存在' });
      return;
    }
    if (this.isAgentOnline()) {
      this.sendToAgent({ type: 'note_request', task_id: taskId });
    } else {
      this.send(ws, { type: 'note_unavailable', task_id: taskId, reason: 'agent_offline' });
    }
  }

  // ------------------------------------------------------------ internal events

  private async handleInternalEvent(body: InternalEventBody, agentId: string): Promise<void> {
    const type = typeof body.type === 'string' ? body.type : '';
    switch (type) {
      case 'task_created': {
        const task = normalizePushTask(body.task);
        if (task && this.isAgentSocketConnected()) {
          this.sendToAgent({ type: 'task', task });
        }
        return;
      }
      case 'task_snapshot': {
        if (body.task && typeof body.task === 'object') {
          this.broadcastPhones({
            type: 'task_updated',
            task: body.task as TaskSummary,
            server_time: isoNow(),
          });
        }
        return;
      }
      case 'note_forward': {
        const taskId = asTaskId(body.task_id);
        const markdown = typeof body.markdown === 'string' ? body.markdown : '';
        if (!taskId || !markdown.trim() || bytesOf(markdown) > MAX_NOTE_BYTES) return;
        const row = await this.env.DB.prepare('SELECT title FROM tasks WHERE id = ? AND agent_id = ?')
          .bind(taskId, agentId)
          .first<{ title: string | null }>();
        if (!row) return;
        this.deliverNote(taskId, cleanTitle(body.title) || row.title || '未命名笔记', markdown);
        return;
      }
      case 'device_revoked': {
        const deviceId = typeof body.device_id === 'string' ? body.device_id : '';
        if (!deviceId) return;
        for (const ws of this.ctx.getWebSockets(ROLE_PHONE)) {
          const attachment = ws.deserializeAttachment() as SocketAttachment | null;
          if (attachment?.device_id === deviceId) {
            this.closeSocket(ws, 4003, 'device revoked');
          }
        }
        return;
      }
      default:
        return;
    }
  }

  // ------------------------------------------------------------ helpers

  private async pushPendingTasks(ws: WebSocket): Promise<void> {
    try {
      const agentId = this.requireAgentId();
      const result = await this.env.DB.prepare(
        `SELECT id, url, platform, status, stage, progress, title, created_at
           FROM tasks
          WHERE agent_id = ? AND status IN ('queued', 'running')
          ORDER BY created_at ASC LIMIT ?`,
      )
        .bind(agentId, MAX_PENDING_TASKS_ON_CONNECT)
        .all<Record<string, unknown>>();
      for (const row of result.results || []) {
        const task = normalizePushTask(row);
        if (task) this.send(ws, { type: 'task', task });
      }
    } catch (error) {
      console.warn('agent-room pending tasks failed', (error as Error)?.message || error);
    }
  }

  private async loadSummary(taskId: string, agentId: string): Promise<TaskSummary | null> {
    const row = await this.env.DB.prepare(
      `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE id = ? AND agent_id = ?`,
    )
      .bind(taskId, agentId)
      .first<Record<string, unknown>>();
    return row ? toTaskSummary(row) : null;
  }

  /** 正文只转发给在线手机；没有手机时只记 task_id，绝不把正文写进 DO SQLite。 */
  private deliverNote(taskId: string, title: string, markdown: string): void {
    const phones = this.ctx.getWebSockets(ROLE_PHONE);
    if (phones.length) {
      for (const ws of phones) {
        this.send(ws, { type: 'note', task_id: taskId, markdown, title, server_time: isoNow() });
      }
      this.ctx.storage.sql.exec('DELETE FROM undelivered_notes WHERE task_id = ?', taskId);
      return;
    }
    this.ctx.storage.sql.exec(
      `INSERT INTO undelivered_notes (task_id, received_at) VALUES (?, ?)
       ON CONFLICT(task_id) DO UPDATE SET received_at = excluded.received_at`,
      taskId,
      isoNow(),
    );
  }

  private isAgentSocketConnected(): boolean {
    return this.ctx.getWebSockets(ROLE_AGENT).length > 0;
  }

  /** presence 口径：socket 存在且最近一次探活未被判定不可达。 */
  private isAgentOnline(): boolean {
    return this.isAgentSocketConnected() && !this.probeUnreachable;
  }

  private lastSeenIso(): string | null {
    return this.agentLastSeen ? new Date(this.agentLastSeen).toISOString() : null;
  }

  private presenceMessage(onlineOverride?: boolean): Record<string, unknown> {
    return {
      type: 'presence',
      online: onlineOverride === undefined ? this.isAgentOnline() : onlineOverride,
      last_seen: this.lastSeenIso(),
      ready: this.ready,
      issues: this.ready === null ? null : this.issues,
      server_time: isoNow(),
    };
  }

  private hasOtherSocket(role: string, conn: string): boolean {
    return this.ctx.getWebSockets(role).some((ws) => {
      const attachment = ws.deserializeAttachment() as SocketAttachment | null;
      return attachment?.conn !== conn;
    });
  }

  private markAgentOffline(): void {
    this.clearProbeTimer();
    this.probePending = false;
    this.probeUnreachable = false;
    this.probeCloseOnTimeout = false;
    if (!this.online) return;
    this.online = false;
    this.persistMeta([
      ['online', '0'],
      ['last_seen_at', this.lastSeenIso() || isoNow()],
    ]);
    this.broadcastPhones(this.presenceMessage(false));
  }

  private send(ws: WebSocket, payload: Record<string, unknown>): void {
    try {
      ws.send(JSON.stringify(payload));
    } catch {
      // socket 已关闭：交给 close/error 回调收尾
    }
  }

  private sendToAgent(payload: Record<string, unknown>): void {
    for (const ws of this.ctx.getWebSockets(ROLE_AGENT)) this.send(ws, payload);
  }

  private broadcastPhones(payload: Record<string, unknown>): void {
    for (const ws of this.ctx.getWebSockets(ROLE_PHONE)) this.send(ws, payload);
  }

  private closeSocket(ws: WebSocket, code: number, reason: string): void {
    try {
      ws.close(code, reason);
    } catch {
      // already closed
    }
  }

  private async ensureAlarm(force = false): Promise<void> {
    try {
      const existing = await this.ctx.storage.getAlarm();
      if (existing == null || force) {
        await this.ctx.storage.setAlarm(Date.now() + this.alarmMs());
      }
    } catch (error) {
      console.warn('agent-room alarm scheduling failed', (error as Error)?.message || error);
    }
  }

  private async deleteAlarm(): Promise<void> {
    try {
      await this.ctx.storage.deleteAlarm();
    } catch {
      // ignore
    }
  }
}

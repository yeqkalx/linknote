/**
 * 链记 LinkNote — 待发送队列（离线提交 + 自动重试）
 *
 * 契约 §6.1：同一设备相同 client_key 重复提交会返回已有任务，
 * 所以「先入队、再发送、断网自动重试」是幂等的，不会重复生成笔记。
 */
import { QUEUE_RETRY_MS } from './config.js';
import { api, isAuthError, isNetworkError } from './api.js';
import { outbox, settings, taskCache } from './store.js';
import { uuid } from './util.js';

const bus = new EventTarget();
let flushingPromise = null;
let stopAuto = null;

function emit(type, detail) {
  try {
    bus.dispatchEvent(new CustomEvent(type, { detail }));
  } catch (error) {
    // 老浏览器没有 CustomEvent 构造器时忽略（现代浏览器都支持）
  }
}

/** 订阅队列事件：change / sent / failed / auth-error */
export function onQueueEvent(type, handler) {
  if (typeof handler !== 'function') return () => {};
  const listener = (event) => handler(event.detail);
  bus.addEventListener(type, listener);
  return () => bus.removeEventListener(type, listener);
}

export function pendingItems() {
  return outbox.read(settings.getApiBase());
}

export function pendingCount() {
  return pendingItems().length;
}

/** 入队一条待发送内容；返回队列项 */
export function enqueueUrl(url, { clientKey } = {}) {
  const base = settings.getApiBase();
  if (!base) return null;
  const item = outbox.add(base, {
    clientKey: clientKey || uuid(),
    url,
    createdAt: new Date().toISOString(),
    attempts: 0,
    lastAttemptAt: null,
    lastError: '',
    sending: false,
    permanent: false,
  });
  emit('change', { pending: pendingCount() });
  return item;
}

const PERMANENT_CODES = new Set([
  'invalid_url',
  'unsupported_platform',
  'bad_request',
  'origin_not_allowed',
  'forbidden',
  'markdown_too_large',
  'pairing_not_found',
]);
const PERMANENT_STATUSES = new Set([400, 403, 404, 413, 422]);

/**
 * 发送队列里的待发内容。
 * 网络错误会停止本轮（等下次联网）；参数错误标记为永久失败，等用户处理。
 */
export function flushQueue() {
  if (flushingPromise) return flushingPromise;

  flushingPromise = (async () => {
    const base = settings.getApiBase();
    if (!base) return { sent: 0, pending: 0 };
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      emit('change', { pending: pendingCount(), offline: true });
      return { sent: 0, pending: pendingCount(), offline: true };
    }

    const snapshot = outbox.read(base);
    if (!snapshot.length) return { sent: 0, pending: 0 };

    let sent = 0;
    let stopped = false;

    for (const item of snapshot) {
      if (stopped) break;
      const current = outbox.read(base).find((entry) => entry.clientKey === item.clientKey);
      if (!current || current.permanent) continue;

      outbox.update(base, current.clientKey, {
        sending: true,
        attempts: (Number(current.attempts) || 0) + 1,
        lastAttemptAt: new Date().toISOString(),
      });
      emit('change', { pending: pendingCount() });

      try {
        const data = await api.createTask({
          url: current.url,
          clientKey: current.clientKey,
          base,
          timeout: 20000,
        });
        const task = data && data.task ? data.task : null;
        outbox.remove(base, current.clientKey);
        if (task) taskCache.upsert(base, task);
        sent += 1;
        emit('sent', { task, url: current.url, clientKey: current.clientKey });
      } catch (error) {
        if (isAuthError(error)) {
          outbox.update(base, current.clientKey, { sending: false, lastError: error.message });
          emit('auth-error', { error });
          emit('change', { pending: pendingCount() });
          stopped = true;
          break;
        }
        if (isNetworkError(error)) {
          outbox.update(base, current.clientKey, { sending: false, lastError: '等待网络恢复' });
          emit('change', { pending: pendingCount() });
          stopped = true;
          break;
        }

        const permanent =
          PERMANENT_STATUSES.has(error.status) || PERMANENT_CODES.has(error.code || '');
        outbox.update(base, current.clientKey, {
          sending: false,
          lastError: error.message || '发送失败',
          permanent,
        });
        emit('failed', { item: current, error, permanent });
        emit('change', { pending: pendingCount() });
        if (!permanent) {
          // 服务端 5xx 之类只试一次，下个周期再试。
          stopped = true;
        }
      }
    }

    emit('change', { pending: pendingCount() });
    return { sent, pending: pendingCount() };
  })().finally(() => {
    flushingPromise = null;
  });

  return flushingPromise;
}

/** 用户手动重试某一条永久失败的队列项 */
export function retryQueued(clientKey) {
  const base = settings.getApiBase();
  if (!base || !clientKey) return;
  outbox.update(base, clientKey, { permanent: false, attempts: 0, lastError: '', sending: false });
  emit('change', { pending: pendingCount() });
  void flushQueue();
}

export function discardQueued(clientKey) {
  const base = settings.getApiBase();
  if (!base || !clientKey) return;
  outbox.remove(base, clientKey);
  emit('change', { pending: pendingCount() });
}

/** 启动自动重试：启动时、联网时、回到前台时、每隔 QUEUE_RETRY_MS */
export function startQueue() {
  if (stopAuto) return stopAuto;

  const flushIfVisible = () => {
    if (typeof document === 'undefined' || document.visibilityState === 'visible') {
      void flushQueue();
    }
  };

  const onOnline = () => {
    emit('change', { pending: pendingCount(), online: true });
    void flushQueue();
  };
  const onVisible = () => {
    if (document.visibilityState === 'visible') void flushQueue();
  };

  const timer = setInterval(() => {
    if (pendingCount() > 0) flushIfVisible();
  }, QUEUE_RETRY_MS);

  window.addEventListener('online', onOnline);
  document.addEventListener('visibilitychange', onVisible);

  stopAuto = () => {
    clearInterval(timer);
    window.removeEventListener('online', onOnline);
    document.removeEventListener('visibilitychange', onVisible);
    stopAuto = null;
  };

  // 启动即尝试发送（等待 app 完成路由初始化后入队更稳妥，这里延后一拍）
  setTimeout(() => {
    void flushQueue();
  }, 1200);

  return stopAuto;
}

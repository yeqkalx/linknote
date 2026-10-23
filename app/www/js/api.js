/**
 * 链记 LinkNote — Relay API 客户端
 *
 * 唯一契约来源：docs/api-contract.md
 * - 所有请求统一 /api/v1 前缀
 * - 统一响应包 { ok, data } / { ok, error: { code, message } }
 * - Bearer token 从 store 里按 Relay 地址读取
 * - 401 会清掉当前 Relay 的 token，并通知全局处理器跳回配对页
 */
import { API_PREFIX } from './config.js';
import { auth, settings } from './store.js';
import { devicePlatform, normalizeApiBase } from './util.js';

export class ApiError extends Error {
  constructor({ code = 'error', message = '请求失败', status = 0, base = '', path = '', cause = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.base = base;
    this.path = path;
    if (cause) this.cause = cause;
  }
}

export function isNetworkError(error) {
  return error instanceof ApiError && (error.code === 'network' || error.code === 'timeout');
}

export function isTimeoutError(error) {
  return error instanceof ApiError && error.code === 'timeout';
}

export function isAuthError(error) {
  return error instanceof ApiError && (error.status === 401 || error.code === 'unauthorized');
}

const HTTP_FALLBACK_MESSAGES = {
  400: '请求参数有误',
  401: '登录状态已失效，请重新配对',
  403: '当前来源不被 Relay 允许',
  404: '内容不存在或已被移除',
  409: '任务状态有变化，请刷新后重试',
  413: '笔记内容过大，无法读取',
  429: '操作太频繁，请稍后再试',
  500: 'Relay 出了点问题，请稍后再试',
  502: 'Relay 暂时不可用',
  503: 'Relay 暂时不可用',
  504: 'Relay 响应超时',
};

let unauthorizedHandler = null;

/** 401 时由 app.js 注册全局处理（清 token + 跳配对页） */
export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === 'function' ? handler : null;
}

function resolveBase(override) {
  if (override === undefined) return settings.getApiBase();
  const normalized = normalizeApiBase(override);
  return normalized || '';
}

function buildUrl(base, path) {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${base}${API_PREFIX}${suffix}`;
}

/**
 * 底层请求。
 * @param {string} path 形如 '/tasks?limit=20'
 * @param {{ method?: string, body?: any, base?: string, signal?: AbortSignal, timeout?: number }} options
 */
async function request(path, { method = 'GET', body = null, base, signal, timeout = 15000 } = {}) {
  const resolvedBase = resolveBase(base);
  if (!resolvedBase) {
    throw new ApiError({
      code: 'no_relay',
      message: '还没有设置 Relay 地址，请到设置页填写',
    });
  }

  const url = buildUrl(resolvedBase, path);
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeout);
  let abortedByTimeout = false;
  let externalAbortHandler = null;

  if (signal) {
    if (signal.aborted) {
      clearTimeout(timer);
      throw new ApiError({ code: 'aborted', message: '请求已取消' });
    }
    externalAbortHandler = () => {
      controller.abort();
    };
    signal.addEventListener('abort', externalAbortHandler, { once: true });
  }

  const headers = { Accept: 'application/json' };
  const authEntry = auth.get(resolvedBase);
  if (authEntry && authEntry.token) headers.Authorization = `Bearer ${authEntry.token}`;

  const init = {
    method,
    headers,
    mode: 'cors',
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'follow',
    signal: controller.signal,
  };
  if (body !== null && body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    clearTimeout(timer);
    if (externalAbortHandler) signal.removeEventListener('abort', externalAbortHandler);
    if (signal && signal.aborted) throw new ApiError({ code: 'aborted', message: '请求已取消' });
    if (error && error.name === 'AbortError') {
      abortedByTimeout = true;
      throw new ApiError({
        code: 'timeout',
        message: '请求超时，请检查网络后重试',
        base: resolvedBase,
        path,
        cause: error,
      });
    }
    throw new ApiError({
      code: 'network',
      message: `无法连接 Relay（${resolvedBase}）`,
      base: resolvedBase,
      path,
      cause: error,
    });
  } finally {
    clearTimeout(timer);
    if (externalAbortHandler) signal.removeEventListener('abort', externalAbortHandler);
  }

  if (externalAbortHandler) signal.removeEventListener('abort', externalAbortHandler);

  let payload = null;
  let parseFailed = false;
  let text = '';
  try {
    text = await response.text();
  } catch (error) {
    if (abortedByTimeout) {
      throw new ApiError({ code: 'timeout', message: '请求超时，请检查网络后重试', base: resolvedBase, path });
    }
    throw new ApiError({ code: 'network', message: '读取 Relay 响应失败', base: resolvedBase, path, cause: error });
  }
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      parseFailed = true;
    }
  }

  const envelopeFailed = payload && typeof payload === 'object' && payload.ok === false;
  if (!response.ok || envelopeFailed) {
    const errorBody = payload && typeof payload === 'object' ? payload.error : null;
    let code =
      (errorBody && typeof errorBody.code === 'string' && errorBody.code) || `http_${response.status}`;
    let message =
      (errorBody && typeof errorBody.message === 'string' && errorBody.message) ||
      HTTP_FALLBACK_MESSAGES[response.status] ||
      `请求失败（HTTP ${response.status}）`;

    if (response.status === 403 && code === 'origin_not_allowed') {
      message = '当前页面来源不在 Relay 白名单（ALLOWED_ORIGINS）内，请检查 Relay 配置';
    }

    const apiError = new ApiError({ code, message, status: response.status, base: resolvedBase, path });
    if (response.status === 401) {
      auth.clear(resolvedBase);
      if (unauthorizedHandler) unauthorizedHandler(apiError);
    }
    throw apiError;
  }

  if (parseFailed) {
    throw new ApiError({
      code: 'bad_response',
      message: 'Relay 返回了无法识别的内容',
      status: response.status,
      base: resolvedBase,
      path,
    });
  }
  if (!payload || typeof payload !== 'object') return null;
  return payload.data !== undefined ? payload.data : null;
}

/* ------------------------------------------------------------------ */
/* 契约接口                                                            */
/* ------------------------------------------------------------------ */

export const api = {
  /** §2 健康检查（公开） */
  health({ base, signal, timeout = 8000 } = {}) {
    return request('/health', { base, signal, timeout });
  },

  /** §3.3 领取配对码 */
  claimPairing({ code, deviceName, platform = devicePlatform(), base, signal, timeout = 15000 } = {}) {
    return request('/pairings/claim', {
      method: 'POST',
      body: { code, device_name: deviceName, platform },
      base,
      signal,
      timeout,
    });
  },

  /** §5.1 当前设备信息 */
  me({ base, signal, timeout = 10000 } = {}) {
    return request('/me', { base, signal, timeout });
  },

  /** §5.2 修改设备名 */
  renameDevice(deviceName, { base, signal, timeout = 10000 } = {}) {
    return request('/me', {
      method: 'PATCH',
      body: { device_name: deviceName },
      base,
      signal,
      timeout,
    });
  },

  /** §5.3 解除配对 */
  unpair({ base, signal, timeout = 10000 } = {}) {
    return request('/me', { method: 'DELETE', base, signal, timeout });
  },

  /** §6.1 提交任务（client_key 用于断网重试幂等） */
  createTask({ url, clientKey = null, base, signal, timeout = 20000 } = {}) {
    return request('/tasks', {
      method: 'POST',
      body: { url, client_key: clientKey },
      base,
      signal,
      timeout,
    });
  },

  /** §6.2 任务列表 */
  listTasks({ limit = 20, before = null, base, signal, timeout = 12000 } = {}) {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (before) params.set('before', before);
    return request(`/tasks?${params.toString()}`, { base, signal, timeout });
  },

  /** §6.3 任务详情（含 Agent 在线状态） */
  getTask(taskId, { base, signal, timeout = 12000 } = {}) {
    return request(`/tasks/${encodeURIComponent(taskId)}`, { base, signal, timeout });
  },

  /** §6.4 获取 Markdown（仅 succeeded） */
  getNote(taskId, { base, signal, timeout = 30000 } = {}) {
    return request(`/tasks/${encodeURIComponent(taskId)}/note`, { base, signal, timeout });
  },

  /** §6.5 失败重试：创建新任务 */
  retryTask(taskId, { base, signal, timeout = 15000 } = {}) {
    return request(`/tasks/${encodeURIComponent(taskId)}/retry`, {
      method: 'POST',
      base,
      signal,
      timeout,
    });
  },

  /** §6.6 从列表移除（归档） */
  removeTask(taskId, { base, signal, timeout = 12000 } = {}) {
    return request(`/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
      base,
      signal,
      timeout,
    });
  },
};

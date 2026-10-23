/**
 * 链记 LinkNote — 本地状态与缓存
 *
 * 设计要点：
 * - token 与配对信息按 Relay 地址分开保存：换 Relay 不会串号。
 * - 任务列表 / 笔记 / 待发送队列都按 Relay 地址分桶缓存。
 * - localStorage 不可用（隐私模式等）时退化为内存存储，保证页面还能用。
 */
import {
  DEFAULT_DEVICE_NAME,
  MAX_CACHED_NOTES,
  MAX_CACHED_TASKS,
  NATIVE_DEFAULT_RELAY,
  NOTE_CACHE_BUDGET,
  STORAGE_KEYS,
} from './config.js';
import { isNativePlatform, normalizeApiBase, safeJsonParse } from './util.js';

/* ------------------------------------------------------------------ */
/* 基础读写                                                            */
/* ------------------------------------------------------------------ */

const memoryStore = new Map();

function readRaw(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return memoryStore.has(key) ? memoryStore.get(key) : null;
  }
}

function writeRaw(key, value) {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch (error) {
    memoryStore.set(key, value);
    return false;
  }
}

function removeRaw(key) {
  try {
    window.localStorage.removeItem(key);
  } catch (error) {
    // 忽略
  }
  memoryStore.delete(key);
}

function readJson(key, fallback) {
  const raw = readRaw(key);
  if (!raw) return fallback;
  const parsed = safeJsonParse(raw, undefined);
  return parsed === undefined ? fallback : parsed;
}

function writeJson(key, value) {
  try {
    return writeRaw(key, JSON.stringify(value));
  } catch (error) {
    return false;
  }
}

const bucketKeys = {
  tasks: (base) => `${STORAGE_KEYS.settings}.tasks.${base}`,
  notes: (base) => `${STORAGE_KEYS.settings}.notes.${base}`,
  outbox: (base) => `${STORAGE_KEYS.settings}.outbox.${base}`,
};

/* ------------------------------------------------------------------ */
/* 设置（Relay 地址、设备名）                                           */
/* ------------------------------------------------------------------ */

const DEFAULT_SETTINGS = { apiBase: '', deviceName: '' };

export function getSettings() {
  const value = readJson(STORAGE_KEYS.settings, {});
  return { ...DEFAULT_SETTINGS, ...(value && typeof value === 'object' ? value : {}) };
}

export function updateSettings(patch) {
  const next = { ...getSettings(), ...(patch || {}) };
  writeJson(STORAGE_KEYS.settings, next);
  return next;
}

/**
 * 默认 Relay：
 * - 原生 App（Capacitor）：优先用设置里保存的地址（在 getApiBase 里已处理），否则用线上地址。
 * - 普通浏览器：与 PWA 同源（Worker 同时托管 PWA）。
 */
export function getDefaultApiBase() {
  try {
    const injected = window.__LINKNOTE_API_BASE__;
    if (typeof injected === 'string' && injected.trim()) {
      const normalized = normalizeApiBase(injected);
      if (normalized) return normalized;
    }
  } catch (error) {
    // 忽略
  }
  if (isNativePlatform()) return NATIVE_DEFAULT_RELAY;
  if (typeof location !== 'undefined' && /^https?:$/.test(location.protocol)) {
    return location.origin;
  }
  return '';
}

function getApiBaseOverride() {
  const normalized = normalizeApiBase(getSettings().apiBase || '');
  return normalized || '';
}

function getApiBase() {
  return getApiBaseOverride() || getDefaultApiBase();
}

/** @returns {{ ok: boolean, base?: string, reason?: string }} */
function setApiBase(value) {
  const normalized = normalizeApiBase(value ?? '');
  if (normalized === null) return { ok: false, reason: 'invalid' };
  updateSettings({ apiBase: normalized });
  return { ok: true, base: normalized || getDefaultApiBase() };
}

function resetApiBase() {
  updateSettings({ apiBase: '' });
  return getDefaultApiBase();
}

function getDeviceName() {
  const name = String(getSettings().deviceName || '').trim();
  return name || DEFAULT_DEVICE_NAME;
}

function setDeviceName(value) {
  const name = String(value ?? '').trim().slice(0, 40);
  updateSettings({ deviceName: name });
  return name || DEFAULT_DEVICE_NAME;
}

export const settings = {
  get: getSettings,
  update: updateSettings,
  getApiBase,
  getApiBaseOverride,
  getDefaultApiBase,
  setApiBase,
  resetApiBase,
  getDeviceName,
  setDeviceName,
};

/* ------------------------------------------------------------------ */
/* 配对 / 认证（按 Relay 地址分桶）                                     */
/* ------------------------------------------------------------------ */

function normalizeDevice(device) {
  if (!device || typeof device !== 'object') return null;
  const id = typeof device.id === 'string' ? device.id : '';
  if (!id) return null;
  return {
    id,
    name: typeof device.name === 'string' ? device.name : '',
    platform: typeof device.platform === 'string' ? device.platform : '',
    paired_at: device.paired_at || device.pairedAt || null,
  };
}

function normalizeAgent(agent) {
  if (!agent || typeof agent !== 'object') return null;
  const id = typeof agent.id === 'string' ? agent.id : '';
  return {
    id,
    name: typeof agent.name === 'string' ? agent.name : '',
    platform: typeof agent.platform === 'string' ? agent.platform : '',
    online: agent.online === true,
    last_seen_at: agent.last_seen_at || null,
  };
}

export function readAuthMap() {
  const value = readJson(STORAGE_KEYS.auth, {});
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

export function getAuth(base) {
  if (!base) return null;
  const entry = readAuthMap()[base];
  if (!entry || typeof entry.token !== 'string' || !entry.token) return null;
  return entry;
}

export function saveAuth(base, { token, device, agent } = {}) {
  if (!base || typeof token !== 'string' || !token) return null;
  const map = readAuthMap();
  const previous = map[base] || {};
  const entry = {
    token,
    device: normalizeDevice(device) || previous.device || null,
    agent: normalizeAgent(agent) || previous.agent || null,
    pairedAt: new Date().toISOString(),
  };
  map[base] = entry;
  writeJson(STORAGE_KEYS.auth, map);
  return entry;
}

export function updateAuth(base, patch = {}) {
  if (!base) return null;
  const map = readAuthMap();
  const entry = map[base];
  if (!entry) return null;
  if (patch.device) {
    const device = normalizeDevice(patch.device);
    if (device) entry.device = { ...(entry.device || {}), ...device };
  }
  if (patch.agent) {
    const agent = normalizeAgent(patch.agent);
    if (agent) entry.agent = { ...(entry.agent || {}), ...agent };
  }
  if (typeof patch.token === 'string' && patch.token) entry.token = patch.token;
  map[base] = entry;
  writeJson(STORAGE_KEYS.auth, map);
  return entry;
}

export function clearAuth(base) {
  if (!base) return;
  const map = readAuthMap();
  if (!(base in map)) return;
  delete map[base];
  writeJson(STORAGE_KEYS.auth, map);
}

export function hasAuth(base) {
  return Boolean(getAuth(base));
}

export const auth = {
  map: readAuthMap,
  get: getAuth,
  save: saveAuth,
  update: updateAuth,
  clear: clearAuth,
  has: hasAuth,
};

/* ------------------------------------------------------------------ */
/* 最近任务缓存                                                        */
/* ------------------------------------------------------------------ */

function sortTasks(items) {
  return items
    .slice()
    .sort((a, b) => (Date.parse(b && b.created_at) || 0) - (Date.parse(a && a.created_at) || 0));
}

function normalizeTaskList(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const items = [];
  for (const item of value) {
    if (!item || typeof item.id !== 'string' || !item.id || seen.has(item.id)) continue;
    seen.add(item.id);
    items.push(item);
  }
  return sortTasks(items).slice(0, MAX_CACHED_TASKS);
}

export function readTaskCache(base) {
  if (!base) return { items: [], fetchedAt: 0 };
  const value = readJson(bucketKeys.tasks(base), { items: [], fetchedAt: 0 });
  const items = normalizeTaskList(value && value.items);
  const fetchedAt = Number(value && value.fetchedAt) || 0;
  return { items, fetchedAt };
}

export function writeTaskCache(base, items, fetchedAt = Date.now()) {
  if (!base) return;
  writeJson(bucketKeys.tasks(base), { items: normalizeTaskList(items), fetchedAt });
}

export function mergeTaskCache(base, incoming, { replace = false } = {}) {
  if (!base) return { items: [], fetchedAt: 0 };
  const current = readTaskCache(base);
  const source = replace ? incoming : [...(incoming || []), ...current.items];
  const merged = normalizeTaskList(source);
  const fetchedAt = Date.now();
  writeTaskCache(base, merged, fetchedAt);
  return { items: merged, fetchedAt };
}

export function upsertTaskCache(base, task) {
  if (!base || !task || typeof task.id !== 'string' || !task.id) return null;
  const { items } = readTaskCache(base);
  const index = items.findIndex((item) => item.id === task.id);
  if (index >= 0) items[index] = { ...items[index], ...task };
  else items.unshift(task);
  writeTaskCache(base, items);
  return task;
}

export function removeTaskCache(base, taskId) {
  if (!base || !taskId) return;
  const { items } = readTaskCache(base);
  writeTaskCache(
    base,
    items.filter((item) => item.id !== taskId),
  );
}

export function findTaskCache(base, taskId) {
  return readTaskCache(base).items.find((item) => item.id === taskId) || null;
}

export function clearTaskCache(base) {
  if (!base) return;
  removeRaw(bucketKeys.tasks(base));
}

export const taskCache = {
  read: readTaskCache,
  write: writeTaskCache,
  merge: mergeTaskCache,
  upsert: upsertTaskCache,
  remove: removeTaskCache,
  find: findTaskCache,
  clear: clearTaskCache,
};

/* ------------------------------------------------------------------ */
/* 笔记缓存（离线阅读）                                                */
/* ------------------------------------------------------------------ */

function readNoteMap(base) {
  if (!base) return {};
  const value = readJson(bucketKeys.notes(base), {});
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

export function readNoteCache(base, taskId) {
  if (!base || !taskId) return null;
  const entry = readNoteMap(base)[taskId];
  if (!entry || typeof entry.markdown !== 'string') return null;
  return entry;
}

export function writeNoteCache(base, taskId, note) {
  if (!base || !taskId || !note || typeof note.markdown !== 'string') return false;
  // 单篇超过总预算就不缓存；仍然可以正常在线阅读。
  if (note.markdown.length > NOTE_CACHE_BUDGET) return false;

  const map = readNoteMap(base);
  map[taskId] = {
    title: typeof note.title === 'string' ? note.title : '',
    markdown: note.markdown,
    completed_at: note.completed_at || null,
    cachedAt: Date.now(),
  };

  const entries = Object.entries(map).sort(
    (a, b) => (Number(b[1] && b[1].cachedAt) || 0) - (Number(a[1] && a[1].cachedAt) || 0),
  );

  let total = 0;
  const kept = {};
  for (const [id, entry] of entries.slice(0, MAX_CACHED_NOTES)) {
    const length = typeof entry.markdown === 'string' ? entry.markdown.length : 0;
    if (total + length > NOTE_CACHE_BUDGET) break;
    total += length;
    kept[id] = entry;
  }

  return writeJson(bucketKeys.notes(base), kept);
}

export function removeNoteCache(base, taskId) {
  if (!base || !taskId) return;
  const map = readNoteMap(base);
  if (!(taskId in map)) return;
  delete map[taskId];
  writeJson(bucketKeys.notes(base), map);
}

export function noteCacheStats(base) {
  const map = readNoteMap(base);
  const ids = Object.keys(map);
  let chars = 0;
  for (const id of ids) {
    const markdown = map[id] && map[id].markdown;
    chars += typeof markdown === 'string' ? markdown.length : 0;
  }
  return { count: ids.length, chars };
}

export function clearNoteCache(base) {
  if (!base) return;
  removeRaw(bucketKeys.notes(base));
}

export const noteCache = {
  read: readNoteCache,
  write: writeNoteCache,
  remove: removeNoteCache,
  stats: noteCacheStats,
  clear: clearNoteCache,
};

/* ------------------------------------------------------------------ */
/* 待发送队列（断网重试，client_key 幂等）                              */
/* ------------------------------------------------------------------ */

const OUTBOX_LIMIT = 20;
const OUTBOX_URL_LIMIT = 8000;

function normalizeOutboxItem(item) {
  if (!item || typeof item.url !== 'string' || !item.url.trim()) return null;
  return {
    clientKey:
      typeof item.clientKey === 'string' && item.clientKey
        ? item.clientKey
        : `legacy-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    url: item.url.trim().slice(0, OUTBOX_URL_LIMIT),
    createdAt: item.createdAt || new Date().toISOString(),
    attempts: Number(item.attempts) || 0,
    lastAttemptAt: item.lastAttemptAt || null,
    lastError: typeof item.lastError === 'string' ? item.lastError : '',
    sending: Boolean(item.sending),
    permanent: Boolean(item.permanent),
  };
}

export function readOutbox(base) {
  if (!base) return [];
  const value = readJson(bucketKeys.outbox(base), []);
  if (!Array.isArray(value)) return [];
  return value.map(normalizeOutboxItem).filter(Boolean).slice(0, OUTBOX_LIMIT);
}

function writeOutbox(base, items) {
  if (!base) return;
  writeJson(bucketKeys.outbox(base), items.slice(0, OUTBOX_LIMIT));
}

export function addOutbox(base, item) {
  if (!base) return null;
  const normalized = normalizeOutboxItem(item);
  if (!normalized) return null;
  const items = readOutbox(base);
  const deduped = items.filter((entry) => entry.clientKey !== normalized.clientKey);
  deduped.unshift(normalized);
  writeOutbox(base, deduped);
  return normalized;
}

export function updateOutbox(base, clientKey, patch) {
  if (!base || !clientKey) return null;
  const items = readOutbox(base);
  const index = items.findIndex((entry) => entry.clientKey === clientKey);
  if (index < 0) return null;
  items[index] = normalizeOutboxItem({ ...items[index], ...patch }) || items[index];
  writeOutbox(base, items);
  return items[index];
}

export function removeOutbox(base, clientKey) {
  if (!base || !clientKey) return;
  writeOutbox(
    base,
    readOutbox(base).filter((entry) => entry.clientKey !== clientKey),
  );
}

export function clearOutbox(base) {
  if (!base) return;
  removeRaw(bucketKeys.outbox(base));
}

export const outbox = {
  read: readOutbox,
  add: addOutbox,
  update: updateOutbox,
  remove: removeOutbox,
  clear: clearOutbox,
};

/* ------------------------------------------------------------------ */
/* 分享草稿 / 配对后的跳转意图 / 安装提示偏好                           */
/* ------------------------------------------------------------------ */

export function setDraft(text) {
  const value = String(text ?? '').trim().slice(0, 8000);
  if (!value) return;
  writeJson(STORAGE_KEYS.draft, { text: value, at: new Date().toISOString() });
}

export function peekDraft() {
  const value = readJson(STORAGE_KEYS.draft, null);
  if (!value || typeof value.text !== 'string' || !value.text.trim()) return null;
  return value;
}

export function takeDraft() {
  const value = peekDraft();
  removeRaw(STORAGE_KEYS.draft);
  return value;
}

export function clearDraft() {
  removeRaw(STORAGE_KEYS.draft);
}

export const draft = { set: setDraft, peek: peekDraft, take: takeDraft, clear: clearDraft };

/** 未配对时把用户原本要去的路由记下来，配对后送回去 */
export function rememberNavIntent(path) {
  if (typeof path !== 'string' || !path.startsWith('/')) return;
  try {
    window.sessionStorage.setItem(STORAGE_KEYS.afterPair, path);
  } catch (error) {
    // 忽略（隐私模式）
  }
}

export function takeNavIntent() {
  try {
    const value = window.sessionStorage.getItem(STORAGE_KEYS.afterPair);
    window.sessionStorage.removeItem(STORAGE_KEYS.afterPair);
    return typeof value === 'string' && value.startsWith('/') ? value : '';
  } catch (error) {
    return '';
  }
}

export const navIntent = { remember: rememberNavIntent, take: takeNavIntent };

const DEFAULT_UI_PREFS = { installDismissed: false };

export function readUiPrefs() {
  const value = readJson(STORAGE_KEYS.ui, {});
  return { ...DEFAULT_UI_PREFS, ...(value && typeof value === 'object' ? value : {}) };
}

export function updateUiPrefs(patch) {
  const next = { ...readUiPrefs(), ...(patch || {}) };
  writeJson(STORAGE_KEYS.ui, next);
  return next;
}

export const uiPrefs = { get: readUiPrefs, update: updateUiPrefs };

/* ------------------------------------------------------------------ */
/* 缓存概览 / 清理                                                     */
/* ------------------------------------------------------------------ */

export function cacheSummary(base) {
  const tasks = readTaskCache(base);
  const notes = noteCacheStats(base);
  return {
    taskCount: tasks.items.length,
    noteCount: notes.count,
    noteChars: notes.chars,
    fetchedAt: tasks.fetchedAt,
  };
}

/** 只清任务与笔记缓存；配对信息、设置、待发送队列不受影响。 */
export function clearCaches(base) {
  clearTaskCache(base);
  clearNoteCache(base);
}

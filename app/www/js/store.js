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
  NOTE_CACHE_BUDGET_BYTES,
  NOTE_LEGACY_BUDGET_BYTES,
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
/* 笔记缓存（IndexedDB 优先，localStorage 兜底）                        */
/*                                                                    */
/* 实时架构下 Markdown 不再写 Relay/D1：手机收到 WS note 后只存本地，   */
/* 阅读器永远读本地缓存。IndexedDB 负责 1.5MB 级正文；不可用时退回      */
/* localStorage，保证旧环境/隐私模式也能离线阅读。                      */
/* ------------------------------------------------------------------ */

const NOTE_DB_NAME = 'linknote-notes';
const NOTE_DB_VERSION = 1;
const NOTE_STORE = 'notes';
let noteDbPromise = null;
let notePersistPromise = null;

function noteDbKey(base, taskId) {
  return `${base}::${taskId}`;
}

/** UTF-8 字节数：缓存预算按字节计算（Markdown 多为中文，不能按字符数） */
function textByteLength(value) {
  const text = typeof value === 'string' ? value : String(value ?? '');
  try {
    if (typeof TextEncoder === 'function') return new TextEncoder().encode(text).length;
  } catch (error) {
    // 退回手算
  }
  let bytes = 0;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.codePointAt(index) || 0;
    if (code <= 0x7f) bytes += 1;
    else if (code <= 0x7ff) bytes += 2;
    else if (code <= 0xffff) bytes += 3;
    else {
      bytes += 4;
      index += 1;
    }
  }
  return bytes;
}

/** 统一笔记条目字段（兼容旧缓存：补 pinned/bytes） */
function normalizeNoteEntry(entry) {
  if (!entry || typeof entry.markdown !== 'string') return null;
  return {
    ...entry,
    title: typeof entry.title === 'string' ? entry.title : '',
    pinned: entry.pinned === true,
    bytes: Number(entry.bytes) > 0 ? Number(entry.bytes) : textByteLength(entry.markdown),
  };
}

/**
 * 请求一次持久化存储（best-effort，仅请求一次）。
 * 只有 noteCache.put 成功后才调用；失败不影响保存结果。
 */
export async function ensureStoragePersisted() {
  if (notePersistPromise) return notePersistPromise;
  notePersistPromise = (async () => {
    try {
      const storage = typeof navigator !== 'undefined' ? navigator.storage : null;
      if (!storage || typeof storage.persist !== 'function') return false;
      if (typeof storage.persisted === 'function') {
        const already = await storage.persisted();
        if (already) return true;
      }
      return Boolean(await storage.persist());
    } catch (error) {
      return false;
    }
  })();
  return notePersistPromise;
}

function openNoteDb() {
  if (noteDbPromise) return noteDbPromise;
  noteDbPromise = new Promise((resolve) => {
    try {
      if (typeof window === 'undefined' || !window.indexedDB) {
        resolve(null);
        return;
      }
      const request = window.indexedDB.open(NOTE_DB_NAME, NOTE_DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(NOTE_STORE)) {
          const store = db.createObjectStore(NOTE_STORE, { keyPath: 'key' });
          store.createIndex('base', 'base', { unique: false });
          store.createIndex('cachedAt', 'cachedAt', { unique: false });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch (error) {
      resolve(null);
    }
  });
  return noteDbPromise;
}

function idbResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function idbGetNote(base, taskId) {
  const db = await openNoteDb();
  if (!db) return null;
  try {
    const tx = db.transaction(NOTE_STORE, 'readonly');
    return (await idbResult(tx.objectStore(NOTE_STORE).get(noteDbKey(base, taskId)))) || null;
  } catch (error) {
    return null;
  }
}

async function idbPutNote(entry) {
  const db = await openNoteDb();
  if (!db) return false;
  try {
    const tx = db.transaction(NOTE_STORE, 'readwrite');
    tx.objectStore(NOTE_STORE).put(entry);
    await new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
    return true;
  } catch (error) {
    return false;
  }
}

async function idbDeleteKeys(keys) {
  if (!keys.length) return;
  const db = await openNoteDb();
  if (!db) return;
  try {
    const tx = db.transaction(NOTE_STORE, 'readwrite');
    const store = tx.objectStore(NOTE_STORE);
    keys.forEach((key) => store.delete(key));
    await new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch (error) {
    // 忽略
  }
}

async function idbDeleteNote(base, taskId) {
  await idbDeleteKeys([noteDbKey(base, taskId)]);
}

async function idbListNotes(base) {
  const db = await openNoteDb();
  if (!db) return [];
  try {
    const tx = db.transaction(NOTE_STORE, 'readonly');
    return (await idbResult(tx.objectStore(NOTE_STORE).index('base').getAll(base))) || [];
  } catch (error) {
    return [];
  }
}

async function idbClearBaseNotes(base, { keepPinned = false } = {}) {
  const db = await openNoteDb();
  if (!db) return { removed: 0, kept: 0 };
  try {
    const tx = db.transaction(NOTE_STORE, 'readonly');
    const entries = (await idbResult(tx.objectStore(NOTE_STORE).index('base').getAll(base))) || [];
    const drop = [];
    let kept = 0;
    entries.forEach((entry) => {
      if (keepPinned && entry && entry.pinned === true) {
        kept += 1;
        return;
      }
      if (entry && entry.key) drop.push(entry.key);
    });
    await idbDeleteKeys(drop);
    return { removed: drop.length, kept };
  } catch (error) {
    return { removed: 0, kept: 0 };
  }
}

/** 淘汰未固定的旧笔记：数量上限 + 字节预算；固定/收藏笔记永不自动淘汰。 */
async function pruneNoteDb(base) {
  const entries = (await idbListNotes(base))
    .map(normalizeNoteEntry)
    .filter(Boolean)
    .sort((a, b) => (Number(b.cachedAt) || 0) - (Number(a.cachedAt) || 0));
  let count = 0;
  let bytes = 0;
  const drop = [];
  entries.forEach((entry) => {
    if (entry.pinned) {
      count += 1;
      bytes += entry.bytes;
      return;
    }
    if (count >= MAX_CACHED_NOTES || bytes + entry.bytes > NOTE_CACHE_BUDGET_BYTES) {
      if (entry.key) drop.push(entry.key);
      return;
    }
    count += 1;
    bytes += entry.bytes;
  });
  await idbDeleteKeys(drop);
}

/* ---- localStorage 兜底（旧版本缓存也在这里，读取时会迁移） ---- */

function readNoteMap(base) {
  if (!base) return {};
  const value = readJson(bucketKeys.notes(base), {});
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function readLegacyNote(base, taskId) {
  if (!base || !taskId) return null;
  const entry = readNoteMap(base)[taskId];
  return normalizeNoteEntry(entry);
}

function writeLegacyNote(base, taskId, note, { pinned = false } = {}) {
  if (!base || !taskId || !note || typeof note.markdown !== 'string') return false;
  const bytes = textByteLength(note.markdown);
  if (bytes > NOTE_CACHE_BUDGET_BYTES) return false;

  const map = readNoteMap(base);
  const previous = map[taskId];
  map[taskId] = {
    title: typeof note.title === 'string' ? note.title : '',
    markdown: note.markdown,
    completed_at: note.completed_at || null,
    cachedAt: Date.now(),
    pinned: pinIfRequested(pinned, previous),
    bytes,
  };

  const entries = Object.entries(map).sort(
    (a, b) => (Number(b[1] && b[1].cachedAt) || 0) - (Number(a[1] && a[1].cachedAt) || 0),
  );

  let count = 0;
  let total = 0;
  const kept = {};
  for (const [id, entry] of entries) {
    const normalized = normalizeNoteEntry(entry);
    if (!normalized) continue;
    if (normalized.pinned) {
      kept[id] = normalized;
      count += 1;
      total += normalized.bytes;
      continue;
    }
    if (count >= MAX_CACHED_NOTES || total + normalized.bytes > NOTE_LEGACY_BUDGET_BYTES) continue;
    kept[id] = normalized;
    count += 1;
    total += normalized.bytes;
  }

  const ok = writeJson(bucketKeys.notes(base), kept);
  if (ok) return true;
  // localStorage 配额不足：只保留固定笔记与当前这篇
  const fallback = {};
  Object.entries(kept).forEach(([id, entry]) => {
    if (entry.pinned || id === taskId) fallback[id] = entry;
  });
  return writeJson(bucketKeys.notes(base), fallback);
}

function pinIfRequested(pinned, previous) {
  if (pinned === true) return true;
  return Boolean(previous && previous.pinned === true);
}

function clearLegacyNotes(base, { keepPinned = false } = {}) {
  if (!keepPinned) {
    const map = readNoteMap(base);
    removeRaw(bucketKeys.notes(base));
    return { removed: Object.keys(map).length, kept: 0 };
  }
  const map = readNoteMap(base);
  const kept = {};
  let removed = 0;
  let keptCount = 0;
  Object.entries(map).forEach(([id, entry]) => {
    if (entry && entry.pinned === true) {
      kept[id] = entry;
      keptCount += 1;
    } else {
      removed += 1;
    }
  });
  writeJson(bucketKeys.notes(base), kept);
  return { removed, kept: keptCount };
}

/** 读取本地笔记（IndexedDB → 迁移 localStorage 旧缓存） */
export async function getNoteCache(base, taskId) {
  if (!base || !taskId) return null;
  const entry = normalizeNoteEntry(await idbGetNote(base, taskId));
  if (entry) return entry;

  const legacy = readLegacyNote(base, taskId);
  if (!legacy) return null;
  const migrated = await idbPutNote({
    ...legacy,
    key: noteDbKey(base, taskId),
    base,
    taskId,
    pinned: legacy.pinned === true,
    bytes: legacy.bytes || textByteLength(legacy.markdown),
  });
  if (migrated) removeLegacyNote(base, taskId);
  return legacy;
}

/**
 * 写入本地笔记（IndexedDB 优先，失败退回 localStorage）。
 * @param {{ pinned?: boolean }} options 不传 pinned 时沿用已有固定状态
 * @returns {Promise<boolean>} 只有真正落盘成功才返回 true（决定 ack persisted）
 */
export async function putNoteCache(base, taskId, note, { pinned } = {}) {
  if (!base || !taskId || !note || typeof note.markdown !== 'string') return false;
  const bytes = textByteLength(note.markdown);
  if (bytes > NOTE_CACHE_BUDGET_BYTES) return false;

  const existing = normalizeNoteEntry(await idbGetNote(base, taskId)) || readLegacyNote(base, taskId);
  const nextPinned = typeof pinned === 'boolean' ? pinned : Boolean(existing && existing.pinned);
  const entry = {
    key: noteDbKey(base, taskId),
    base,
    taskId,
    title: typeof note.title === 'string' ? note.title : '',
    markdown: note.markdown,
    completed_at: note.completed_at || null,
    cachedAt: Date.now(),
    pinned: nextPinned,
    bytes,
  };

  if (await idbPutNote(entry)) {
    await pruneNoteDb(base);
    void ensureStoragePersisted();
    return true;
  }
  const ok = writeLegacyNote(base, taskId, note, { pinned: nextPinned });
  if (ok) void ensureStoragePersisted();
  return ok;
}

/** 固定/取消固定（固定笔记不参与自动淘汰） */
export async function setNotePinned(base, taskId, pinned) {
  if (!base || !taskId) return false;
  const existing = await getNoteCache(base, taskId);
  if (!existing) return false;
  const nextPinned = pinned === true;
  const entry = {
    ...existing,
    key: noteDbKey(base, taskId),
    base,
    taskId,
    pinned: nextPinned,
    bytes: existing.bytes || textByteLength(existing.markdown),
  };
  if (await idbPutNote(entry)) return true;
  return writeLegacyNote(base, taskId, existing, { pinned: nextPinned });
}

/** 批量缓存索引（任务列表徽标/固定状态；一次 IO，不逐条查询） */
export async function listNoteMeta(base) {
  const map = new Map();
  if (!base) return map;
  (await idbListNotes(base)).forEach((entry) => {
    const normalized = normalizeNoteEntry(entry);
    if (normalized && typeof normalized.taskId === 'string') {
      map.set(normalized.taskId, {
        pinned: normalized.pinned,
        bytes: normalized.bytes,
        cachedAt: Number(normalized.cachedAt) || 0,
      });
    }
  });
  Object.entries(readNoteMap(base)).forEach(([taskId, entry]) => {
    if (map.has(taskId)) return;
    const normalized = normalizeNoteEntry(entry);
    if (normalized) {
      map.set(taskId, {
        pinned: normalized.pinned,
        bytes: normalized.bytes,
        cachedAt: Number(normalized.cachedAt) || 0,
      });
    }
  });
  return map;
}

export async function removeNoteCache(base, taskId) {
  if (!base || !taskId) return;
  await idbDeleteNote(base, taskId);
  removeLegacyNote(base, taskId);
}

export async function noteCacheStats(base) {
  const meta = await listNoteMeta(base);
  let bytes = 0;
  let pinnedCount = 0;
  let pinnedBytes = 0;
  meta.forEach((entry) => {
    bytes += entry.bytes || 0;
    if (entry.pinned) {
      pinnedCount += 1;
      pinnedBytes += entry.bytes || 0;
    }
  });
  return { count: meta.size, bytes, pinnedCount, pinnedBytes, chars: bytes };
}

/** 清除缓存；默认保留固定/收藏笔记（设置页会二次确认） */
export async function clearNoteCache(base, { keepPinned = false } = {}) {
  if (!base) return { removed: 0, kept: 0 };
  const idbResultCount = await idbClearBaseNotes(base, { keepPinned });
  const legacyResult = clearLegacyNotes(base, { keepPinned });
  return {
    removed: (idbResultCount.removed || 0) + (legacyResult.removed || 0),
    kept: Math.max(idbResultCount.kept || 0, legacyResult.kept || 0),
  };
}

export const noteCache = {
  get: getNoteCache,
  put: putNoteCache,
  remove: removeNoteCache,
  setPinned: setNotePinned,
  listMeta: listNoteMeta,
  stats: noteCacheStats,
  clear: clearNoteCache,
  ensurePersist: ensureStoragePersisted,
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

export async function cacheSummary(base) {
  const tasks = readTaskCache(base);
  const notes = await noteCacheStats(base);
  return {
    taskCount: tasks.items.length,
    noteCount: notes.count,
    noteBytes: notes.bytes,
    pinnedCount: notes.pinnedCount,
    noteChars: notes.bytes,
    fetchedAt: tasks.fetchedAt,
  };
}

/** 只清任务与笔记缓存；配对信息、设置、待发送队列不受影响。默认保留固定笔记。 */
export async function clearCaches(base, { keepPinned = true } = {}) {
  clearTaskCache(base);
  return clearNoteCache(base, { keepPinned });
}

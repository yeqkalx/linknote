/**
 * 链记 LinkNote — 通用工具函数
 * 只依赖 config.js，不依赖 DOM 以外的东西。
 */
import {
  CODE_ALPHABET,
  CODE_LENGTH,
  MAX_SHARE_TEXT,
  MAX_URL_LENGTH,
  PLATFORM_META,
  STORAGE_KEYS,
} from './config.js';

/** 转义 HTML 文本（用于拼模板字符串，所有用户数据都必须经过它） */
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    switch (char) {
      case '&':
        return '&amp;';
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '"':
        return '&quot;';
      default:
        return '&#39;';
    }
  });
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function truncate(value, max) {
  const text = String(value ?? '');
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

/** 生成 UUID v4（client_key 需要，契约要求 uuid-or-null） */
export function uuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID();
    } catch (error) {
      // 继续走兜底
    }
  }
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  const random = () => Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0');
  return `${random()}-${random().slice(0, 4)}-4${random().slice(1, 4)}-8${random().slice(1, 4)}-${random()}${random().slice(0, 4)}`;
}

/** 安全解析 JSON，解析失败返回 fallback */
export function safeJsonParse(raw, fallback) {
  if (typeof raw !== 'string' || !raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch (error) {
    return fallback;
  }
}

/* ------------------------------------------------------------------ */
/* 配对码                                                              */
/* ------------------------------------------------------------------ */

/**
 * 规范化配对码：忽略大小写、横线、空格，只保留契约字母表里的字符。
 * @returns {{ clean: string, formatted: string, valid: boolean }}
 */
export function normalizePairCode(raw) {
  const upper = String(raw ?? '').toUpperCase();
  const clean = Array.from(upper)
    .filter((char) => CODE_ALPHABET.includes(char))
    .join('')
    .slice(0, CODE_LENGTH);
  const formatted = clean.length > 4 ? `${clean.slice(0, 4)}-${clean.slice(4)}` : clean;
  return { clean, formatted, valid: clean.length === CODE_LENGTH };
}

/* ------------------------------------------------------------------ */
/* 链接识别                                                             */
/* ------------------------------------------------------------------ */

const URL_PATTERN = /https?:\/\/[^\s<>"'“”‘’（）()【】[\]{}]+/gi;
const BARE_HOST_PATTERN =
  /(?:^|[\s:：])((?:[\w-]+\.)*(?:bilibili\.com|b23\.tv|youtube\.com|youtu\.be|douyin\.com|iesdouyin\.com|kuaishou\.com)\/[^\s<>"'“”‘’（）()【】[\]{}]*)/gi;

function trimUrlTail(value) {
  return String(value).replace(/[)\].,;:!?，。；：、！？…"'”’》】]+$/g, '');
}

/**
 * 从分享文案里提取第一个链接（按出现位置，而不是按是否带协议头）。
 * Relay 的提取规则是「第一个链接 + 裸链接自动补 https://」，这里保持一致。
 */
export function extractFirstUrl(text) {
  const source = String(text ?? '');
  if (!source.trim()) return null;

  const candidates = [];
  let match;

  URL_PATTERN.lastIndex = 0;
  while ((match = URL_PATTERN.exec(source)) !== null) {
    candidates.push({ index: match.index, url: trimUrlTail(match[0]) });
    if (match.index === URL_PATTERN.lastIndex) URL_PATTERN.lastIndex += 1;
  }

  BARE_HOST_PATTERN.lastIndex = 0;
  while ((match = BARE_HOST_PATTERN.exec(source)) !== null) {
    if (match[1]) {
      const index = match.index + (match[0].length - match[1].length);
      candidates.push({ index, url: trimUrlTail(`https://${match[1]}`) });
    }
    if (match.index === BARE_HOST_PATTERN.lastIndex) BARE_HOST_PATTERN.lastIndex += 1;
  }

  if (!candidates.length) return null;
  candidates.sort((a, b) => a.index - b.index);
  for (const candidate of candidates) {
    try {
      const parsed = new URL(candidate.url);
      if (/^https?:$/.test(parsed.protocol)) return candidate.url;
    } catch (error) {
      // 继续尝试下一个候选
    }
  }
  return null;
}

/** 从链接/域名识别平台（与契约白名单一致），未知返回 null */
export function detectPlatform(value) {
  const urlText = extractFirstUrl(value) || String(value ?? '').trim();
  if (!urlText) return null;
  let host = '';
  try {
    host = new URL(urlText).hostname.toLowerCase().replace(/^www\./, '');
  } catch (error) {
    return null;
  }
  for (const [key, meta] of Object.entries(PLATFORM_META)) {
    if (meta.hosts.some((candidate) => host === candidate || host.endsWith(`.${candidate}`))) {
      return key;
    }
  }
  return null;
}

export function hostOf(value) {
  const urlText = extractFirstUrl(value) || String(value ?? '').trim();
  try {
    return new URL(urlText).hostname.replace(/^www\./, '');
  } catch (error) {
    return '';
  }
}

/** 列表里展示用的简洁来源：域名 + 路径前段 */
export function shortenUrl(value, max = 64) {
  const urlText = extractFirstUrl(value) || String(value ?? '').trim();
  if (!urlText) return '';
  try {
    const url = new URL(urlText);
    const host = url.hostname.replace(/^www\./, '');
    const path = url.pathname === '/' ? '' : url.pathname;
    return truncate(`${host}${path}`, max);
  } catch (error) {
    return truncate(urlText, max);
  }
}

/* ------------------------------------------------------------------ */
/* Relay 地址                                                          */
/* ------------------------------------------------------------------ */

/**
 * 规范化用户输入的 Relay 地址。
 * 返回 ''（表示留空/用默认）、规范化 origin 字符串，或 null（非法输入）。
 */
export function normalizeApiBase(input) {
  if (input === null || input === undefined) return '';
  let value = String(input).trim();
  if (!value) return '';
  if (value === 'null' || value === 'undefined') return '';

  // 允许用户误贴 /api/v1 结尾，自动去掉。
  value = value.replace(/\/+$/, '');
  value = value.replace(/\/api\/v1$/i, '').replace(/\/api$/i, '');
  value = value.replace(/\/+$/, '');

  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) {
    const isLocal =
      /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/i.test(value) ||
      /^\d{1,3}(\.\d{1,3}){3}(:\d+)?(\/|$)/.test(value);
    value = `${isLocal ? 'http' : 'https'}://${value}`;
  }

  let url;
  try {
    url = new URL(value);
  } catch (error) {
    return null;
  }
  if (!/^https?:$/.test(url.protocol)) return null;
  if (url.username || url.password) return null;
  if (url.pathname !== '/' && url.pathname !== '') return null;
  if (url.search || url.hash) return null;
  return url.origin;
}

export function apiBaseLabel(base) {
  if (!base) return '未设置';
  try {
    return new URL(base).host;
  } catch (error) {
    return base;
  }
}

/* ------------------------------------------------------------------ */
/* 时间格式化                                                          */
/* ------------------------------------------------------------------ */

export function parseTime(value) {
  const time = Date.parse(String(value ?? ''));
  return Number.isFinite(time) ? time : 0;
}

export function formatRelativeTime(value, now = Date.now()) {
  const time = parseTime(value);
  if (!time) return '';
  const diff = Math.max(0, now - time);
  if (diff < 45000) return '刚刚';
  if (diff < 3600000) return `${Math.max(1, Math.round(diff / 60000))} 分钟前`;
  if (diff < 86400000) return `${Math.round(diff / 3600000)} 小时前`;
  if (diff < 172800000) return '昨天';
  const date = new Date(time);
  const nowDate = new Date(now);
  if (date.getFullYear() === nowDate.getFullYear()) {
    return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
  }
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}

export function formatDateTime(value) {
  const time = parseTime(value);
  if (!time) return '';
  const date = new Date(time);
  const pad = (number) => String(number).padStart(2, '0');
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatClock(value) {
  const time = parseTime(value);
  if (!time) return '';
  const date = new Date(time);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/** 毫秒数 → “2 分 10 秒” */
export function formatElapsed(ms) {
  const totalSeconds = Math.max(0, Math.floor(Number(ms) / 1000) || 0);
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return seconds ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours} 小时 ${restMinutes} 分` : `${hours} 小时`;
}

/** 起止时间 → 已用时长 */
export function elapsedBetween(startValue, endValue) {
  const start = parseTime(startValue);
  if (!start) return 0;
  const end = endValue ? parseTime(endValue) : Date.now();
  return Math.max(0, (end || Date.now()) - start);
}

export function formatDurationSeconds(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '';
  return formatElapsed(value * 1000);
}

export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const rounded = size >= 10 || index === 0 ? Math.round(size) : Math.round(size * 10) / 10;
  return `${rounded} ${units[index]}`;
}

/* ------------------------------------------------------------------ */
/* 设备 / 平台                                                         */
/* ------------------------------------------------------------------ */

export function isNativePlatform() {
  try {
    const capacitor = typeof window !== 'undefined' ? window.Capacitor : null;
    if (capacitor && typeof capacitor.isNativePlatform === 'function') {
      return Boolean(capacitor.isNativePlatform());
    }
    // 部分 WebView 只注入 platform 字段。
    if (capacitor && typeof capacitor.platform === 'string') {
      return capacitor.platform !== 'web';
    }
  } catch (error) {
    // 忽略
  }
  return false;
}

export function devicePlatform() {
  if (isNativePlatform()) {
    try {
      const platform = window.Capacitor && window.Capacitor.getPlatform ? window.Capacitor.getPlatform() : '';
      if (platform === 'android' || platform === 'ios') return platform;
    } catch (error) {
      // 继续用 UA 判断
    }
  }
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
  if (/android/i.test(ua)) return 'android';
  if (/iphone|ipad|ipod/i.test(ua)) return 'ios';
  return 'web';
}

export function isIos() {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
  if (/iphone|ipad|ipod/i.test(ua)) return true;
  // iPadOS 13+ 的桌面 UA
  return ua.includes('Macintosh') && typeof navigator !== 'undefined' && navigator.maxTouchPoints > 1;
}

export function isStandalone() {
  try {
    if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true;
    if (window.matchMedia && window.matchMedia('(display-mode: minimal-ui)').matches) return true;
    if (navigator.standalone === true) return true;
    return isNativePlatform();
  } catch (error) {
    return false;
  }
}

export function prefersReducedMotion() {
  try {
    return Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch (error) {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* 剪贴板 / 下载                                                        */
/* ------------------------------------------------------------------ */

/** 复制文本，成功返回 true */
export async function copyText(value) {
  const text = String(value ?? '');
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (error) {
    // 退回 execCommand
  }
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '0';
    textarea.style.left = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');
    textarea.remove();
    return ok;
  } catch (error) {
    return false;
  }
}

export function safeFilename(value, fallback = '链记笔记') {
  const name = String(value ?? '')
    .replace(/[\u0000-\u001f<>:"/\\|?*]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80);
  return name || fallback;
}

/** 触发浏览器下载（App 内置为普通下载） */
export function downloadTextFile(filename, text, mime = 'text/markdown;charset=utf-8') {
  try {
    const blob = new Blob([String(text ?? '')], { type: mime });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return true;
  } catch (error) {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* 杂项                                                                */
/* ------------------------------------------------------------------ */

export function debounce(fn, wait = 250) {
  let timer = null;
  return function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

export function nextFrame() {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve());
    else setTimeout(resolve, 16);
  });
}

/** 读取分享文本（限制长度，避免超长 hash/存储） */
export function normalizeShareText(value) {
  return String(value ?? '').trim().slice(0, MAX_SHARE_TEXT);
}

/** 校验用户提交的文本长度（契约 url 1–2048；超长时前端先提取链接） */
export function fitSubmitText(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return { ok: false, reason: 'empty', text: '' };
  if (text.length <= MAX_URL_LENGTH) return { ok: true, text };
  const extracted = extractFirstUrl(text);
  if (extracted) return { ok: true, text: extracted, extracted: true };
  return { ok: false, reason: 'too_long', text: '' };
}

export { STORAGE_KEYS };

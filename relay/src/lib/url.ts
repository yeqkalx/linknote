/** URL 提取、平台识别、SSRF 基础防护。逻辑与 BiliNote / wecom_bot/platforms.py 对齐。 */

import type { Platform } from '../types';

const URL_RE = /https?:\/\/[^\s<>"'“”‘’（）()\[\]{}，。；;、]+/i;

// 已知平台裸域名（分享文案里常没有协议头）
const BARE_PLATFORM_RE =
  /(?:www\.)?(?:bilibili\.com\/video\/[A-Za-z0-9]+|b23\.tv\/[A-Za-z0-9]+|youtube\.com\/watch\?v=[\w-]+|youtube\.com\/shorts\/[\w-]+|youtu\.be\/[\w-]+|douyin\.com\/[\w-]+|v\.douyin\.com\/[\w-]+|kuaishou\.com\/[\w-]+|v\.kuaishou\.com\/[\w-]+)(?:[/?#][^\s<>"'“”‘’（）()\[\]{}，。；;、]*)?/i;

const TRAILING_JUNK = /[.,;:!?)>】》」』、。，；：！？]+$/;

const SHORT_LINK_PLATFORMS: Record<string, Platform> = {
  'b23.tv': 'bilibili',
  'youtu.be': 'youtube',
  'v.douyin.com': 'douyin',
  'v.kuaishou.com': 'kuaishou',
};

const IPV4_RE = /^\d{1,3}(?:\.\d{1,3}){3}$/;

/** 从分享文案里取第一个视频链接，并补全协议。 */
export function extractFirstUrl(text: string): string | null {
  if (!text) return null;
  const m = URL_RE.exec(text);
  if (m) return m[0].replace(TRAILING_JUNK, '');
  const b = BARE_PLATFORM_RE.exec(text);
  if (b) return `https://${b[0]}`.replace(TRAILING_JUNK, '');
  return null;
}

export function platformForHost(hostRaw: string): Platform | null {
  const host = hostRaw.toLowerCase().replace(/\.$/, '');
  if (!host) return null;
  if (SHORT_LINK_PLATFORMS[host]) return SHORT_LINK_PLATFORMS[host];

  const bare = host.startsWith('www.') ? host.slice(4) : host;
  if (bare === 'bilibili.com' || bare.endsWith('.bilibili.com')) return 'bilibili';
  if (bare === 'youtube.com' || bare.endsWith('.youtube.com') || bare === 'youtu.be') return 'youtube';
  if (bare === 'douyin.com' || bare.endsWith('.douyin.com') || bare === 'iesdouyin.com' || bare.endsWith('.iesdouyin.com')) {
    return 'douyin';
  }
  if (bare === 'kuaishou.com' || bare.endsWith('.kuaishou.com')) return 'kuaishou';
  return null;
}

function isPrivateOrLocalHost(host: string): boolean {
  const h = host.toLowerCase();
  if (h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.local') || h.endsWith('.internal')) {
    return true;
  }
  if (h === '[::1]' || h === '::1' || h.startsWith('[')) return true;
  if (IPV4_RE.test(h)) {
    const parts = h.split('.').map(Number);
    if (parts.some((p) => p < 0 || p > 255)) return true;
    if (parts[0] === 10 || parts[0] === 127 || parts[0] === 0) return true;
    if (parts[0] === 169 && parts[1] === 254) return true;
    if (parts[0] === 192 && parts[1] === 168) return true;
    if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true;
    if (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127) return true;
    return true; // 链接场景不需要直接访问任何 IP 字面量
  }
  return false;
}

export interface NormalizedUrl {
  url: string;
  platform: Platform;
}

/** 规范化并校验用户提交的链接，失败返回 null（调用方给统一错误）。 */
export function normalizeVideoUrl(input: string): NormalizedUrl | null {
  const raw = input.trim();
  if (!raw || raw.length > 4096) return null;

  const extracted = extractFirstUrl(raw);
  if (!extracted) return null;

  let candidate = extracted;
  if (!/^https?:\/\//i.test(candidate)) candidate = `https://${candidate}`;

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
  if (parsed.username || parsed.password) return null;
  if (parsed.port && parsed.port !== '80' && parsed.port !== '443') return null;

  const host = parsed.hostname.toLowerCase();
  if (!host || isPrivateOrLocalHost(host)) return null;

  const platform = platformForHost(host);
  if (!platform) return null;

  const normalized = parsed.toString();
  if (normalized.length > 2048) return null;
  return { url: normalized, platform };
}

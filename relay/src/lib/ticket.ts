/**
 * 手机实时连接 ticket：短时 HMAC 签名，绑定 agent_id / device_id。
 * 浏览器 WebSocket 不能带 Authorization 头，所以用 URL 上的短时 ticket 换连接。
 * 绝不把长期 device token 放进 URL。
 */

import {
  base64UrlDecodeText,
  base64UrlEncodeText,
  hmacSha256Base64Url,
  hmacVerifyBase64Url,
} from './crypto';

interface TicketPayload {
  /** agent_id */
  a: string;
  /** device_id */
  d: string;
  /** 过期时间（毫秒时间戳） */
  e: number;
  /** 一次性随机串（防重放留痕；当前只做长度校验） */
  n: string;
}

export interface VerifiedTicket {
  agentId: string;
  deviceId: string;
  expiresAt: number;
}

const MIN_TTL_MS = 1_000;
const MAX_TTL_MS = 10 * 60_000;

/** TTL 默认 60s（docs/realtime-protocol.md §1.2）。 */
export function ticketTtlMs(env: { TICKET_TTL_SECONDS?: string }): number {
  const n = Number(env.TICKET_TTL_SECONDS || '60');
  if (!Number.isFinite(n)) return 60_000;
  return Math.min(MAX_TTL_MS, Math.max(MIN_TTL_MS, Math.trunc(n) * 1000));
}

export async function issueTicket(
  secret: string,
  agentId: string,
  deviceId: string,
  ttlMs: number,
): Promise<{ ticket: string; expiresAt: number }> {
  const expiresAt = Date.now() + ttlMs;
  const payload: TicketPayload = { a: agentId, d: deviceId, e: expiresAt, n: crypto.randomUUID() };
  const encoded = base64UrlEncodeText(JSON.stringify(payload));
  const signature = await hmacSha256Base64Url(secret, encoded);
  return { ticket: `${encoded}.${signature}`, expiresAt };
}

export async function verifyTicket(
  secret: string,
  ticket: string,
  now = Date.now(),
): Promise<VerifiedTicket | null> {
  if (!secret || !ticket || ticket.length > 2048) return null;
  const parts = ticket.split('.');
  if (parts.length !== 2) return null;
  const [encoded, signature] = parts;
  const valid = await hmacVerifyBase64Url(secret, encoded, signature);
  if (!valid) return null;
  const raw = base64UrlDecodeText(encoded);
  if (!raw) return null;
  let payload: TicketPayload;
  try {
    payload = JSON.parse(raw) as TicketPayload;
  } catch {
    return null;
  }
  if (typeof payload.a !== 'string' || typeof payload.d !== 'string') return null;
  if (typeof payload.e !== 'number' || !Number.isFinite(payload.e)) return null;
  if (payload.a.length > 64 || payload.d.length > 64) return null;
  if (payload.e <= now) return null;
  return { agentId: payload.a, deviceId: payload.d, expiresAt: payload.e };
}

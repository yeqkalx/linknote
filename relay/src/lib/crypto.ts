/** token / 配对码生成与哈希，以及实时 ticket 的 HMAC 工具。 */

const PAIRING_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';

const textEncoder = new TextEncoder();

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function base64UrlToBytes(text: string): Uint8Array | null {
  const normalized = text.replace(/-/g, '+').replace(/_/g, '/');
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(normalized)) return null;
  try {
    const binary = atob(normalized);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch {
    return null;
  }
}

export function base64UrlEncodeText(text: string): string {
  return base64UrlEncode(textEncoder.encode(text));
}

export function base64UrlDecodeText(text: string): string | null {
  const bytes = base64UrlToBytes(text);
  if (!bytes) return null;
  try {
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    textEncoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign', 'verify'],
  );
}

export async function hmacSha256Base64Url(secret: string, payload: string): Promise<string> {
  const key = await hmacKey(secret);
  const signature = await crypto.subtle.sign('HMAC', key, textEncoder.encode(payload));
  return base64UrlEncode(new Uint8Array(signature));
}

/** 用 WebCrypto 的 verify 做常数时间比较。 */
export async function hmacVerifyBase64Url(
  secret: string,
  payload: string,
  signature: string,
): Promise<boolean> {
  const bytes = base64UrlToBytes(signature);
  if (!bytes || bytes.length === 0) return false;
  const key = await hmacKey(secret);
  try {
    return await crypto.subtle.verify('HMAC', key, bytes, textEncoder.encode(payload));
  } catch {
    return false;
  }
}

export function randomToken(prefix: 'agt' | 'dev'): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return `${prefix}_${base64UrlEncode(bytes)}`;
}

export function generatePairingCode(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  let code = '';
  for (const b of bytes) code += PAIRING_ALPHABET[b % PAIRING_ALPHABET.length];
  return `${code.slice(0, 4)}-${code.slice(4)}`;
}

/** 去掉横线/空格并转大写；格式不合法返回 null。 */
export function normalizePairingCode(raw: string): string | null {
  const code = raw.toUpperCase().replace(/[\s-]+/g, '');
  if (code.length !== 8) return null;
  for (const ch of code) {
    if (!PAIRING_ALPHABET.includes(ch)) return null;
  }
  return code;
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', textEncoder.encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function hashToken(token: string): Promise<string> {
  return sha256Hex(token);
}

export async function hashPairingCode(normalizedCode: string): Promise<string> {
  return sha256Hex(`pairing:${normalizedCode}`);
}

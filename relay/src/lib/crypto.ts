/** token / 配对码生成与哈希。 */

const PAIRING_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';

function base64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

export function randomToken(prefix: 'agt' | 'dev'): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return `${prefix}_${base64Url(bytes)}`;
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
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function hashToken(token: string): Promise<string> {
  return sha256Hex(token);
}

export async function hashPairingCode(normalizedCode: string): Promise<string> {
  return sha256Hex(`pairing:${normalizedCode}`);
}

/** 统一响应、参数校验、错误、时间工具。 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

export function ok(data: unknown, status = 200): Response {
  return json({ ok: true, data }, status);
}

export function fail(code: string, message: string, status = 400): Response {
  return json({ ok: false, error: { code, message } }, status);
}

export function bad(code: string, message: string): never {
  throw new ApiError(400, code, message);
}

/** 读取 JSON body，并对请求体大小做一次粗保护。 */
export async function readJson(req: Request): Promise<Record<string, unknown>> {
  const length = Number(req.headers.get('content-length') || '0');
  if (length > 3_000_000) {
    throw new ApiError(413, 'payload_too_large', '请求内容过大');
  }
  const text = await req.text();
  if (text.length > 3_000_000) {
    throw new ApiError(413, 'payload_too_large', '请求内容过大');
  }
  if (!text) return {};
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('not object');
    }
    return value as Record<string, unknown>;
  } catch {
    throw new ApiError(400, 'bad_request', '请求不是合法的 JSON 对象');
  }
}

export function optString(value: unknown, max: number): string | null {
  if (value == null) return null;
  if (typeof value !== 'string') throw new ApiError(400, 'bad_request', '字段类型错误');
  const s = value.trim();
  if (s.length > max) throw new ApiError(400, 'bad_request', `内容太长（最多 ${max} 字符）`);
  return s;
}

export function reqString(value: unknown, max: number, field: string): string {
  const s = optString(value, max);
  if (!s) throw new ApiError(400, 'bad_request', `缺少 ${field}`);
  return s;
}

export function optInt(value: unknown, min: number, max: number, fallback: number): number {
  if (value == null) return fallback;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) throw new ApiError(400, 'bad_request', '数字字段格式错误');
  return Math.min(max, Math.max(min, Math.trunc(n)));
}

export function isoNow(): string {
  return new Date().toISOString();
}

export function isoAfter(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

export function isOnline(lastSeenAt: string | null): boolean {
  if (!lastSeenAt) return false;
  const t = Date.parse(lastSeenAt);
  return Number.isFinite(t) && Date.now() - t <= 60_000;
}

export function bytesOf(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

export function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

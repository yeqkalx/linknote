/** LinkNote Relay Worker 入口：CORS + /api/v1 路由 + PWA 静态文件回退。 */

import { ApiError, fail } from './lib/http';
import * as h from './handlers';
import type { Env } from './types';

const API = '/api/v1';

interface CorsInfo {
  headers: Record<string, string>;
  allowed: boolean;
}

function resolveCors(request: Request, env: Env): CorsInfo {
  const origin = request.headers.get('origin');
  const entries = (env.ALLOWED_ORIGINS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const wildcard = entries.includes('*');
  const base: Record<string, string> = {
    'access-control-allow-methods': 'GET, POST, PATCH, DELETE, OPTIONS',
    'access-control-allow-headers': 'Authorization, Content-Type',
    'access-control-max-age': '86400',
  };

  if (!origin) return { headers: base, allowed: true };
  if (wildcard) {
    return { headers: { ...base, 'access-control-allow-origin': '*' }, allowed: true };
  }
  if (entries.includes(origin)) {
    return {
      headers: { ...base, 'access-control-allow-origin': origin, vary: 'Origin' },
      allowed: true,
    };
  }
  return { headers: { vary: 'Origin' }, allowed: false };
}

function withCors(response: Response, cors: CorsInfo): Response {
  for (const [key, value] of Object.entries(cors.headers)) {
    response.headers.set(key, value);
  }
  return response;
}

function decodePart(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    throw new ApiError(400, 'bad_request', '路径格式错误');
  }
}

async function routeApi(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;

  if (path === `${API}/health`) {
    if (request.method !== 'GET') return fail('method_not_allowed', '请求方法不支持', 405);
    return h.health(env);
  }

  if (!path.startsWith(`${API}/`)) {
    throw new ApiError(404, 'not_found', '接口不存在');
  }

  const raw = path.slice(API.length + 1);
  const seg = raw.split('/').filter(Boolean).map(decodePart);
  const method = request.method;

  // /pairings...
  if (seg[0] === 'pairings') {
    if (seg.length === 1) {
      if (method === 'POST') return h.createPairing(request, env);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 2 && seg[1] === 'claim') {
      if (method === 'POST') return h.claimPairing(request, env);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 2) {
      if (method === 'GET') return h.getPairing(request, env, seg[1]);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 3 && seg[2] === 'cancel') {
      if (method === 'POST') return h.cancelPairing(request, env, seg[1]);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
  }

  // /agent...
  if (seg[0] === 'agent') {
    if (seg.length === 2 && seg[1] === 'me') {
      if (method === 'GET') return h.agentMe(request, env);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 2 && seg[1] === 'heartbeat') {
      if (method === 'POST') return h.agentHeartbeat(request, env);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 2 && seg[1] === 'pairings') {
      if (method === 'POST') return h.createAgentPairing(request, env);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 3 && seg[1] === 'devices') {
      if (method === 'DELETE') return h.deleteAgentDevice(request, env, seg[2]);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length >= 3 && seg[1] === 'tasks') {
      if (seg.length === 3 && seg[2] === 'next') {
        if (method === 'GET') return h.nextTask(request, env);
        return fail('method_not_allowed', '请求方法不支持', 405);
      }
      if (seg.length === 3) {
        if (method === 'GET') return h.getAgentTask(request, env, seg[2]);
        return fail('method_not_allowed', '请求方法不支持', 405);
      }
      if (seg.length === 4) {
        if (method !== 'POST') return fail('method_not_allowed', '请求方法不支持', 405);
        if (seg[3] === 'progress') return h.progressTask(request, env, seg[2]);
        if (seg[3] === 'complete') return h.completeTask(request, env, seg[2]);
        if (seg[3] === 'fail') return h.failTask(request, env, seg[2]);
        if (seg[3] === 'heartbeat') return h.heartbeatTask(request, env, seg[2]);
      }
    }
  }

  // /me
  if (seg.length === 1 && seg[0] === 'me') {
    if (method === 'GET') return h.me(request, env);
    if (method === 'PATCH') return h.patchMe(request, env);
    if (method === 'DELETE') return h.deleteMe(request, env);
    return fail('method_not_allowed', '请求方法不支持', 405);
  }

  // /tasks...
  if (seg[0] === 'tasks') {
    if (seg.length === 1) {
      if (method === 'GET') return h.listTasks(request, env);
      if (method === 'POST') return h.createTask(request, env);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 2) {
      if (method === 'GET') return h.getTask(request, env, seg[1]);
      if (method === 'DELETE') return h.archiveTask(request, env, seg[1]);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
    if (seg.length === 3) {
      if (seg[2] === 'note' && method === 'GET') return h.getNote(request, env, seg[1]);
      if (seg[2] === 'retry' && method === 'POST') return h.retryTask(request, env, seg[1]);
      return fail('method_not_allowed', '请求方法不支持', 405);
    }
  }

  throw new ApiError(404, 'not_found', '接口不存在');
}

async function serveStatic(request: Request, url: URL, env: Env): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return fail('not_found', '接口不存在', 404);
  }
  const asset = await env.ASSETS.fetch(request);
  if (asset.status === 404) {
    const indexUrl = new URL('/index.html', url);
    return env.ASSETS.fetch(
      new Request(indexUrl.toString(), { method: 'GET', headers: request.headers }),
    );
  }
  return asset;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (!url.pathname.startsWith('/api/')) {
      return serveStatic(request, url, env);
    }

    const cors = resolveCors(request, env);
    if (!cors.allowed) {
      return fail('origin_not_allowed', '不允许的请求来源', 403);
    }
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors.headers });
    }

    try {
      return withCors(await routeApi(request, env), cors);
    } catch (error) {
      if (error instanceof ApiError) {
        return withCors(fail(error.code, error.message, error.status), cors);
      }
      console.error('relay unhandled error', error);
      return withCors(fail('internal', '服务器开小差了，请稍后再试', 500), cors);
    }
  },
};

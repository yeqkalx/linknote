/** Bearer token 认证。只做一次 token hash 查询；无效一律 401。 */

import { ApiError } from './http';
import { hashToken } from './crypto';
import type { AgentRow, DeviceRow, Env } from '../types';

export interface DeviceWithAgent {
  device: DeviceRow;
  agent: AgentRow;
}

function bearer(req: Request): string {
  const header = req.headers.get('authorization') || '';
  const m = /^Bearer\s+(\S+)$/i.exec(header.trim());
  if (!m) throw new ApiError(401, 'unauthorized', '缺少登录凭证');
  const token = m[1];
  if (token.length > 200) throw new ApiError(401, 'unauthorized', '登录凭证无效');
  return token;
}

export async function requireAgent(req: Request, env: Env): Promise<AgentRow> {
  const token = bearer(req);
  if (!token.startsWith('agt_')) throw new ApiError(401, 'unauthorized', 'Agent 凭证无效');
  const row = await env.DB.prepare('SELECT * FROM agents WHERE token_hash = ?').bind(await hashToken(token)).first<AgentRow>();
  if (!row) throw new ApiError(401, 'unauthorized', 'Agent 凭证已失效，请重新配对');
  return row;
}

export async function requireDevice(req: Request, env: Env): Promise<DeviceWithAgent> {
  const token = bearer(req);
  if (!token.startsWith('dev_')) throw new ApiError(401, 'unauthorized', '设备凭证无效');
  const hash = await hashToken(token);
  const row = await env.DB.prepare(
    `SELECT d.id AS d_id, d.agent_id AS d_agent_id, d.name AS d_name, d.platform AS d_platform,
            d.token_hash AS d_token_hash, d.created_at AS d_created_at, d.last_seen_at AS d_last_seen_at,
            d.revoked_at AS d_revoked_at,
            a.id AS a_id, a.name AS a_name, a.platform AS a_platform, a.version AS a_version,
            a.token_hash AS a_token_hash, a.created_at AS a_created_at, a.last_seen_at AS a_last_seen_at
       FROM devices d JOIN agents a ON a.id = d.agent_id
      WHERE d.token_hash = ? AND d.revoked_at IS NULL
      LIMIT 1`,
  )
    .bind(hash)
    .first<Record<string, string | null>>();

  if (!row) throw new ApiError(401, 'unauthorized', '设备凭证已失效，请重新配对');

  const device: DeviceRow = {
    id: row.d_id as string,
    agent_id: row.d_agent_id as string,
    name: row.d_name as string,
    platform: row.d_platform as string,
    token_hash: row.d_token_hash as string,
    created_at: row.d_created_at as string,
    last_seen_at: (row.d_last_seen_at as string | null) ?? null,
    revoked_at: (row.d_revoked_at as string | null) ?? null,
  };
  const agent: AgentRow = {
    id: row.a_id as string,
    name: row.a_name as string,
    platform: row.a_platform as string,
    version: row.a_version as string,
    token_hash: row.a_token_hash as string,
    created_at: row.a_created_at as string,
    last_seen_at: (row.a_last_seen_at as string | null) ?? null,
  };
  return { device, agent };
}

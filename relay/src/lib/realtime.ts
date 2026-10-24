/** Worker → Durable Object 的通知与在线状态查询（内部 secret 保护）。 */

import { isOnline } from './http';
import type { AgentPresence, Env, RoomEvent } from '../types';

const ROOM_ORIGIN = 'https://agent-room.internal';

function roomStub(env: Env, agentId: string): DurableObjectStub {
  return env.AGENT_ROOM.get(env.AGENT_ROOM.idFromName(agentId));
}

function internalHeaders(env: Env, agentId: string): Headers {
  const headers = new Headers({ 'content-type': 'application/json' });
  headers.set('x-internal-secret', env.INTERNAL_SECRET || '');
  headers.set('x-agent-id', agentId);
  return headers;
}

/** 投递一个内部事件给 agent 的 DO；失败只记日志，不影响 REST 主流程。 */
export async function notifyAgentRoom(env: Env, agentId: string, event: RoomEvent): Promise<boolean> {
  try {
    const stub = roomStub(env, agentId);
    const response = await stub.fetch(
      new Request(`${ROOM_ORIGIN}/internal/event`, {
        method: 'POST',
        headers: internalHeaders(env, agentId),
        body: JSON.stringify(event),
      }),
    );
    if (!response.ok) {
      console.warn(`relay room event ${event.type} failed`, response.status);
      return false;
    }
    return true;
  } catch (error) {
    console.warn(`relay room event ${event.type} failed`, (error as Error)?.message || error);
    return false;
  }
}

/** 手机 REST 快照里的 agent.online 以 DO 实时状态为准，DO 不可用时退回 D1 last_seen。 */
export async function getAgentPresence(
  env: Env,
  agentId: string,
  fallbackLastSeen: string | null,
): Promise<AgentPresence> {
  try {
    const stub = roomStub(env, agentId);
    const response = await stub.fetch(
      new Request(`${ROOM_ORIGIN}/internal/presence`, {
        method: 'GET',
        headers: internalHeaders(env, agentId),
      }),
    );
    if (response.ok) {
      const body = (await response.json()) as {
        ok?: boolean;
        data?: { online?: boolean; last_seen_at?: string | null; realtime?: boolean };
      };
      if (body?.ok && body.data) {
        return {
          online: Boolean(body.data.online),
          last_seen_at: body.data.last_seen_at ?? fallbackLastSeen,
          realtime: Boolean(body.data.realtime),
        };
      }
    }
  } catch (error) {
    console.warn('relay room presence failed', (error as Error)?.message || error);
  }
  return { online: isOnline(fallbackLastSeen), last_seen_at: fallbackLastSeen, realtime: false };
}

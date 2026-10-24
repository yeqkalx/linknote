/** 所有 Relay API handler。路由见 src/index.ts，接口语义见 docs/api-contract.md。 */

import {
  ApiError,
  bad,
  bytesOf,
  fail,
  isoAfter,
  isoNow,
  json,
  ok,
  oneOf,
  optInt,
  optString,
  readJson,
  reqString,
} from './lib/http';
import {
  generatePairingCode,
  hashPairingCode,
  hashToken,
  normalizePairingCode,
  randomToken,
} from './lib/crypto';
import { requireAgent, requireDevice } from './lib/auth';
import { getAgentPresence, notifyAgentRoom } from './lib/realtime';
import { TASK_SUMMARY_COLUMNS, toAgentPublic, toDevicePublic, toTaskSummary } from './lib/rows';
import { issueTicket, ticketTtlMs } from './lib/ticket';
import { normalizeVideoUrl } from './lib/url';
import type {
  AgentPresence,
  AgentPublic,
  AgentRow,
  AgentPushTask,
  DevicePublic,
  Env,
  PairingRow,
  TaskSummary,
} from './types';

export const RELAY_VERSION = '0.3.0';

const AGENT_PLATFORMS = ['windows', 'macos', 'linux'] as const;
const DEVICE_PLATFORMS = ['android', 'ios', 'web'] as const;
const PROGRESS_STAGES = [
  'parsing',
  'downloading',
  'transcribing',
  'summarizing',
  'formatting',
  'saving',
] as const;

function pairingTtl(env: Env): number {
  const n = Number(env.PAIRING_TTL_SECONDS || '600');
  return Number.isFinite(n) ? Math.min(3600, Math.max(60, Math.trunc(n))) : 600;
}

function leaseSeconds(env: Env): number {
  const n = Number(env.TASK_LEASE_SECONDS || '1800');
  return Number.isFinite(n) ? Math.min(7200, Math.max(300, Math.trunc(n))) : 1800;
}

function pairingStatus(row: PairingRow): 'pending' | 'claimed' | 'expired' {
  if (row.claimed_at) return 'claimed';
  if (row.canceled_at) return 'expired';
  if (Date.parse(row.expires_at) <= Date.now()) return 'expired';
  return 'pending';
}

async function newPairingSession(
  env: Env,
  agentId: string,
): Promise<{ pairing_id: string; code: string; expires_at: string }> {
  const ttl = pairingTtl(env);
  for (let attempt = 0; attempt < 5; attempt++) {
    const code = generatePairingCode();
    const normalized = normalizePairingCode(code);
    if (!normalized) continue;
    const codeHash = await hashPairingCode(normalized);
    const exists = await env.DB.prepare('SELECT id FROM pairing_sessions WHERE code_hash = ?')
      .bind(codeHash)
      .first<{ id: string }>();
    if (exists) continue;

    const id = crypto.randomUUID();
    const now = isoNow();
    const expiresAt = isoAfter(ttl);
    await env.DB.prepare(
      `INSERT INTO pairing_sessions (id, agent_id, code_hash, created_at, expires_at)
       VALUES (?, ?, ?, ?, ?)`,
    )
      .bind(id, agentId, codeHash, now, expiresAt)
      .run();
    return { pairing_id: id, code, expires_at: expiresAt };
  }
  throw new ApiError(500, 'internal', '生成配对码失败，请重试');
}

async function devicePublicById(env: Env, deviceId: string): Promise<DevicePublic | null> {
  const row = await env.DB.prepare(
    `SELECT id, agent_id, name, platform, token_hash, created_at, last_seen_at, revoked_at
       FROM devices WHERE id = ?`,
  )
    .bind(deviceId)
    .first<Record<string, string | null>>();
  if (!row) return null;
  return toDevicePublic({
    id: row.id as string,
    agent_id: row.agent_id as string,
    name: row.name as string,
    platform: row.platform as string,
    token_hash: row.token_hash as string,
    created_at: row.created_at as string,
    last_seen_at: (row.last_seen_at as string | null) ?? null,
    revoked_at: (row.revoked_at as string | null) ?? null,
  });
}

async function failTasksForDevice(env: Env, deviceId: string): Promise<void> {
  const now = isoNow();
  await env.DB.prepare(
    `UPDATE tasks
        SET status = 'failed', stage = 'failed', progress = 0,
            error_code = 'device_unpaired', error_message = '设备已解除配对',
            completed_at = ?, updated_at = ?, lease_expires_at = NULL
      WHERE device_id = ? AND status IN ('queued', 'running')`,
  )
    .bind(now, now, deviceId)
    .run();
}

function taskNotFound(): never {
  throw new ApiError(404, 'task_not_found', '任务不存在');
}

/** Agent 在线状态以 DO 实时 socket 为准；DO 不可用/从未用过实时通道时退回 D1 last_seen。 */
async function presenceOf(env: Env, agent: AgentRow): Promise<AgentPresence> {
  return getAgentPresence(env, agent.id, agent.last_seen_at);
}

function toAgentPushTask(task: TaskSummary): AgentPushTask {
  return {
    id: task.id,
    url: task.url,
    platform: task.platform,
    status: task.status,
    stage: task.stage,
    progress: task.progress,
    title: task.title,
    created_at: task.created_at,
  };
}

async function taskSummaryById(env: Env, agentId: string, taskId: string): Promise<TaskSummary | null> {
  const row = await env.DB.prepare(
    `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE id = ? AND agent_id = ? LIMIT 1`,
  )
    .bind(taskId, agentId)
    .first<Record<string, unknown>>();
  return row ? toTaskSummary(row) : null;
}

/** 新建任务后推给 Agent（WS 在线才会收到；离线时 D1 queued 会在重连 hello 后补发）。 */
async function notifyTaskCreated(env: Env, agentId: string, task: TaskSummary): Promise<void> {
  await notifyAgentRoom(env, agentId, { type: 'task_created', task: toAgentPushTask(task) });
}

/** REST 改任务元数据后把最新快照推给手机（老 REST 轮询 Agent 也能让新 PWA 实时看到进度）。 */
async function notifyTaskSnapshot(env: Env, agentId: string, taskId: string): Promise<void> {
  const snapshot = await taskSummaryById(env, agentId, taskId);
  if (!snapshot) return;
  await notifyAgentRoom(env, agentId, { type: 'task_snapshot', task: snapshot });
}

export async function createRealtimeTicket(req: Request, env: Env): Promise<Response> {
  const { device, agent } = await requireDevice(req, env);
  if (!env.TICKET_SECRET) throw new ApiError(500, 'internal', '实时通道未配置，请联系管理员');
  const ttl = ticketTtlMs(env);
  const { ticket, expiresAt } = await issueTicket(env.TICKET_SECRET, agent.id, device.id, ttl);
  const presence = await presenceOf(env, agent);
  return ok({
    ticket,
    expires_at: new Date(expiresAt).toISOString(),
    agent_id: agent.id,
    agent_online: presence.online,
    server_time: isoNow(),
  });
}

// ---------------------------------------------------------------- health

export async function health(env: Env): Promise<Response> {
  await env.DB.prepare('SELECT 1').first();
  return ok({ status: 'ok', version: RELAY_VERSION, realtime: true, time: isoNow() });
}

// ---------------------------------------------------------------- pairing

export async function createPairing(req: Request, env: Env): Promise<Response> {
  const body = await readJson(req);
  const name = (optString(body.agent_name, 40) || '我的电脑').slice(0, 40);
  const platform = oneOf(body.platform, AGENT_PLATFORMS, 'windows');
  const version = optString(body.version, 40) || '';

  const agentId = crypto.randomUUID();
  const agentToken = randomToken('agt');
  const tokenHash = await hashToken(agentToken);
  const now = isoNow();

  await env.DB.prepare(
    `INSERT INTO agents (id, name, platform, version, token_hash, created_at, last_seen_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(agentId, name, platform, version, tokenHash, now, now)
    .run();

  const session = await newPairingSession(env, agentId);
  return ok(
    {
      agent_id: agentId,
      pairing_id: session.pairing_id,
      code: session.code,
      expires_at: session.expires_at,
      agent_token: agentToken,
    },
    201,
  );
}

export async function getPairing(req: Request, env: Env, pairingId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const row = await env.DB.prepare('SELECT * FROM pairing_sessions WHERE id = ? AND agent_id = ?')
    .bind(pairingId, agent.id)
    .first<PairingRow>();
  if (!row) throw new ApiError(404, 'pairing_not_found', '配对码不存在');

  let device: DevicePublic | null = null;
  if (row.claimed_device_id) {
    device = await devicePublicById(env, row.claimed_device_id);
  }
  return ok({ status: pairingStatus(row), expires_at: row.expires_at, device });
}

export async function cancelPairing(req: Request, env: Env, pairingId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const row = await env.DB.prepare('SELECT * FROM pairing_sessions WHERE id = ? AND agent_id = ?')
    .bind(pairingId, agent.id)
    .first<PairingRow>();
  if (!row) throw new ApiError(404, 'pairing_not_found', '配对码不存在');
  if (row.claimed_at) throw new ApiError(409, 'pairing_already_claimed', '配对码已经被使用');
  if (row.canceled_at) return ok({});

  await env.DB.prepare('UPDATE pairing_sessions SET canceled_at = ? WHERE id = ? AND agent_id = ?')
    .bind(isoNow(), pairingId, agent.id)
    .run();
  return ok({});
}

export async function claimPairing(req: Request, env: Env): Promise<Response> {
  const body = await readJson(req);
  const rawCode = reqString(body.code, 32, '配对码');
  const normalized = normalizePairingCode(rawCode);
  if (!normalized) throw new ApiError(400, 'invalid_code', '配对码格式不对，应为 8 位字母数字');
  const codeHash = await hashPairingCode(normalized);

  const row = await env.DB.prepare('SELECT * FROM pairing_sessions WHERE code_hash = ?')
    .bind(codeHash)
    .first<PairingRow>();
  if (!row) throw new ApiError(404, 'pairing_not_found', '配对码不存在或已失效');
  if (row.claimed_at) throw new ApiError(409, 'pairing_already_claimed', '配对码已经被使用');
  if (row.canceled_at) throw new ApiError(409, 'pairing_already_claimed', '配对码已被取消');
  if (Date.parse(row.expires_at) <= Date.now()) {
    throw new ApiError(409, 'pairing_expired', '配对码已过期，请在电脑上重新生成');
  }

  const deviceName = (optString(body.device_name, 40) || '我的手机').slice(0, 40);
  const devicePlatform = oneOf(body.platform, DEVICE_PLATFORMS, 'android');
  const deviceId = crypto.randomUUID();
  const deviceToken = randomToken('dev');
  const tokenHash = await hashToken(deviceToken);
  const now = isoNow();

  // 先插设备，再原子占用配对码；抢占失败就把设备删掉。
  await env.DB.prepare(
    `INSERT INTO devices (id, agent_id, name, platform, token_hash, created_at, last_seen_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(deviceId, row.agent_id, deviceName, devicePlatform, tokenHash, now, now)
    .run();

  const claimed = await env.DB.prepare(
    `UPDATE pairing_sessions
        SET claimed_at = ?, claimed_device_id = ?
      WHERE id = ? AND claimed_at IS NULL AND canceled_at IS NULL AND expires_at > ?`,
  )
    .bind(now, deviceId, row.id, now)
    .run();

  if (!claimed.meta || claimed.meta.changes !== 1) {
    await env.DB.prepare('DELETE FROM devices WHERE id = ?').bind(deviceId).run();
    throw new ApiError(409, 'pairing_already_claimed', '配对码已经被使用，请重新生成');
  }

  const agent = await env.DB.prepare('SELECT * FROM agents WHERE id = ?')
    .bind(row.agent_id)
    .first<AgentRow>();
  if (!agent) throw new ApiError(500, 'internal', '电脑信息丢失，请重新配对');

  return ok({
    device_id: deviceId,
    device_token: deviceToken,
    agent: { id: agent.id, name: agent.name, platform: agent.platform },
    server_time: now,
  });
}

export async function createAgentPairing(req: Request, env: Env): Promise<Response> {
  const agent = await requireAgent(req, env);
  const session = await newPairingSession(env, agent.id);
  return ok(
    { pairing_id: session.pairing_id, code: session.code, expires_at: session.expires_at },
    201,
  );
}

// ---------------------------------------------------------------- agent self

export async function agentMe(req: Request, env: Env): Promise<Response> {
  const agent = await requireAgent(req, env);
  const devices = await env.DB.prepare(
    `SELECT id, agent_id, name, platform, token_hash, created_at, last_seen_at, revoked_at
       FROM devices WHERE agent_id = ? AND revoked_at IS NULL ORDER BY created_at DESC`,
  )
    .bind(agent.id)
    .all<Record<string, string | null>>();

  const counts = await env.DB.prepare(
    `SELECT status, COUNT(*) AS n FROM tasks
      WHERE agent_id = ? AND status IN ('queued', 'running') GROUP BY status`,
  )
    .bind(agent.id)
    .all<{ status: string; n: number }>();

  const queue = { queued: 0, running: 0 };
  for (const c of counts.results || []) {
    if (c.status === 'queued') queue.queued = Number(c.n) || 0;
    if (c.status === 'running') queue.running = Number(c.n) || 0;
  }

  const presence = await presenceOf(env, agent);
  return ok({
    agent: {
      ...toAgentPublic(agent),
      online: presence.online,
      last_seen_at: presence.last_seen_at,
    },
    // v0.3 readiness：在线 ≠ 可生成（旧 Agent 不上报时为 null）。
    ready: presence.ready,
    issues: presence.issues,
    devices: (devices.results || []).map((d) =>
      toDevicePublic({
        id: d.id as string,
        agent_id: d.agent_id as string,
        name: d.name as string,
        platform: d.platform as string,
        token_hash: d.token_hash as string,
        created_at: d.created_at as string,
        last_seen_at: (d.last_seen_at as string | null) ?? null,
        revoked_at: (d.revoked_at as string | null) ?? null,
      }),
    ),
    queue,
    server_time: isoNow(),
  });
}

export async function agentHeartbeat(req: Request, env: Env): Promise<Response> {
  const agent = await requireAgent(req, env);
  const body = await readJson(req);
  const version = optString(body.version, 40);
  const now = isoNow();

  if (version) {
    await env.DB.prepare('UPDATE agents SET last_seen_at = ?, version = ? WHERE id = ?')
      .bind(now, version, agent.id)
      .run();
  } else {
    await env.DB.prepare('UPDATE agents SET last_seen_at = ? WHERE id = ?').bind(now, agent.id).run();
  }

  const queued = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM tasks WHERE agent_id = ? AND status = 'queued'`,
  )
    .bind(agent.id)
    .first<{ n: number }>();

  return ok({ server_time: now, queued_tasks: Number(queued?.n || 0) });
}

export async function deleteAgentDevice(req: Request, env: Env, deviceId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const device = await env.DB.prepare('SELECT id FROM devices WHERE id = ? AND agent_id = ?')
    .bind(deviceId, agent.id)
    .first<{ id: string }>();
  if (!device) throw new ApiError(404, 'not_found', '设备不存在');

  await env.DB.prepare('UPDATE devices SET revoked_at = ? WHERE id = ? AND agent_id = ?')
    .bind(isoNow(), deviceId, agent.id)
    .run();
  await failTasksForDevice(env, deviceId);
  await notifyAgentRoom(env, agent.id, { type: 'device_revoked', device_id: deviceId });
  return ok({});
}

// ---------------------------------------------------------------- device self

export async function me(req: Request, env: Env): Promise<Response> {
  const { device, agent } = await requireDevice(req, env);
  const presence = await presenceOf(env, agent);
  return ok({
    device: {
      id: device.id,
      name: device.name,
      platform: device.platform,
      paired_at: device.created_at,
      last_seen_at: device.last_seen_at,
    },
    agent: {
      id: agent.id,
      name: agent.name,
      online: presence.online,
      last_seen_at: presence.last_seen_at,
    },
    server_time: isoNow(),
  });
}

export async function patchMe(req: Request, env: Env): Promise<Response> {
  const { device } = await requireDevice(req, env);
  const body = await readJson(req);
  const name = reqString(body.device_name, 40, '设备名');
  await env.DB.prepare('UPDATE devices SET name = ? WHERE id = ?').bind(name, device.id).run();
  return ok({
    device: {
      id: device.id,
      name,
      platform: device.platform,
      paired_at: device.created_at,
      last_seen_at: device.last_seen_at,
    },
  });
}

export async function deleteMe(req: Request, env: Env): Promise<Response> {
  const { device } = await requireDevice(req, env);
  await env.DB.prepare('UPDATE devices SET revoked_at = ? WHERE id = ?')
    .bind(isoNow(), device.id)
    .run();
  await failTasksForDevice(env, device.id);
  await notifyAgentRoom(env, device.agent_id, { type: 'device_revoked', device_id: device.id });
  return ok({});
}

// ---------------------------------------------------------------- tasks

export async function createTask(req: Request, env: Env): Promise<Response> {
  const { device } = await requireDevice(req, env);
  const body = await readJson(req);
  const urlInput = reqString(body.url, 4096, '链接');

  let clientKey: string | null = null;
  if (body.client_key != null) {
    if (typeof body.client_key !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(body.client_key)) {
      bad('bad_request', 'client_key 格式错误');
    }
    clientKey = body.client_key;
    const existing = await env.DB.prepare(
      `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE device_id = ? AND client_key = ? LIMIT 1`,
    )
      .bind(device.id, clientKey)
      .first<Record<string, unknown>>();
    if (existing) return ok({ task: toTaskSummary(existing) }, 200);
  }

  const normalized = normalizeVideoUrl(urlInput);
  if (!normalized) {
    throw new ApiError(400, 'invalid_url', '只支持 B 站 / YouTube / 抖音 / 快手的视频链接');
  }

  const id = crypto.randomUUID();
  const now = isoNow();
  try {
    await env.DB.prepare(
      `INSERT INTO tasks (id, agent_id, device_id, client_key, url, platform, status, stage,
                          progress, message, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 'queued', 'pending', 0, '排队中', ?, ?)`,
    )
      .bind(id, device.agent_id, device.id, clientKey, normalized.url, normalized.platform, now, now)
      .run();
  } catch (e) {
    if (clientKey) {
      const existing = await env.DB.prepare(
        `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE device_id = ? AND client_key = ? LIMIT 1`,
      )
        .bind(device.id, clientKey)
        .first<Record<string, unknown>>();
      if (existing) return ok({ task: toTaskSummary(existing) }, 200);
    }
    throw e;
  }

  const row = await env.DB.prepare(`SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE id = ?`)
    .bind(id)
    .first<Record<string, unknown>>();
  const summary = toTaskSummary(row || {});
  await notifyTaskCreated(env, device.agent_id, summary);
  return ok({ task: summary }, 201);
}

export async function listTasks(req: Request, env: Env): Promise<Response> {
  const { device } = await requireDevice(req, env);
  const url = new URL(req.url);
  const limit = optInt(url.searchParams.get('limit'), 1, 50, 20);
  const before = url.searchParams.get('before');

  let sql = `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE device_id = ? AND archived_at IS NULL`;
  const binds: unknown[] = [device.id];
  if (before) {
    if (Number.isNaN(Date.parse(before))) bad('bad_request', 'before 游标格式错误');
    sql += ' AND created_at < ?';
    binds.push(before);
  }
  sql += ' ORDER BY created_at DESC LIMIT ?';
  binds.push(limit);

  const result = await env.DB.prepare(sql)
    .bind(...binds)
    .all<Record<string, unknown>>();
  const items = (result.results || []).map(toTaskSummary);
  const nextBefore = items.length === limit ? items[items.length - 1].created_at : null;
  return ok({ items, next_before: nextBefore });
}

async function getOwnedTask(
  env: Env,
  deviceId: string,
  taskId: string,
): Promise<Record<string, unknown> | null> {
  return env.DB.prepare(
    `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE id = ? AND device_id = ? LIMIT 1`,
  )
    .bind(taskId, deviceId)
    .first<Record<string, unknown>>();
}

export async function getTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const { device, agent } = await requireDevice(req, env);
  const row = await getOwnedTask(env, device.id, taskId);
  if (!row) return taskNotFound();
  const presence = await presenceOf(env, agent);
  return ok({
    task: toTaskSummary(row),
    agent: { id: agent.id, name: agent.name, online: presence.online },
  });
}

export async function getNote(req: Request, env: Env, taskId: string): Promise<Response> {
  const { device } = await requireDevice(req, env);
  const row = await env.DB.prepare('SELECT id FROM tasks WHERE id = ? AND device_id = ? LIMIT 1')
    .bind(taskId, device.id)
    .first<{ id: string }>();
  if (!row) return taskNotFound();
  throw new ApiError(
    410,
    'note_not_stored',
    '笔记正文不再保存在服务器，请通过实时通道获取（手机端会把笔记缓存在本地）',
  );
}

export async function retryTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const { device } = await requireDevice(req, env);
  const row = await env.DB.prepare(
    `SELECT id, status, url, platform FROM tasks WHERE id = ? AND device_id = ? LIMIT 1`,
  )
    .bind(taskId, device.id)
    .first<{ id: string; status: string; url: string; platform: string }>();
  if (!row) return taskNotFound();
  if (row.status !== 'failed' && row.status !== 'succeeded') {
    throw new ApiError(409, 'invalid_state', '任务还在处理中，不能重试');
  }

  const newId = crypto.randomUUID();
  const now = isoNow();
  await env.DB.prepare(
    `INSERT INTO tasks (id, agent_id, device_id, url, platform, status, stage, progress, message, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, 'queued', 'pending', 0, '排队中', ?, ?)`,
  )
    .bind(newId, device.agent_id, device.id, row.url, row.platform, now, now)
    .run();

  const created = await env.DB.prepare(`SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE id = ?`)
    .bind(newId)
    .first<Record<string, unknown>>();
  const summary = toTaskSummary(created || {});
  await notifyTaskCreated(env, device.agent_id, summary);
  return ok({ task: summary }, 201);
}

export async function archiveTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const { device } = await requireDevice(req, env);
  const row = await env.DB.prepare('SELECT status FROM tasks WHERE id = ? AND device_id = ?')
    .bind(taskId, device.id)
    .first<{ status: string }>();
  if (!row) return taskNotFound();
  if (row.status === 'queued' || row.status === 'running') {
    throw new ApiError(409, 'invalid_state', '任务还在处理中，不能移除');
  }
  await env.DB.prepare('UPDATE tasks SET archived_at = ?, updated_at = ? WHERE id = ? AND device_id = ?')
    .bind(isoNow(), isoNow(), taskId, device.id)
    .run();
  return ok({});
}

// ---------------------------------------------------------------- agent task loop

export async function nextTask(req: Request, env: Env): Promise<Response> {
  const agent = await requireAgent(req, env);
  const now = isoNow();
  const lease = leaseSeconds(env);

  // 1) 收回租约过期的任务；最多重试 2 次。
  await env.DB.prepare(
    `UPDATE tasks
        SET status = 'queued', stage = 'pending', progress = 0,
            message = '电脑重连，等待重试', lease_expires_at = NULL, updated_at = ?
      WHERE agent_id = ? AND status = 'running' AND lease_expires_at IS NOT NULL
        AND lease_expires_at < ? AND attempts < 2`,
  )
    .bind(now, agent.id, now)
    .run();

  await env.DB.prepare(
    `UPDATE tasks
        SET status = 'failed', stage = 'failed', progress = 0,
            error_code = 'agent_lease_expired',
            error_message = '电脑处理超时，已自动重试 2 次仍失败',
            completed_at = ?, updated_at = ?, lease_expires_at = NULL
      WHERE agent_id = ? AND status = 'running' AND lease_expires_at IS NOT NULL
        AND lease_expires_at < ? AND attempts >= 2`,
  )
    .bind(now, now, agent.id, now)
    .run();

  // 2) 找最早一条 queued；再条件更新抢占，避免并发重复领取。
  const candidate = await env.DB.prepare(
    `SELECT t.id FROM tasks t JOIN devices d ON d.id = t.device_id
      WHERE t.agent_id = ? AND t.status = 'queued' AND d.revoked_at IS NULL
      ORDER BY t.created_at ASC LIMIT 1`,
  )
    .bind(agent.id)
    .first<{ id: string }>();
  if (!candidate) return ok({ task: null });

  const claimed = await env.DB.prepare(
    `UPDATE tasks
        SET status = 'running', stage = 'parsing', progress = 0, message = '电脑已接单',
            claimed_at = ?, updated_at = ?, lease_expires_at = ?, attempts = attempts + 1
      WHERE id = ? AND status = 'queued'`,
  )
    .bind(now, now, isoAfter(lease), candidate.id)
    .run();
  if (!claimed.meta || claimed.meta.changes !== 1) return ok({ task: null });

  const task = await env.DB.prepare(
    'SELECT id, url, platform, attempts, created_at, device_id FROM tasks WHERE id = ?',
  )
    .bind(candidate.id)
    .first();
  await notifyTaskSnapshot(env, agent.id, candidate.id);
  return ok({ task });
}

async function requireAgentTask(
  env: Env,
  agentId: string,
  taskId: string,
): Promise<{ id: string; status: string; message?: string }> {
  const row = await env.DB.prepare(
    'SELECT id, status, message FROM tasks WHERE id = ? AND agent_id = ?',
  )
    .bind(taskId, agentId)
    .first<{ id: string; status: string; message: string }>();
  if (!row) return taskNotFound();
  return row;
}

export async function progressTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const body = await readJson(req);
  const stage = oneOf(body.stage, PROGRESS_STAGES, 'parsing');
  const message = optString(body.message, 200);
  const task = await requireAgentTask(env, agent.id, taskId);

  if (task.status === 'succeeded' || task.status === 'failed') {
    throw new ApiError(409, 'invalid_state', '任务已经结束');
  }
  if (task.status !== 'running') {
    throw new ApiError(409, 'invalid_state', '任务还没有被领取');
  }

  const now = isoNow();
  if (body.progress == null) {
    await env.DB.prepare(
      `UPDATE tasks SET stage = ?, message = COALESCE(?, message), updated_at = ?, lease_expires_at = ?
        WHERE id = ? AND agent_id = ? AND status = 'running'`,
    )
      .bind(stage, message, now, isoAfter(leaseSeconds(env)), taskId, agent.id)
      .run();
  } else {
    const progress = optInt(body.progress, 0, 99, 0);
    await env.DB.prepare(
      `UPDATE tasks SET stage = ?, progress = ?, message = COALESCE(?, message),
                        updated_at = ?, lease_expires_at = ?
        WHERE id = ? AND agent_id = ? AND status = 'running'`,
    )
      .bind(stage, progress, message, now, isoAfter(leaseSeconds(env)), taskId, agent.id)
      .run();
  }
  await notifyTaskSnapshot(env, agent.id, taskId);
  return ok({ task_id: taskId, updated_at: now });
}

export async function completeTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const body = await readJson(req);
  const rawMarkdown = typeof body.markdown === 'string' ? body.markdown : '';
  // 实时架构：正文不写 D1；REST 的 markdown 只做「在线手机 best-effort 转发」兼容，
  // 正式链路是 Agent 通过 WS task_note（本地 outbox 支持 note_request 补发）。
  let markdown: string | null = null;
  if (rawMarkdown.trim()) {
    if (bytesOf(rawMarkdown) > 1_572_864) {
      throw new ApiError(413, 'markdown_too_large', '笔记超过 1.5 MiB，无法转发');
    }
    markdown = rawMarkdown;
  }

  const rawTitle = optString(body.title, 200) || '';
  const title = (rawTitle.replace(/[\r\n\t]+/g, ' ').trim() || '未命名笔记').slice(0, 200);
  let duration: number | null = null;
  if (body.duration_seconds != null) {
    const n = Number(body.duration_seconds);
    if (!Number.isFinite(n) || n < 0) bad('bad_request', 'duration_seconds 格式错误');
    duration = Math.min(864000, Math.trunc(n));
  }
  let meta: string | null = null;
  if (body.meta != null) {
    if (typeof body.meta !== 'object' || Array.isArray(body.meta)) {
      bad('bad_request', 'meta 必须是对象');
    }
    meta = JSON.stringify(body.meta);
    if (meta.length > 20_000) bad('bad_request', 'meta 太大');
  }

  const task = await requireAgentTask(env, agent.id, taskId);
  if (task.status === 'succeeded') return ok({ task_id: taskId, note_forwarded: false });
  if (task.status === 'failed') throw new ApiError(409, 'invalid_state', '任务已经失败');
  if (task.status !== 'running') throw new ApiError(409, 'invalid_state', '任务还没有被领取');

  const now = isoNow();
  await env.DB.prepare(
    `UPDATE tasks
        SET status = 'succeeded', stage = 'done', progress = 100, message = '已完成',
            title = ?, duration_seconds = ?, meta = ?,
            error_code = NULL, error_message = NULL,
            lease_expires_at = NULL, completed_at = ?, updated_at = ?
      WHERE id = ? AND agent_id = ? AND status = 'running'`,
  )
    .bind(title, duration, meta, now, now, taskId, agent.id)
    .run();

  await notifyTaskSnapshot(env, agent.id, taskId);
  let noteForwarded = false;
  if (markdown) {
    noteForwarded = await notifyAgentRoom(env, agent.id, {
      type: 'note_forward',
      task_id: taskId,
      markdown,
      title,
    });
  }
  return ok({ task_id: taskId, completed_at: now, note_forwarded: noteForwarded });
}

export async function failTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const body = await readJson(req);
  const code = (optString(body.code, 64) || 'agent_error').replace(/[^A-Za-z0-9_-]/g, '_');
  const message = (optString(body.message, 1000) || '电脑处理失败，请稍后重试').slice(0, 1000);
  const task = await requireAgentTask(env, agent.id, taskId);
  if (task.status === 'failed') return ok({ task_id: taskId });
  if (task.status === 'succeeded') throw new ApiError(409, 'invalid_state', '任务已经成功');

  const now = isoNow();
  await env.DB.prepare(
    `UPDATE tasks
        SET status = 'failed', stage = 'failed', progress = 0,
            error_code = ?, error_message = ?, lease_expires_at = NULL,
            completed_at = ?, updated_at = ?
      WHERE id = ? AND agent_id = ? AND status IN ('running', 'queued')`,
  )
    .bind(code, message, now, now, taskId, agent.id)
    .run();
  await notifyTaskSnapshot(env, agent.id, taskId);
  return ok({ task_id: taskId, completed_at: now });
}

export async function heartbeatTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const task = await requireAgentTask(env, agent.id, taskId);
  if (task.status !== 'running') throw new ApiError(409, 'invalid_state', '任务不在运行中');
  const expiresAt = isoAfter(leaseSeconds(env));
  await env.DB.prepare(
    `UPDATE tasks SET lease_expires_at = ?, updated_at = ?
      WHERE id = ? AND agent_id = ? AND status = 'running'`,
  )
    .bind(expiresAt, isoNow(), taskId, agent.id)
    .run();
  return ok({ lease_expires_at: expiresAt });
}

export async function getAgentTask(req: Request, env: Env, taskId: string): Promise<Response> {
  const agent = await requireAgent(req, env);
  const row = await env.DB.prepare(
    `SELECT ${TASK_SUMMARY_COLUMNS} FROM tasks WHERE id = ? AND agent_id = ?`,
  )
    .bind(taskId, agent.id)
    .first<Record<string, unknown>>();
  if (!row) return taskNotFound();
  return ok({ task: toTaskSummary(row) });
}

export { fail };

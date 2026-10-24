/** Relay 的共享类型。字段命名与 docs/api-contract.md 保持一致。 */

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  /** 每个 agent 一个 Durable Object（SQLite 后端，Hibernation WebSocket）。 */
  AGENT_ROOM: DurableObjectNamespace;
  /** Worker ↔ DO 内部调用凭证；生产用 `wrangler secret put`，本地用 `npm run dev`。 */
  INTERNAL_SECRET: string;
  /** 手机实时连接 ticket 的 HMAC 密钥；生产用 `wrangler secret put`，本地用 `npm run dev`。 */
  TICKET_SECRET: string;
  PUBLIC_BASE_URL: string;
  ALLOWED_ORIGINS: string;
  PAIRING_TTL_SECONDS: string;
  TASK_LEASE_SECONDS: string;
  /** 以下均为可选覆盖值，默认值满足 docs/realtime-protocol.md；测试时可调小。 */
  TICKET_TTL_SECONDS?: string;
  WS_ALARM_SECONDS?: string;
  WS_AGENT_STALE_SECONDS?: string;
  WS_PHONE_STALE_SECONDS?: string;
}

export type Platform = 'bilibili' | 'youtube' | 'douyin' | 'kuaishou';
export type AgentPlatform = 'windows' | 'macos' | 'linux';
export type DevicePlatform = 'android' | 'ios' | 'web';
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed';
export type TaskStage =
  | 'pending'
  | 'parsing'
  | 'downloading'
  | 'transcribing'
  | 'summarizing'
  | 'formatting'
  | 'saving'
  | 'done'
  | 'failed';

export interface AgentRow {
  id: string;
  name: string;
  platform: string;
  version: string;
  token_hash: string;
  created_at: string;
  last_seen_at: string | null;
}

export interface DeviceRow {
  id: string;
  agent_id: string;
  name: string;
  platform: string;
  token_hash: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

export interface DeviceWithAgent {
  device: DeviceRow;
  agent: AgentRow;
}

export interface PairingRow {
  id: string;
  agent_id: string;
  code_hash: string;
  created_at: string;
  expires_at: string;
  claimed_at: string | null;
  claimed_device_id: string | null;
  canceled_at: string | null;
}

export interface TaskRow {
  id: string;
  agent_id: string;
  device_id: string;
  client_key: string | null;
  url: string;
  platform: string;
  status: TaskStatus;
  stage: TaskStage;
  progress: number;
  message: string;
  title: string | null;
  /** 历史列：实时架构下不再写入，读取也不会返回正文。 */
  markdown?: string | null;
  error_code: string | null;
  error_message: string | null;
  duration_seconds: number | null;
  meta: string | null;
  attempts: number;
  lease_expires_at: string | null;
  created_at: string;
  updated_at: string;
  claimed_at: string | null;
  completed_at: string | null;
  archived_at: string | null;
}

/** 任务摘要（列表/详情用，不含 markdown）。 */
export interface TaskSummary {
  id: string;
  url: string;
  platform: string;
  status: string;
  stage: string;
  progress: number;
  message: string;
  title: string | null;
  error_code: string | null;
  error_message: string | null;
  duration_seconds: number | null;
  /** 任务是否已产出笔记。正文不保存在 Relay（经 WebSocket / 手机本地缓存）。 */
  has_note: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface AgentPublic {
  id: string;
  name: string;
  platform: string;
  version: string;
  online: boolean;
  last_seen_at: string | null;
  created_at: string;
}

export interface DevicePublic {
  id: string;
  name: string;
  platform: string;
  assigned_agent?: string;
  paired_at: string;
  last_seen_at: string | null;
}

// ---------------------------------------------------------------- realtime

/** Agent 通过 REST 新建任务后，Worker 推给 DO 的最小任务负载。 */
export interface AgentPushTask {
  id: string;
  url: string;
  platform: string;
  status: string;
  stage?: string;
  progress?: number;
  title: string | null;
  created_at: string;
}

/** Worker → DO 的内部事件（仅同账号 Worker 可调用，带 X-Internal-Secret）。 */
export type RoomEvent =
  | { type: 'task_created'; task: AgentPushTask }
  | { type: 'task_snapshot'; task: TaskSummary }
  | { type: 'note_forward'; task_id: string; markdown: string; title?: string | null }
  | { type: 'device_revoked'; device_id: string }
  | { type: 'ping' };

export interface AgentPresence {
  online: boolean;
  last_seen_at: string | null;
  /** true 表示该 agent 用过实时通道，online 以 DO socket 为准。 */
  realtime: boolean;
}

export interface RealtimeTicket {
  ticket: string;
  expires_at: string;
  agent_id: string;
  agent_online: boolean;
}

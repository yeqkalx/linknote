/** Relay 的共享类型。字段命名与 docs/api-contract.md 保持一致。 */

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  PUBLIC_BASE_URL: string;
  ALLOWED_ORIGINS: string;
  PAIRING_TTL_SECONDS: string;
  TASK_LEASE_SECONDS: string;
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
  paired_at: string;
  last_seen_at: string | null;
}

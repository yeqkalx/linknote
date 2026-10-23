/** D1 row → 契约中的公共对象。 */

import { isOnline } from './http';
import type {
  AgentPublic,
  AgentRow,
  DevicePublic,
  DeviceRow,
  TaskSummary,
} from '../types';

/** 任务摘要列：绝不把 markdown 带进列表/轮询。 */
export const TASK_SUMMARY_COLUMNS = `id, url, platform, status, stage, progress, message, title,
  error_code, error_message, duration_seconds, created_at, updated_at, completed_at,
  (CASE WHEN markdown IS NOT NULL AND length(markdown) > 0 THEN 1 ELSE 0 END) AS has_note`;

export function toTaskSummary(row: Record<string, unknown>): TaskSummary {
  return {
    id: String(row.id),
    url: String(row.url),
    platform: String(row.platform),
    status: String(row.status),
    stage: String(row.stage),
    progress: Number(row.progress ?? 0),
    message: String(row.message ?? ''),
    title: row.title == null ? null : String(row.title),
    error_code: row.error_code == null ? null : String(row.error_code),
    error_message: row.error_message == null ? null : String(row.error_message),
    duration_seconds: row.duration_seconds == null ? null : Number(row.duration_seconds),
    has_note: Boolean(row.has_note),
    created_at: String(row.created_at),
    updated_at: String(row.updated_at),
    completed_at: row.completed_at == null ? null : String(row.completed_at),
  };
}

export function toAgentPublic(row: AgentRow): AgentPublic {
  return {
    id: row.id,
    name: row.name,
    platform: row.platform,
    version: row.version,
    online: isOnline(row.last_seen_at),
    last_seen_at: row.last_seen_at,
    created_at: row.created_at,
  };
}

export function toDevicePublic(row: DeviceRow): DevicePublic {
  return {
    id: row.id,
    name: row.name,
    platform: row.platform,
    paired_at: row.created_at,
    last_seen_at: row.last_seen_at,
  };
}

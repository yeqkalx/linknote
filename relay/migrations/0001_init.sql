-- LinkNote relay schema v1
-- 所有时间都是 ISO 8601 UTC 字符串（可在 SQLite 里按字典序比较）。

CREATE TABLE IF NOT EXISTS agents (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  platform      TEXT NOT NULL DEFAULT 'windows',
  version       TEXT NOT NULL DEFAULT '',
  token_hash    TEXT NOT NULL UNIQUE,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS pairing_sessions (
  id                 TEXT PRIMARY KEY,
  agent_id           TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  code_hash          TEXT NOT NULL UNIQUE,
  created_at         TEXT NOT NULL,
  expires_at         TEXT NOT NULL,
  claimed_at         TEXT,
  claimed_device_id  TEXT REFERENCES devices(id) ON DELETE SET NULL,
  canceled_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_pairing_agent_active
  ON pairing_sessions(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS devices (
  id            TEXT PRIMARY KEY,
  agent_id      TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  platform      TEXT NOT NULL DEFAULT 'android',
  token_hash    TEXT NOT NULL UNIQUE,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT,
  revoked_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_devices_agent ON devices(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tasks (
  id                TEXT PRIMARY KEY,
  agent_id          TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  device_id         TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  client_key        TEXT,
  url               TEXT NOT NULL,
  platform          TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  stage             TEXT NOT NULL DEFAULT 'pending'
                    CHECK (stage IN ('pending', 'parsing', 'downloading', 'transcribing',
                                     'summarizing', 'formatting', 'saving', 'done', 'failed')),
  progress          INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  message           TEXT NOT NULL DEFAULT '',
  title             TEXT,
  markdown          TEXT,
  error_code        TEXT,
  error_message     TEXT,
  duration_seconds  INTEGER,
  meta              TEXT,
  attempts          INTEGER NOT NULL DEFAULT 0,
  lease_expires_at  TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  claimed_at        TEXT,
  completed_at      TEXT,
  archived_at       TEXT,
  UNIQUE (device_id, client_key)
);

CREATE INDEX IF NOT EXISTS idx_tasks_agent_queue
  ON tasks(agent_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_device_list
  ON tasks(device_id, archived_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_running
  ON tasks(agent_id, status, lease_expires_at);

// E2E 辅助：用 Agent 配对码领取设备、创建真实任务，保存 session 供后续步骤使用。
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const home = process.env.LINKNOTE_HOME || path.join(root, 'pc-agent', '.e2e');
const cfg = JSON.parse(readFileSync(path.join(home, 'agent.json'), 'utf8'));
const base = `${cfg.relay_base_url.replace(/\/$/, '')}/api/v1`;
const outDir = path.join(root, 'assets', 'e2e');
mkdirSync(outDir, { recursive: true });

async function api(method, route, { token, body } = {}) {
  const res = await fetch(base + route, {
    method,
    headers: {
      ...(body ? { 'content-type': 'application/json; charset=utf-8' } : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json();
  if (!res.ok || json.ok !== true) {
    throw new Error(`${method} ${route} -> ${res.status} ${JSON.stringify(json)}`);
  }
  return json.data;
}

const claim = await api('POST', '/pairings/claim', {
  body: { code: cfg.pairing_code, device_name: 'E2E 测试手机', platform: 'android' },
});

const task = await api('POST', '/tasks', {
  token: claim.device_token,
  body: {
    url: 'https://www.bilibili.com/video/BV1GJ411x7h7',
    client_key: `e2e-real-${Date.now().toString(36)}`,
  },
});

const session = {
  relay: cfg.relay_base_url,
  agent_id: cfg.agent_id,
  agent_name: cfg.agent_name,
  agent_token: cfg.agent_token,
  device_id: claim.device_id,
  device_name: claim.device_name || 'E2E 测试手机',
  device_token: claim.device_token,
  task_id: task.task.id,
  url: task.task.url,
  created_at: task.task.created_at,
};
writeFileSync(path.join(outDir, 'session.json'), JSON.stringify(session, null, 2), 'utf8');
writeFileSync(path.join(home, 'e2e-session.json'), JSON.stringify(session, null, 2), 'utf8');
console.log(JSON.stringify({ task: task.task, agent: claim.agent, device_id: claim.device_id }, null, 2));

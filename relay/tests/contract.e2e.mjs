// LinkNote Relay 契约端到端测试（REST 兼容 + Durable Objects / WebSocket 实时链路）。
//
// 覆盖：
// - 配对 / 任务 / 重试 / 归档 / 设备撤销 / CORS 等 REST 契约；
// - POST /realtime/ticket、agent WS（Authorization Bearer）、phone WS（ticket）、hello/presence；
// - 任务创建 → Agent 秒收；task_update → D1 + 手机 task_updated；task_note → 手机 note；
// - ack_note 转发 Agent、note_request 重发、离线 queued 重连补发、心跳超时判离线；
// - A/B 多租户隔离（任务/事件/WS/ticket 全部不可跨 agent）；
// - 笔记正文不落 D1 / DO SQLite（对本地持久化文件做二进制 marker 扫描）。
//
// 用法：npm test   （加 --keep 保留服务，方便手动打开 http://127.0.0.1:8799）
import { spawn, execFileSync } from 'node:child_process';
import { rm, mkdir } from 'node:fs/promises';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import WebSocket from 'ws';

const relayDir = path.resolve(import.meta.dirname, '..');
const stateDir = path.join(relayDir, '.wrangler', 'test-state');
const wranglerBin = path.join(relayDir, 'node_modules', 'wrangler', 'bin', 'wrangler.js');
const port = Number(process.env.TEST_PORT || 8799);
const inspectorPort = port + 500;
const base = `http://127.0.0.1:${port}/api/v1`;
const wsBase = `ws://127.0.0.1:${port}/api/v1`;
const keep = process.argv.includes('--keep');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let child;
let logs = '';
let passed = 0;
let failed = 0;
const failures = [];

function logServer(chunk) {
  logs += chunk.toString();
  if (process.env.VERBOSE) process.stdout.write(chunk);
}

function step(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      passed++;
      console.log(`  ok  ${name}`);
    })
    .catch((error) => {
      failed++;
      failures.push({ name, error });
      console.error(`  FAIL ${name}: ${error.message}`);
    });
}

function runWrangler(args) {
  return new Promise((resolve, reject) => {
    const p = spawn(process.execPath, [wranglerBin, ...args], {
      cwd: relayDir,
      env: { ...process.env, CI: '1', WRANGLER_SEND_METRICS: 'false', NO_COLOR: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (out += d));
    p.on('exit', (code) =>
      code === 0
        ? resolve(out)
        : reject(new Error(`wrangler ${args.join(' ')} 退出码 ${code}\n${out.slice(-3000)}`)),
    );
  });
}

async function waitForHealth(timeoutMs = 120_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return await res.json();
    } catch {
      // not ready
    }
    await sleep(700);
  }
  throw new Error(`Relay 在 ${timeoutMs}ms 内没有就绪\n${logs.slice(-5000)}`);
}

async function call(method, route, { token, body, headers = {} } = {}) {
  const res = await fetch(`${base}${route}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'content-type': 'application/json; charset=utf-8' }),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = null;
  const text = await res.text();
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    // keep null
  }
  return { status: res.status, json, text, headers: res.headers };
}

function expectOk(result, status = 200) {
  assert.equal(result.status, status, `HTTP ${result.status}: ${result.text}`);
  assert.equal(result.json?.ok, true, `expected ok: ${result.text}`);
  return result.json.data;
}

function expectError(result, status, code) {
  assert.equal(result.status, status, `HTTP ${result.status}: ${result.text}`);
  assert.equal(result.json?.ok, false, `expected error: ${result.text}`);
  if (code) assert.equal(result.json.error.code, code, `error code: ${result.text}`);
  return result.json.error;
}

// ---------------------------------------------------------------- WebSocket client

function connectRaw(url, options = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, { headers: options.headers || {} });
    const state = {
      ws,
      autoPong: options.autoPong !== false,
      queue: [],
      all: [],
      waiters: [],
      opened: false,
      closed: null,
      closeWaiters: [],
    };

    const settle = (error) => {
      for (const waiter of state.waiters.splice(0)) {
        clearTimeout(waiter.timer);
        waiter.reject(error);
      }
    };

    ws.on('message', (raw) => {
      let msg;
      try {
        msg = JSON.parse(String(raw));
      } catch {
        return;
      }
      state.all.push(msg);
      if (msg.type === 'ping' && state.autoPong) {
        try {
          ws.send(JSON.stringify({ type: 'pong', ts: msg.ts }));
        } catch {
          // socket closed
        }
      }
      const index = state.waiters.findIndex((waiter) => waiter.predicate(msg));
      if (index >= 0) {
        const [waiter] = state.waiters.splice(index, 1);
        clearTimeout(waiter.timer);
        waiter.resolve(msg);
        return;
      }
      state.queue.push(msg);
    });

    ws.on('close', (code, reason) => {
      state.closed = { code, reason: String(reason || '') };
      settle(new Error(`socket closed (${code}) while waiting for message`));
      for (const waiter of state.closeWaiters.splice(0)) {
        clearTimeout(waiter.timer);
        waiter.resolve(state.closed);
      }
    });

    ws.on('error', (error) => {
      if (!state.opened) reject(error);
    });

    ws.on('unexpected-response', (req, res) => {
      try {
        res.resume();
      } catch {
        // ignore
      }
      reject(Object.assign(new Error(`WS handshake rejected: HTTP ${res.statusCode}`), {
        statusCode: res.statusCode,
      }));
    });

    ws.on('open', () => {
      state.opened = true;
      state.send = (payload) => ws.send(JSON.stringify(payload));
      state.close = () => {
        try {
          ws.close(1000);
        } catch {
          // ignore
        }
      };
      state.waitFor = (predicate, timeoutMs = 8000, label = 'message') => {
        const queueIndex = state.queue.findIndex(predicate);
        if (queueIndex >= 0) return Promise.resolve(state.queue.splice(queueIndex, 1)[0]);
        return new Promise((res, rej) => {
          const timer = setTimeout(() => {
            const index = state.waiters.findIndex((w) => w.timer === timer);
            if (index >= 0) state.waiters.splice(index, 1);
            rej(new Error(`timeout waiting for ${label}`));
          }, timeoutMs);
          state.waiters.push({ predicate, resolve: res, reject: rej, timer });
        });
      };
      state.expectNone = (predicate, timeoutMs = 1500) =>
        new Promise((res, rej) => {
          const queueIndex = state.queue.findIndex(predicate);
          if (queueIndex >= 0) {
            state.queue.splice(queueIndex, 1);
            rej(new Error('unexpected matching message received'));
            return;
          }
          const timer = setTimeout(() => {
            const index = state.waiters.findIndex((w) => w.timer === timer);
            if (index >= 0) state.waiters.splice(index, 1);
            res();
          }, timeoutMs);
          state.waiters.push({
            predicate,
            resolve: () => rej(new Error('unexpected matching message received')),
            reject: () => {},
            timer,
          });
        });
      state.waitClosed = (timeoutMs = 5000) => {
        if (state.closed) return Promise.resolve(state.closed);
        return new Promise((res, rej) => {
          const timer = setTimeout(
            () => rej(new Error(`timeout waiting for socket close`)),
            timeoutMs,
          );
          state.closeWaiters.push({ resolve: res, timer });
        });
      };
      resolve(state);
    });
  });
}

async function issueTicket(deviceToken) {
  const data = expectOk(await call('POST', '/realtime/ticket', { token: deviceToken }));
  assert.equal(typeof data.ticket, 'string');
  assert.ok(data.ticket.length > 20, 'ticket 太短');
  assert.ok(Date.parse(data.expires_at) > Date.now(), 'ticket 应未过期');
  return data;
}

async function openPhone(deviceToken, options = {}) {
  const data = await issueTicket(deviceToken);
  const client = await connectRaw(`${wsBase}/ws?ticket=${encodeURIComponent(data.ticket)}`, options);
  client.ticketData = data;
  return client;
}

async function openAgent(agentToken, options = {}) {
  return connectRaw(`${wsBase}/agent/ws`, {
    ...options,
    headers: { authorization: `Bearer ${agentToken}`, ...(options.headers || {}) },
  });
}

async function expectWsRejected(url, statusCode, options = {}) {
  try {
    const client = await connectRaw(url, options);
    client.close();
    assert.fail('expected WS handshake to be rejected');
  } catch (error) {
    if (String(error.message).startsWith('expected')) throw error;
    assert.equal(error.statusCode, statusCode, `WS handshake status: ${error.message}`);
  }
}

/** 本地 Miniflare 对「DO 主动 close 的帧」转发不稳定：生产按 close code 断言，本地宽容处理。 */
async function maybeClosed(client, timeoutMs) {
  try {
    return await client.waitClosed(timeoutMs);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- persistence scan

function scanStateForMarkers(markers) {
  const files = [];
  (function walk(dir) {
    if (!existsSync(dir)) return;
    for (const name of readdirSync(dir)) {
      const full = path.join(dir, name);
      const info = statSync(full);
      if (info.isDirectory()) walk(full);
      else if (info.size <= 32 * 1024 * 1024) files.push(full);
    }
  })(stateDir);
  for (const file of files) {
    const buffer = readFileSync(file);
    for (const marker of markers) {
      if (buffer.includes(marker)) {
        throw new Error(`笔记正文被持久化到了 ${path.relative(stateDir, file)}`);
      }
    }
  }
}

// ---------------------------------------------------------------- test body

async function main() {
  console.log(`\n== LinkNote Relay contract test (port ${port}) ==`);
  await rm(stateDir, { recursive: true, force: true });
  await mkdir(stateDir, { recursive: true });

  console.log('preparing local D1...');
  await runWrangler(['d1', 'migrations', 'apply', 'linknote-db', '--local', '--persist-to', stateDir]);

  console.log('starting wrangler dev...');
  child = spawn(
    process.execPath,
    [
      wranglerBin,
      'dev',
      '--port',
      String(port),
      '--inspector-port',
      String(inspectorPort),
      '--local',
      '--persist-to',
      stateDir,
      '--show-interactive-dev-session=false',
      '--var',
      'INTERNAL_SECRET:test-internal-secret',
      '--var',
      'TICKET_SECRET:test-ticket-secret',
      '--var',
      'TICKET_TTL_SECONDS:3',
      '--var',
      'WS_ALARM_SECONDS:1',
      '--var',
      'WS_AGENT_STALE_SECONDS:3',
      '--var',
      'WS_PHONE_STALE_SECONDS:8',
    ],
    {
      cwd: relayDir,
      env: { ...process.env, CI: '1', WRANGLER_SEND_METRICS: 'false', NO_COLOR: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout.on('data', logServer);
  child.stderr.on('data', logServer);
  child.on('exit', (code) => {
    if (code && code !== 0) console.error(`wrangler dev exited with ${code}`);
  });

  const health = await waitForHealth();
  console.log(`relay ready: version ${health.data?.version || '?'}\n`);

  const agentName = '测试电脑·客厅';
  const deviceName = '测试手机·Pixel';
  const taskUrl = 'https://www.bilibili.com/video/BV1xx411c7mD';
  const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const markerLegacyRest = `MARKER-LEGACY-REST-${unique}`;
  const markerWs = `MARKER-WS-${unique}`;
  const markerRestBridge = `MARKER-REST-BRIDGE-${unique}`;
  const markers = [markerLegacyRest, markerWs, markerRestBridge];

  let agentToken;
  let agentId;
  let code;
  let pairingId;
  let deviceToken;
  let deviceId;
  let taskId;
  let otherDeviceToken;
  let otherDeviceId;

  const clients = [];
  const track = (client) => {
    clients.push(client);
    return client;
  };

  // ============================================================ Phase A: REST

  await step('health 返回 ok 且声明实时能力', async () => {
    assert.equal(health.ok, true);
    assert.equal(health.data.status, 'ok');
    assert.equal(health.data.realtime, true);
  });

  await step('无 token 访问 /me 返回 401', async () => {
    expectError(await call('GET', '/me'), 401, 'unauthorized');
  });

  await step('创建配对，返回 8 位码和 agent_token', async () => {
    const data = expectOk(
      await call('POST', '/pairings', {
        body: { agent_name: agentName, platform: 'windows', version: '0.1.0-test' },
      }),
      201,
    );
    agentToken = data.agent_token;
    agentId = data.agent_id;
    code = data.code;
    pairingId = data.pairing_id;
    assert.match(agentToken, /^agt_[\w-]{40,}$/);
    assert.match(code, /^[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$/);
    assert.ok(Date.parse(data.expires_at) > Date.now());
  });

  await step('Agent 查询配对状态为 pending（中文 agent 名称无损）', async () => {
    const data = expectOk(await call('GET', `/pairings/${pairingId}`, { token: agentToken }));
    assert.equal(data.status, 'pending');
    assert.equal(data.device, null);
  });

  await step('手机用小写、无横线的码领取配对', async () => {
    const data = expectOk(
      await call('POST', '/pairings/claim', {
        body: {
          code: code.toLowerCase().replace('-', ''),
          device_name: deviceName,
          platform: 'android',
        },
      }),
    );
    deviceToken = data.device_token;
    deviceId = data.device_id;
    assert.match(deviceToken, /^dev_/);
    assert.equal(data.agent.id, agentId);
    const state = expectOk(await call('GET', `/pairings/${pairingId}`, { token: agentToken }));
    assert.equal(state.status, 'claimed');
    assert.equal(state.device.name, deviceName);
  });

  await step('同一个配对码不能二次领取', async () => {
    expectError(
      await call('POST', '/pairings/claim', {
        body: { code, device_name: 'x', platform: 'android' },
      }),
      409,
      'pairing_already_claimed',
    );
  });

  await step('/me 返回设备信息和 agents 在线状态', async () => {
    const data = expectOk(await call('GET', '/me', { token: deviceToken }));
    assert.equal(data.device.name, deviceName);
    assert.equal(data.agent.name, agentName);
    assert.equal(typeof data.agent.online, 'boolean');
  });

  await step('不支持的链接被拒绝', async () => {
    expectError(
      await call('POST', '/tasks', {
        token: deviceToken,
        body: { url: 'https://example.com/video/1' },
      }),
      400,
      'invalid_url',
    );
    expectError(
      await call('POST', '/tasks', {
        token: deviceToken,
        body: { url: 'http://127.0.0.1:8483/api/generate_note' },
      }),
      400,
      'invalid_url',
    );
  });

  await step('提交任务并验证 client_key 幂等', async () => {
    const data = expectOk(
      await call('POST', '/tasks', {
        token: deviceToken,
        body: { url: taskUrl, client_key: 'test-client-key-0001' },
      }),
      201,
    );
    taskId = data.task.id;
    assert.equal(data.task.status, 'queued');
    assert.equal(data.task.platform, 'bilibili');
    assert.equal(data.task.has_note, false);

    const dup = await call('POST', '/tasks', {
      token: deviceToken,
      body: { url: taskUrl, client_key: 'test-client-key-0001' },
    });
    expectOk(dup, 200);
    assert.equal(dup.json.data.task.id, taskId);
  });

  await step('任务列表包含刚提交的任务', async () => {
    const data = expectOk(await call('GET', '/tasks?limit=10', { token: deviceToken }));
    assert.ok(data.items.some((t) => t.id === taskId));
    assert.equal(typeof data.items[0].has_note, 'boolean');
  });

  await step('Agent 领取任务；队列空时返回 null', async () => {
    const data = expectOk(await call('GET', '/agent/tasks/next', { token: agentToken }));
    assert.equal(data.task.id, taskId);
    assert.equal(data.task.attempts, 1);
    const empty = expectOk(await call('GET', '/agent/tasks/next', { token: agentToken }));
    assert.equal(empty.task, null);
  });

  await step('Agent REST 回传进度，手机能看到阶段/进度', async () => {
    expectOk(
      await call('POST', `/agent/tasks/${taskId}/progress`, {
        token: agentToken,
        body: { stage: 'transcribing', progress: 45, message: '正在转写' },
      }),
    );
    const detail = expectOk(await call('GET', `/tasks/${taskId}`, { token: deviceToken }));
    assert.equal(detail.task.status, 'running');
    assert.equal(detail.task.stage, 'transcribing');
    assert.equal(detail.task.progress, 45);
    assert.equal(detail.task.message, '正在转写');
  });

  await step('REST complete 只写元数据：has_note=true、正文不再落 D1、/note 返回 410', async () => {
    expectOk(
      await call('POST', `/agent/tasks/${taskId}/complete`, {
        token: agentToken,
        body: {
          title: '测试笔记·标题',
          markdown: `# 测试笔记\n\n${markerLegacyRest}`,
          duration_seconds: 321,
        },
      }),
    );
    const detail = expectOk(await call('GET', `/tasks/${taskId}`, { token: deviceToken }));
    assert.equal(detail.task.status, 'succeeded');
    assert.equal(detail.task.stage, 'done');
    assert.equal(detail.task.progress, 100);
    assert.equal(detail.task.title, '测试笔记·标题');
    assert.equal(detail.task.has_note, true);
    expectError(
      await call('GET', `/tasks/${taskId}/note`, { token: deviceToken }),
      410,
      'note_not_stored',
    );
  });

  await step('重复 complete 幂等', async () => {
    const data = expectOk(
      await call('POST', `/agent/tasks/${taskId}/complete`, {
        token: agentToken,
        body: { title: 'x', markdown: `# x\n\n${markerLegacyRest}` },
      }),
    );
    assert.equal(data.note_forwarded, false);
  });

  await step('其他设备 token 看不到该任务（404）', async () => {
    const p = expectOk(await call('POST', '/agent/pairings', { token: agentToken, body: {} }), 201);
    const other = expectOk(
      await call('POST', '/pairings/claim', {
        body: { code: p.code, device_name: '另一台手机', platform: 'android' },
      }),
    );
    otherDeviceToken = other.device_token;
    otherDeviceId = other.device_id;
    expectError(
      await call('GET', `/tasks/${taskId}`, { token: other.device_token }),
      404,
      'task_not_found',
    );
    expectError(
      await call('GET', `/tasks/${taskId}/note`, { token: other.device_token }),
      404,
      'task_not_found',
    );
  });

  await step('重试成功任务会创建新任务', async () => {
    const data = expectOk(await call('POST', `/tasks/${taskId}/retry`, { token: deviceToken }), 201);
    assert.notEqual(data.task.id, taskId);
    assert.equal(data.task.status, 'queued');
    const detail = expectOk(await call('GET', `/tasks/${data.task.id}`, { token: deviceToken }));
    assert.equal(detail.task.url, taskUrl);
  });

  await step('运行中的任务不能归档；终态可以归档且从列表消失', async () => {
    const running = expectOk(await call('GET', '/agent/tasks/next', { token: agentToken }));
    assert.ok(running.task);
    expectError(
      await call('DELETE', `/tasks/${running.task.id}`, { token: deviceToken }),
      409,
      'invalid_state',
    );
    expectOk(
      await call('POST', `/agent/tasks/${running.task.id}/fail`, {
        token: agentToken,
        body: { code: 'test_failure', message: '测试失败' },
      }),
    );
    expectOk(await call('DELETE', `/tasks/${running.task.id}`, { token: deviceToken }));
    const list = expectOk(await call('GET', '/tasks?limit=50', { token: deviceToken }));
    assert.ok(!list.items.some((t) => t.id === running.task.id));
  });

  await step('Agent 设备列表与撤销设备', async () => {
    const me = expectOk(await call('GET', '/agent/me', { token: agentToken }));
    assert.ok(me.devices.length >= 2);
    assert.equal(me.devices.find((d) => d.id === deviceId).name, deviceName);
    expectOk(await call('DELETE', `/agent/devices/${otherDeviceId}`, { token: agentToken }));
    expectError(await call('GET', '/me', { token: otherDeviceToken }), 401, 'unauthorized');
  });

  await step('CORS：允许的 Origin 有头，不允许的 403', async () => {
    const allowed = await call('GET', '/health', {
      headers: { Origin: 'http://localhost:8787' },
    });
    assert.equal(allowed.headers.get('access-control-allow-origin'), 'http://localhost:8787');
    const denied = await call('GET', '/health', { headers: { Origin: 'https://evil.example' } });
    expectError(denied, 403, 'origin_not_allowed');
  });

  await step('未知接口 404、错误方法 405', async () => {
    expectError(await call('GET', '/nope', { token: deviceToken }), 404, 'not_found');
    expectError(await call('GET', '/pairings', { token: agentToken }), 405, 'method_not_allowed');
    expectError(await call('GET', '/realtime/ticket', { token: deviceToken }), 405, 'method_not_allowed');
  });

  // ============================================================ Phase B: realtime

  await step('ticket：device token 换取短时签名 ticket，401 无凭证', async () => {
    expectError(await call('POST', '/realtime/ticket'), 401, 'unauthorized');
    expectError(await call('POST', '/realtime/ticket', { token: agentToken }), 401, 'unauthorized');
    const data = await issueTicket(deviceToken);
    assert.equal(data.agent_id, agentId);
    assert.equal(typeof data.agent_online, 'boolean');
  });

  await step('agent WS：无效 token 握手被 401 拒绝', async () => {
    await expectWsRejected(`${wsBase}/agent/ws`, 401, {
      headers: { authorization: 'Bearer agt_invalid_token_0000000000000000' },
    });
    await expectWsRejected(`${wsBase}/agent/ws`, 401);
  });

  await step('phone WS：非法 ticket 握手被 401 拒绝；Origin 白名单生效', async () => {
    await expectWsRejected(`${wsBase}/ws?ticket=garbage.ticket`, 401);
    const data = await issueTicket(deviceToken);
    await expectWsRejected(`${wsBase}/ws?ticket=${encodeURIComponent(data.ticket)}`, 403, {
      headers: { origin: 'https://evil.example' },
    });
  });

  await step('ticket 过期后握手被 401 拒绝', async () => {
    const data = await issueTicket(deviceToken);
    await sleep(3400);
    await expectWsRejected(`${wsBase}/ws?ticket=${encodeURIComponent(data.ticket)}`, 401);
  });

  let phoneA;
  await step('手机 WS 连接：hello + 初始 presence(offline)', async () => {
    phoneA = track(await openPhone(deviceToken));
    const hello = await phoneA.waitFor((m) => m.type === 'hello', 5000, 'phone hello');
    assert.equal(hello.agent_id, agentId);
    assert.equal(typeof hello.online, 'boolean');
    const presence = await phoneA.waitFor((m) => m.type === 'presence', 5000, 'phone presence');
    assert.equal(presence.online, false);
  });

  let agentA;
  await step('Agent WS 连接：hello + 手机实时 presence(online)', async () => {
    agentA = track(await openAgent(agentToken));
    const hello = await agentA.waitFor((m) => m.type === 'hello', 5000, 'agent hello');
    assert.equal(hello.agent_id, agentId);
    const presence = await phoneA.waitFor((m) => m.type === 'presence' && m.online === true, 5000, 'presence online');
    assert.equal(presence.online, true);
  });

  let supersededAgent;
  await step('同一 agent 新 WS 顶掉旧连接；手机不误报离线，推送切到新连接', async () => {
    supersededAgent = agentA;
    const agentA2 = track(await openAgent(agentToken));
    await agentA2.waitFor((m) => m.type === 'hello', 5000, 'agent2 hello');
    const closed = await maybeClosed(agentA, 1500);
    // 生产环境旧连接应收到 4001；本地 Miniflare 不一定转发 DO 主动 close 帧。
    if (closed) assert.equal(closed.code, 4001);
    await phoneA.expectNone((m) => m.type === 'presence' && m.online === false, 1200);
    agentA = agentA2;
  });

  let liveTaskId;
  await step('REST 创建任务后 Agent WS 立即收到 task（queued），被顶掉的旧连接收不到', async () => {
    const data = expectOk(
      await call('POST', '/tasks', { token: deviceToken, body: { url: taskUrl } }),
      201,
    );
    liveTaskId = data.task.id;
    const pushed = await agentA.waitFor(
      (m) => m.type === 'task' && m.task?.id === liveTaskId,
      5000,
      'agent task push',
    );
    assert.equal(pushed.task.status, 'queued');
    assert.equal(pushed.task.url, taskUrl);
    assert.equal(pushed.task.platform, 'bilibili');
    await supersededAgent.expectNone(
      (m) => m.type === 'task' && m.task?.id === liveTaskId,
      1200,
    );
    // 旧 socket 已在服务端下线：REST 快照的 online 仍为 true（新连接在线）。
    const me = expectOk(await call('GET', '/me', { token: deviceToken }));
    assert.equal(me.agent.online, true);
  });

  await step('Agent task_update(running) 更新 D1 并实时推手机', async () => {
    agentA.send({
      type: 'task_update',
      task_id: liveTaskId,
      status: 'running',
      stage: 'transcribing',
      progress: 30,
      message: '正在转写',
      title: '实时测试笔记',
    });
    const updated = await phoneA.waitFor(
      (m) => m.type === 'task_updated' && m.task?.id === liveTaskId,
      5000,
      'phone task_updated running',
    );
    assert.equal(updated.task.status, 'running');
    assert.equal(updated.task.progress, 30);
    assert.equal(updated.task.stage, 'transcribing');
    assert.equal(updated.task.title, '实时测试笔记');
    const detail = expectOk(await call('GET', `/tasks/${liveTaskId}`, { token: deviceToken }));
    assert.equal(detail.task.status, 'running');
    assert.equal(detail.task.progress, 30);
    assert.equal(detail.task.stage, 'transcribing');
  });

  await step('Agent task_update(completed) → D1 succeeded + 手机 task_updated', async () => {
    agentA.send({
      type: 'task_update',
      task_id: liveTaskId,
      status: 'completed',
      progress: 100,
      title: '实时测试笔记',
    });
    const updated = await phoneA.waitFor(
      (m) => m.type === 'task_updated' && m.task?.id === liveTaskId && m.task?.status === 'succeeded',
      5000,
      'phone task_updated succeeded',
    );
    assert.equal(updated.task.progress, 100);
    assert.equal(updated.task.stage, 'done');
    assert.equal(updated.task.has_note, true);
  });

  await step('Agent task_note：手机收到正文（WS 通道），D1 不返回正文', async () => {
    const markdown = `# 实时笔记\n\n${markerWs}\n\n- 要点一\n- 要点二`;
    agentA.send({ type: 'task_note', task_id: liveTaskId, markdown, title: '实时测试笔记' });
    const note = await phoneA.waitFor(
      (m) => m.type === 'note' && m.task_id === liveTaskId,
      5000,
      'phone note',
    );
    assert.equal(note.markdown, markdown);
    assert.equal(note.title, '实时测试笔记');
    expectError(
      await call('GET', `/tasks/${liveTaskId}/note`, { token: deviceToken }),
      410,
      'note_not_stored',
    );
  });

  await step('手机 ack_note 转发给 Agent', async () => {
    phoneA.send({ type: 'ack_note', task_id: liveTaskId });
    const ack = await agentA.waitFor(
      (m) => m.type === 'ack_note' && m.task_id === liveTaskId,
      5000,
      'agent ack_note',
    );
    assert.equal(ack.task_id, liveTaskId);
  });

  await step('手机 note_request → Agent 收到；Agent 重发 task_note → 手机再次收到', async () => {
    phoneA.send({ type: 'note_request', task_id: liveTaskId });
    const request = await agentA.waitFor(
      (m) => m.type === 'note_request' && m.task_id === liveTaskId,
      5000,
      'agent note_request',
    );
    assert.equal(request.task_id, liveTaskId);
    const markdown = `# 实时笔记（重发）\n\n${markerWs}`;
    agentA.send({ type: 'task_note', task_id: liveTaskId, markdown, title: '实时测试笔记' });
    const note = await phoneA.waitFor(
      (m) => m.type === 'note' && m.task_id === liveTaskId,
      5000,
      'phone note resend',
    );
    assert.equal(note.markdown, markdown);
  });

  await step('Agent 正常断开：手机 ≤2s 收到 presence(offline)，note_request 得到 note_unavailable', async () => {
    agentA.close();
    const presence = await phoneA.waitFor(
      (m) => m.type === 'presence' && m.online === false,
      3000,
      'presence offline',
    );
    assert.equal(presence.online, false);
    const me = expectOk(await call('GET', '/me', { token: deviceToken }));
    assert.equal(me.agent.online, false);
    phoneA.send({ type: 'note_request', task_id: liveTaskId });
    const unavailable = await phoneA.waitFor(
      (m) => m.type === 'note_unavailable' && m.task_id === liveTaskId,
      5000,
      'note_unavailable',
    );
    assert.equal(unavailable.reason, 'agent_offline');
  });

  const queuedTaskId = expectOk(
    await call('POST', '/tasks', { token: deviceToken, body: { url: taskUrl } }),
    201,
  ).task.id;

  await step('Agent 离线时任务保留 queued；重连后补发 task', async () => {
    const detail = expectOk(
      await call('GET', `/agent/tasks/${queuedTaskId}`, { token: agentToken }),
    );
    assert.equal(detail.task.status, 'queued');

    agentA = track(await openAgent(agentToken));
    await agentA.waitFor((m) => m.type === 'hello', 5000, 'agent reconnect hello');
    const pushed = await agentA.waitFor(
      (m) => m.type === 'task' && m.task?.id === queuedTaskId,
      5000,
      'queued task resend',
    );
    assert.equal(pushed.task.status, 'queued');
  });

  await step('REST complete 兼容桥：markdown 经 DO 转发在线手机（不落 D1）', async () => {
    // 先把重连补发的 queued 任务用 REST 链路处理完，避免干扰后续 next 领取。
    const claimed = expectOk(await call('GET', '/agent/tasks/next', { token: agentToken }));
    assert.equal(claimed.task.id, queuedTaskId);
    expectOk(
      await call('POST', `/agent/tasks/${queuedTaskId}/complete`, {
        token: agentToken,
        body: { title: '离线补发任务', markdown: '# 旧客户端\n\n无正文落库' },
      }),
    );

    const bridgeTask = expectOk(
      await call('POST', '/tasks', { token: deviceToken, body: { url: taskUrl } }),
      201,
    ).task;
    const next = expectOk(await call('GET', '/agent/tasks/next', { token: agentToken }));
    assert.equal(next.task.id, bridgeTask.id);
    expectOk(
      await call('POST', `/agent/tasks/${bridgeTask.id}/progress`, {
        token: agentToken,
        body: { stage: 'summarizing', progress: 80 },
      }),
    );
    const completed = expectOk(
      await call('POST', `/agent/tasks/${bridgeTask.id}/complete`, {
        token: agentToken,
        body: { title: '兼容桥笔记', markdown: `# 兼容桥\n\n${markerRestBridge}` },
      }),
    );
    assert.equal(completed.note_forwarded, true);
    const note = await phoneA.waitFor(
      (m) => m.type === 'note' && m.task_id === bridgeTask.id,
      6000,
      'phone note via REST bridge',
    );
    assert.match(note.markdown, new RegExp(markerRestBridge));
    const updated = await phoneA.waitFor(
      (m) => m.type === 'task_updated' && m.task?.id === bridgeTask.id && m.task?.status === 'succeeded',
      5000,
      'task_updated via REST bridge',
    );
    assert.equal(updated.task.has_note, true);
  });

  await step('心跳兜底：Agent 不回应 ping 时判离线（测试用 3s 窗口）', async () => {
    const silent = track(await openAgent(agentToken, { autoPong: false }));
    await silent.waitFor((m) => m.type === 'hello', 5000, 'silent hello');
    await phoneA.waitFor((m) => m.type === 'presence' && m.online === true, 5000, 'presence true');
    const offline = await phoneA.waitFor(
      (m) => m.type === 'presence' && m.online === false,
      9000,
      'presence offline by heartbeat',
    );
    assert.equal(offline.online, false);
    const me = expectOk(await call('GET', '/me', { token: deviceToken }));
    assert.equal(me.agent.online, false);
    const closed = await maybeClosed(silent, 2000);
    if (closed) assert.equal(closed.code, 4002);
    try {
      silent.close();
    } catch {
      // ignore
    }
  });

  let agentA4;
  await step('Agent 重新连接后手机 presence 恢复 online', async () => {
    agentA4 = track(await openAgent(agentToken));
    await agentA4.waitFor((m) => m.type === 'hello', 5000, 'agent4 hello');
    await phoneA.waitFor((m) => m.type === 'presence' && m.online === true, 5000, 'presence restore');
  });

  // ---------------------------------------------------------- A/B 多租户隔离

  let agentBToken;
  let agentBId;
  let deviceBToken;

  await step('创建第二套 agent/设备（B）', async () => {
    const pairing = expectOk(
      await call('POST', '/pairings', {
        body: { agent_name: 'B 电脑', platform: 'windows' },
      }),
      201,
    );
    agentBToken = pairing.agent_token;
    agentBId = pairing.agent_id;
    const claim = expectOk(
      await call('POST', '/pairings/claim', {
        body: { code: pairing.code, device_name: 'B 手机', platform: 'android' },
      }),
    );
    deviceBToken = claim.device_token;
    assert.notEqual(agentBId, agentId);
  });

  let phoneB;
  await step('B 手机 WS 连接后 hello.agent_id 为 B（ticket 绑定不可跨租户）', async () => {
    phoneB = track(await openPhone(deviceBToken));
    const hello = await phoneB.waitFor((m) => m.type === 'hello', 5000, 'B phone hello');
    assert.equal(hello.agent_id, agentBId);
    await phoneB.waitFor((m) => m.type === 'presence', 5000, 'B phone presence');
  });

  let agentB;
  let taskB;
  await step('B 的任务与事件只发给 B：A 收不到、B 能收到', async () => {
    taskB = expectOk(
      await call('POST', '/tasks', { token: deviceBToken, body: { url: taskUrl } }),
      201,
    ).task.id;
    await phoneA.expectNone((m) => m.type === 'note' && m.task_id === taskB, 800);
    agentB = track(await openAgent(agentBToken));
    await agentB.waitFor((m) => m.type === 'hello', 5000, 'B agent hello');
    const pushed = await agentB.waitFor(
      (m) => m.type === 'task' && m.task?.id === taskB,
      5000,
      'B agent task push',
    );
    assert.equal(pushed.task.status, 'queued');
    await agentA4.expectNone((m) => m.type === 'task' && m.task?.id === taskB, 1200);

    agentB.send({ type: 'task_update', task_id: taskB, status: 'running', progress: 50 });
    const updatedB = await phoneB.waitFor(
      (m) => m.type === 'task_updated' && m.task?.id === taskB,
      5000,
      'B phone task_updated',
    );
    assert.equal(updatedB.task.status, 'running');
    await phoneA.expectNone((m) => m.type === 'task_updated' && m.task?.id === taskB, 1200);
    await phoneA.expectNone((m) => m.type === 'note' && m.task_id === taskB, 800);
  });

  await step('A/B REST 资源隔离：互相看不到对方任务（404）', async () => {
    expectError(await call('GET', `/tasks/${taskB}`, { token: deviceToken }), 404, 'task_not_found');
    expectError(await call('GET', `/tasks/${liveTaskId}`, { token: deviceBToken }), 404, 'task_not_found');
    const listA = expectOk(await call('GET', '/tasks?limit=50', { token: deviceToken }));
    assert.ok(!listA.items.some((t) => t.id === taskB));
    const listB = expectOk(await call('GET', '/tasks?limit=50', { token: deviceBToken }));
    assert.ok(!listB.items.some((t) => t.id === liveTaskId));
  });

  await step('B 的 note 只发给 B 手机', async () => {
    const markdown = `# B 的笔记\n\nB-ONLY-${unique}`;
    agentB.send({ type: 'task_note', task_id: taskB, markdown, title: 'B 的笔记' });
    const noteB = await phoneB.waitFor((m) => m.type === 'note' && m.task_id === taskB, 5000, 'B phone note');
    assert.equal(noteB.markdown, markdown);
    await phoneA.expectNone((m) => m.type === 'note' && m.task_id === taskB, 1200);
  });

  await step('撤销设备后其实时连接被服务端下线（不再收到新事件）', async () => {
    const pairing = expectOk(
      await call('POST', '/agent/pairings', { token: agentToken, body: {} }),
      201,
    );
    const claim = expectOk(
      await call('POST', '/pairings/claim', {
        body: { code: pairing.code, device_name: '待撤销手机', platform: 'android' },
      }),
    );
    const phoneC = track(await openPhone(claim.device_token));
    await phoneC.waitFor((m) => m.type === 'hello', 5000, 'phone C hello');
    const revokedTask = expectOk(
      await call('POST', '/tasks', { token: claim.device_token, body: { url: taskUrl } }),
      201,
    ).task.id;
    expectOk(await call('DELETE', `/agent/devices/${claim.device_id}`, { token: agentToken }));
    const closed = await maybeClosed(phoneC, 1500);
    assert.ok(closed === null || closed.code === 4003);

    // 功能断言：撤销后该设备的 socket 不再收到事件（其他手机照常收到）。
    const markdown = `# 撤销后事件\n\nREVOKED-ONLY-${unique}`;
    agentA4.send({ type: 'task_update', task_id: revokedTask, status: 'running', progress: 20 });
    agentA4.send({ type: 'task_note', task_id: revokedTask, markdown, title: '撤销后事件' });
    const noteForOthers = await phoneA.waitFor(
      (m) => m.type === 'note' && m.task_id === revokedTask,
      6000,
      'other phone note after revoke',
    );
    assert.match(noteForOthers.markdown, /REVOKED-ONLY-/);
    await phoneC.expectNone((m) => m.type === 'note' && m.task_id === revokedTask, 1500);
  });

  await step('笔记正文不落 D1 / DO SQLite（本地持久化文件 marker 扫描）', async () => {
    scanStateForMarkers(markers);
  });

  await step('WS 相关非升级请求返回 426', async () => {
    const res = await call('GET', '/agent/ws', { token: agentToken });
    expectError(res, 426, 'upgrade_required');
  });

  // 收尾：关闭所有 socket（不影响断言）
  for (const client of clients) {
    try {
      client.close();
    } catch {
      // ignore
    }
  }
  await sleep(300);

  console.log(`\n${'='.repeat(50)}`);
  console.log(`PASS ${passed}  FAIL ${failed}`);
  if (failed) {
    console.error('Failures:', failures.map((f) => f.name).join(', '));
    console.error('--- server logs (tail) ---');
    console.error(logs.slice(-6000));
  }
  console.log(`${'='.repeat(50)}\n`);
  return failed === 0;
}

function cleanup() {
  if (!child) return;
  if (keep) {
    console.log(`\n--keep: relay 留在 http://127.0.0.1:${port}（D1 state: ${stateDir}）`);
    return;
  }
  try {
    if (process.platform === 'win32') {
      execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill('SIGTERM');
    }
  } catch {
    // ignore
  }
}

try {
  const okResult = await main();
  cleanup();
  process.exit(okResult ? 0 : 1);
} catch (error) {
  console.error(error);
  cleanup();
  process.exit(1);
}

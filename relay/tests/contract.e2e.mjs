// LinkNote Relay 契约端到端测试。
// 启动一个独立的 wrangler dev（端口 8799、独立 D1 状态目录），走完配对 → 任务 → 完成 → 读取笔记 → 重试/权限。
// 用法：npm test   （加 --keep 可保留服务，方便手动打开 http://127.0.0.1:8799）
import { spawn, execFileSync } from 'node:child_process';
import { rm, mkdir } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const relayDir = path.resolve(import.meta.dirname, '..');
const stateDir = path.join(relayDir, '.wrangler', 'test-state');
const wranglerBin = path.join(relayDir, 'node_modules', 'wrangler', 'bin', 'wrangler.js');
const port = Number(process.env.TEST_PORT || 8799);
const inspectorPort = port + 500;
const base = `http://127.0.0.1:${port}/api/v1`;
const keep = process.argv.includes('--keep');

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
      code === 0 ? resolve(out) : reject(new Error(`wrangler ${args.join(' ')} 退出码 ${code}\n${out.slice(-3000)}`)),
    );
  });
}

async function waitForHealth(timeoutMs = 90_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return await res.json();
    } catch {
      // not ready
    }
    await new Promise((r) => setTimeout(r, 700));
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
  const markdown = '# 测试笔记\n\n这是 **中文** 内容，包含 `code`。\n\n- 要点一\n- 要点二\n\n[链接](https://example.com)';
  const taskUrl = 'https://www.bilibili.com/video/BV1xx411c7mD';

  let agentToken;
  let code;
  let pairingId;
  let deviceToken;
  let deviceId;
  let taskId;

  await step('health 返回 ok', async () => {
    assert.equal(health.ok, true);
    assert.equal(health.data.status, 'ok');
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
    assert.equal(data.agent.name, agentName);
    expectOk(await call('GET', `/pairings/${pairingId}`, { token: agentToken }));
    const state = expectOk(await call('GET', `/pairings/${pairingId}`, { token: agentToken }));
    assert.equal(state.status, 'claimed');
    assert.equal(state.device.name, deviceName);
  });

  await step('同一个配对码不能二次领取', async () => {
    expectError(
      await call('POST', '/pairings/claim', { body: { code, device_name: 'x', platform: 'android' } }),
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
      await call('POST', '/tasks', { token: deviceToken, body: { url: 'https://example.com/video/1' } }),
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

  await step('Agent 回传进度，手机能看到阶段/进度', async () => {
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

  await step('Agent 完成任务，中文 Markdown 原样保存', async () => {
    expectOk(
      await call('POST', `/agent/tasks/${taskId}/complete`, {
        token: agentToken,
        body: { title: '测试笔记·标题', markdown, duration_seconds: 321 },
      }),
    );
    const detail = expectOk(await call('GET', `/tasks/${taskId}`, { token: deviceToken }));
    assert.equal(detail.task.status, 'succeeded');
    assert.equal(detail.task.stage, 'done');
    assert.equal(detail.task.progress, 100);
    assert.equal(detail.task.title, '测试笔记·标题');
    assert.equal(detail.task.has_note, true);

    const note = expectOk(await call('GET', `/tasks/${taskId}/note`, { token: deviceToken }));
    assert.equal(note.markdown, markdown);
    assert.equal(note.title, '测试笔记·标题');
  });

  await step('重复 complete 幂等', async () => {
    expectOk(
      await call('POST', `/agent/tasks/${taskId}/complete`, {
        token: agentToken,
        body: { title: 'x', markdown: '# x' },
      }),
    );
  });

  await step('其他设备 token 看不到该任务（404）', async () => {
    const p = expectOk(await call('POST', '/agent/pairings', { token: agentToken, body: {} }), 201);
    const other = expectOk(
      await call('POST', '/pairings/claim', {
        body: { code: p.code, device_name: '另一台手机', platform: 'android' },
      }),
    );
    expectError(await call('GET', `/tasks/${taskId}`, { token: other.device_token }), 404, 'task_not_found');
    expectError(await call('GET', `/tasks/${taskId}/note`, { token: other.device_token }), 404, 'task_not_found');
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
    expectError(await call('DELETE', `/tasks/${running.task.id}`, { token: deviceToken }), 409, 'invalid_state');
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
    const other = me.devices.find((d) => d.id !== deviceId);
    expectOk(await call('DELETE', `/agent/devices/${other.id}`, { token: agentToken }));
    expectError(await call('GET', '/me', { token: other.device_token }), 401, 'unauthorized');
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
  });

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

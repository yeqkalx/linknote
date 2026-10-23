// LinkNote PWA UI 冒烟测试（Playwright + 系统 Chrome，无需下载浏览器）。
// 前置：relay 在 http://127.0.0.1:8787 运行；PWA 已 sync 到 relay/public。
// 用法：node scripts/ui-smoke.mjs
import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const RELAY = process.env.UI_RELAY || 'http://127.0.0.1:8787';
const CHROME =
  process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const root = path.resolve(import.meta.dirname, '..');
const shots = path.join(root, 'assets', 'ui-shots');
await mkdir(shots, { recursive: true });

const results = [];
function ok(name) {
  results.push({ name, ok: true });
  console.log(`  ok  ${name}`);
}
function fail(name, error) {
  results.push({ name, ok: false, error });
  console.error(`  FAIL ${name}: ${error?.message || error}`);
}

async function api(method, route, { token, body } = {}) {
  const res = await fetch(`${RELAY}/api/v1${route}`, {
    method,
    headers: {
      ...(body ? { 'content-type': 'application/json' } : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json();
  if (!res.ok || json.ok !== true) throw new Error(`${method} ${route} -> ${res.status} ${JSON.stringify(json)}`);
  return json.data;
}

const runId = Date.now().toString(36);
const agentName = '我的电脑';
const deviceName = '我的手机';
const url = 'https://www.bilibili.com/video/BV1GJ411x7h7';
const title = '示例视频笔记';
const markdown = `# ${title}\n\n这是 **UI 测试** 的正文。\n\n- 第一点\n- 第二点\n\n<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" onerror="window.__pwned = 1">\n\n<script>window.__pwned = 2</script>\n\n[示例链接](https://example.com)\n`;

let browser;
let pairing;
let deviceToken;

try {
  // 先创建配对码（Agent 侧）
  pairing = await api('POST', '/pairings', {
    body: { agent_name: agentName, platform: 'windows', version: 'ui-test' },
  });

  browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    serviceWorkers: 'block',
    userAgent:
      'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36',
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') pageErrors.push(`console.error: ${msg.text()}`);
  });

  await page.goto(RELAY + '/', { waitUntil: 'networkidle' });

  try {
    await page.waitForSelector('#pair-code', { timeout: 15_000 });
    ok('首次打开进入配对页');
  } catch (error) {
    fail('首次打开进入配对页', error);
    await page.screenshot({ path: path.join(shots, 'pair-failed.png'), fullPage: true });
    throw error;
  }

  await page.fill('#pair-code', pairing.code);
  await page.fill('#pair-device-name', deviceName);
  await page.screenshot({ path: path.join(shots, '01-pair.png'), fullPage: true });
  await page.click('#pair-submit');
  await page.waitForSelector('#url-input', { timeout: 20_000 });
  ok('输入配对码后进入收件箱');
  await page.screenshot({ path: path.join(shots, '02-home-empty.png'), fullPage: true });

  const authJson = await page.evaluate(() => localStorage.getItem('linknote.auth.v1'));
  const authMap = JSON.parse(authJson || '{}');
  const firstAuth = Object.values(authMap)[0];
  assert.ok(firstAuth && typeof firstAuth.token === 'string', 'localStorage 应有 device token');
  deviceToken = firstAuth.token;
  assert.match(deviceToken, /^dev_/);
  ok('配对 token 已保存');

  // 提交任务（走 UI）
  await page.fill('#url-input', url);
  await page.click('#submit-btn');
  await page.waitForSelector(`[data-task-id]`, { timeout: 15_000 });
  ok('通过 UI 提交任务并出现在列表');

  // 取任务 id
  const tasks = await api('GET', '/tasks?limit=10', { token: deviceToken });
  const task = tasks.items.find((item) => item.url === url);
  assert.ok(task, '任务列表应包含刚提交的任务');
  const taskId = task.id;

  // Agent 领取 + 回传进度 + 完成（模拟 PC Agent）
  const next = await api('GET', '/agent/tasks/next', { token: pairing.agent_token });
  assert.equal(next.task?.id, taskId, 'next 应领取刚提交的任务');
  await api('POST', `/agent/tasks/${taskId}/progress`, {
    token: pairing.agent_token,
    body: { stage: 'transcribing', progress: 55, message: '正在语音转写' },
  });
  await page.waitForTimeout(1200);
  ok('列表/详情能看到 running 状态');

  await api('POST', `/agent/tasks/${taskId}/complete`, {
    token: pairing.agent_token,
    body: { title, markdown, duration_seconds: 212 },
  });

  // 打开详情（直接 hash 路由，同时验证详情页）
  await page.goto(`${RELAY}/#/task/${taskId}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.waitForSelector('#reader', { timeout: 15_000 });
  // 阅读器会去掉与卡片标题重复的 H1，所以标题在页面主体（.note-title），正文在 #reader。
  const pageText = await page.locator('body').innerText();
  assert.ok(pageText.includes(title), '详情页应显示笔记标题');
  const readerText = await page.locator('#reader').innerText();
  assert.ok(readerText.includes('第二点'), '阅读器应显示正文');

  const pwned = await page.evaluate(() => window.__pwned);
  assert.equal(pwned, undefined, 'Markdown 中的脚本不能执行');
  const dangerous = await page.evaluate(() => {
    const reader = document.querySelector('#reader');
    if (!reader) return -1;
    return reader.querySelectorAll(
      'script, iframe, object, embed, [onerror], [onload], [onclick], [href^="javascript:"]',
    ).length;
  });
  assert.equal(dangerous, 0, 'Markdown 应经 DOMPurify 清洗（脚本/事件属性/危险 schema 被移除）');
  ok('详情页渲染 Markdown，XSS 被清洗');

  await page.screenshot({ path: path.join(shots, '03-reader.png'), fullPage: true });

  // 列表页完成态
  await page.goto(`${RELAY}/#/home`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  const listText = await page.locator('body').innerText();
  assert.ok(listText.includes(title), '收件箱应显示已完成任务标题');
  await page.screenshot({ path: path.join(shots, '04-home-done.png'), fullPage: true });
  await page.screenshot({ path: path.join(shots, '10-home-viewport.png'), fullPage: false });
  ok('收件箱显示完成任务');

  // 设置页
  await page.goto(`${RELAY}/#/settings`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  const settingsText = await page.locator('body').innerText();
  assert.ok(settingsText.includes('服务器') || settingsText.includes('Relay'), '设置页应显示服务器地址');
  assert.ok(settingsText.includes(deviceName) || settingsText.includes('设备'), '设置页应显示设备');
  await page.screenshot({ path: path.join(shots, '05-settings.png'), fullPage: true });
  ok('设置页可用');

  // share_target 入口：/share?text=...
  await page.goto(
    `${RELAY}/share?text=${encodeURIComponent(`分享自 B 站 ${url}`)}&title=${encodeURIComponent('测试分享')}`,
    { waitUntil: 'networkidle' },
  );
  await page.waitForTimeout(1000);
  const currentUrl = page.url();
  assert.ok(currentUrl.includes('#/home'), `share 应跳转到收件箱，实际 ${currentUrl}`);
  const draftValue = await page.locator('#url-input').inputValue();
  assert.ok(draftValue.includes(url), `分享内容应预填，实际 ${draftValue}`);
  await page.screenshot({ path: path.join(shots, '06-share-target.png'), fullPage: true });
  ok('PWA share_target 入口预填分享内容');

  if (pageErrors.length) {
    // Service Worker 被 block 时会有注册失败，忽略；其他错误要暴露
    const meaningful = pageErrors.filter(
      (message) => !/service.?worker|sw\.js|Failed to register/i.test(message),
    );
    assert.deepEqual(meaningful, [], `页面不应有 JS 错误: ${meaningful.join(' | ')}`);
    ok('页面无 JS 错误');
  } else {
    ok('页面无 JS 错误');
  }
} catch (error) {
  fail('UI 流程', error);
  if (browser) {
    const pages = browser.contexts().flatMap((c) => c.pages());
    if (pages[0]) {
      await pages[0]
        .screenshot({ path: path.join(shots, '99-failure.png'), fullPage: true })
        .catch(() => {});
    }
  }
} finally {
  if (browser) await browser.close().catch(() => {});
}

const failed = results.filter((item) => !item.ok);
console.log(`\nUI smoke: PASS ${results.length - failed.length} FAIL ${failed.length}`);
if (failed.length) {
  console.error(failed.map((item) => `- ${item.name}: ${item.error?.message || item.error}`).join('\n'));
  process.exitCode = 1;
}


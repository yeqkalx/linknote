// 用真实 E2E 会话数据在 PWA 里打开真实生成的 B 站笔记，截图留档。
// 前置：assets/e2e/session.json（device_token）与 assets/e2e/bili-task-id.txt 存在；relay 在 8787。
import { chromium } from 'playwright-core';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const shots = path.join(root, 'assets', 'ui-shots');
mkdirSync(shots, { recursive: true });
const RELAY = process.env.UI_RELAY || 'http://127.0.0.1:8787';
const sessionPath = path.join(root, 'assets', 'e2e', 'session.json');
const taskPath = path.join(root, 'assets', 'e2e', 'bili-task-id.txt');
if (!existsSync(sessionPath) || !existsSync(taskPath)) {
  console.error('缺少 E2E 会话文件，先跑真实链路测试');
  process.exit(1);
}
const session = JSON.parse(readFileSync(sessionPath, 'utf8').replace(/^\uFEFF/, ''));
const taskId = readFileSync(taskPath, 'utf8').replace(/^\uFEFF/, '').trim();
const base = RELAY.replace(/\/$/, '');
const authEntry = {
  token: session.device_token,
  device: { id: session.device_id, name: session.device_name || 'E2E 测试手机', platform: 'android' },
  agent: { id: session.agent_id, name: session.agent_name || 'E2E 测试电脑', platform: 'windows', online: false },
  pairedAt: new Date().toISOString(),
};

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
});
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
await context.addInitScript(
  ({ base, entry }) => {
    try {
      const key = 'linknote.auth.v1';
      const map = JSON.parse(localStorage.getItem(key) || '{}');
      map[base] = entry;
      localStorage.setItem(key, JSON.stringify(map));
    } catch {
      // ignore
    }
  },
  { base, entry: authEntry },
);
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

await page.goto(`${base}/#/task/${taskId}`, { waitUntil: 'networkidle' });
await page.waitForSelector('#reader', { timeout: 20_000 });
await page.waitForTimeout(800);
const title = await page.locator('.note-title').innerText().catch(() => '');
const text = await page.locator('#reader').innerText();
console.log('TITLE', title);
console.log('READER_CHARS', text.length);
console.log('READER_HEAD', text.slice(0, 180).replace(/\n+/g, ' | '));
console.log('ERRORS', errors.join(' | ') || '(none)');
await page.screenshot({ path: path.join(shots, '08-real-bilibili-note.png'), fullPage: true });

await page.goto(`${base}/#/home`, { waitUntil: 'networkidle' });
await page.waitForTimeout(800);
await page.screenshot({ path: path.join(shots, '09-real-home.png'), fullPage: true });
await browser.close();

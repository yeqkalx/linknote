// 把 app/www 同步到 relay/public，让 Worker 一起托管 PWA。
import { cp, rm, mkdir, access } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const src = path.join(root, 'app', 'www');
const dest = path.join(root, 'relay', 'public');

try {
  await access(path.join(src, 'index.html'));
} catch {
  console.error('[sync-app] app/www/index.html 不存在；先完成 PWA 页面或检查路径');
  process.exit(1);
}

await rm(dest, { recursive: true, force: true });
await mkdir(dest, { recursive: true });
await cp(src, dest, { recursive: true });
console.log('[sync-app] app/www -> relay/public');

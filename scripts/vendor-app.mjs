// 把 marked / DOMPurify 的 ESM 构建复制到 app/www/vendor。
// 这样 PWA 不需要打包器，也能离线加载。
import { copyFile, mkdir, access } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const app = path.join(root, 'app');
const vendor = path.join(app, 'www', 'vendor');

const files = [
  ['node_modules/marked/lib/marked.esm.js', 'marked.esm.js'],
  ['node_modules/dompurify/dist/purify.es.mjs', 'purify.es.mjs'],
];

await mkdir(vendor, { recursive: true });

for (const [srcRel, destName] of files) {
  const src = path.join(app, srcRel);
  const dest = path.join(vendor, destName);
  try {
    await access(src);
  } catch {
    console.error(`[vendor] missing ${src}\n先运行: npm --prefix app install`);
    process.exit(1);
  }
  await copyFile(src, dest);
  console.log(`[vendor] ${srcRel} -> www/vendor/${destName}`);
}

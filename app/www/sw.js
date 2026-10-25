/**
 * 链记 LinkNote — Service Worker（纯手写，无构建步骤）
 *
 * 策略：
 * - 壳资源（index / css / js / vendor / manifest / icons）：安装时预缓存，运行时 stale-while-revalidate。
 * - 页面导航：网络优先；离线回退到缓存的 index.html（hash 路由只有一个入口）。
 * - API（/api/）：不拦截、不缓存，永远走网络；离线由页面的本地队列兜底。
 */
const SW_VERSION = '0.4.0';
// 同一版本号下改资源时递增 CACHE_REVISION：新 SW 会建新的壳缓存并清掉旧壳，
// 保证已安装用户升级后拿到 v0.4 的新 JS/CSS，而不是旧的缓存脚本。
const CACHE_REVISION = 3;
const SHELL_CACHE = `linknote-shell-v${SW_VERSION}-r${CACHE_REVISION}`;
const SCOPE = self.registration.scope;
const toUrl = (path) => new URL(path, SCOPE).href;

const PRECACHE = [
  '',
  'index.html',
  'manifest.webmanifest',
  'share.html',
  'css/app.css',
  'js/app.js',
  'js/config.js',
  'js/util.js',
  'js/store.js',
  'js/api.js',
  'js/queue.js',
  'js/ui.js',
  'js/markdown.js',
  'js/router.js',
  'js/bridge.js',
  'js/pwa.js',
  'js/realtime.js',
  'js/views/pair.js',
  'js/views/home.js',
  'js/views/task.js',
  'js/views/settings.js',
  'vendor/marked.esm.js',
  'vendor/purify.es.mjs',
  'icons/favicon.svg',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-maskable-192.png',
  'icons/icon-maskable-512.png',
  'icons/apple-touch-icon.png',
].map(toUrl);

const INDEX_URL = toUrl('index.html');
const ROOT_URL = toUrl('');

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // 逐个缓存：单个资源缺失（例如图标还没生成）不应让整个安装失败。
      await Promise.allSettled(
        PRECACHE.map(async (assetUrl) => {
          try {
            const response = await fetch(new Request(assetUrl, { cache: 'reload' }));
            if (response && response.ok) await cache.put(assetUrl, response);
          } catch (error) {
            // 忽略单个失败，运行时再补齐。
          }
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith('linknote-shell-') && key !== SHELL_CACHE)
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'SKIP_WAITING') {
    void self.skipWaiting();
    return;
  }
  if (data.type === 'GET_VERSION') {
    const reply = { type: 'LINKNOTE_SW_VERSION', version: SW_VERSION, cache: SHELL_CACHE };
    if (event.ports && event.ports[0]) event.ports[0].postMessage(reply);
    else if (event.source && typeof event.source.postMessage === 'function') event.source.postMessage(reply);
  }
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  let requestUrl;
  try {
    requestUrl = new URL(request.url);
  } catch (error) {
    return;
  }

  // 只处理同源请求；跨域（例如后来的图片外链）交给浏览器。
  if (requestUrl.origin !== self.location.origin) return;
  // API 永远不拦截：断网由页面显示错误 + 本地队列负责。
  if (/\/api\//.test(requestUrl.pathname)) return;

  if (request.mode === 'navigate') {
    event.respondWith(handleNavigate(request));
    return;
  }

  if (!/^https?:$/.test(requestUrl.protocol)) return;
  event.respondWith(handleAsset(request));
});

async function matchShell(assetUrl) {
  const cache = await caches.open(SHELL_CACHE);
  const direct = await cache.match(assetUrl);
  if (direct) return direct;
  if (assetUrl === ROOT_URL) return (await cache.match(INDEX_URL)) || null;
  return null;
}

async function handleNavigate(request) {
  const requestUrl = new URL(request.url);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      // 只缓存真正的 SPA 壳，避免把 /share 的跳转页当成首页缓存。
      if (/\/$|\/index\.html$/.test(requestUrl.pathname)) {
        const cache = await caches.open(SHELL_CACHE);
        cache.put(INDEX_URL, response.clone()).catch(() => {});
      }
      return response;
    }
  } catch (error) {
    // 离线：走缓存。
  }

  const cached = (await matchShell(INDEX_URL)) || (await matchShell(ROOT_URL));
  if (cached) return cached;

  return new Response(
    '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>链记 · 离线</title><body style="margin:0;padding:32px;background:#f4f7fc;color:#0f1b30;font-family:system-ui,sans-serif">' +
      '<h1 style="font-size:20px">当前处于离线状态</h1><p style="line-height:1.7">本地还没有缓存页面。请连接网络后重新打开链记。</p></body></html>',
    { status: 200, headers: { 'Content-Type': 'text/html;charset=utf-8' } },
  );
}

async function handleAsset(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);

  const networkPromise = fetch(request)
    .then((response) => {
      if (response && response.ok && response.type === 'basic') {
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    // 后台更新，不阻塞当前请求。
    void networkPromise;
    return cached;
  }

  const response = await networkPromise;
  if (response) return response;

  return new Response('/* 离线：该资源还没有缓存 */', {
    status: 504,
    headers: { 'Content-Type': 'text/plain;charset=utf-8' },
  });
}



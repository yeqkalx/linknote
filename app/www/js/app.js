/**
 * 链记 LinkNote — 应用入口
 * 初始化路由、守卫、原生分享桥、Service Worker、离线队列、全局错误处理。
 */
import { auth, draft, navIntent, settings } from './store.js';
import { setUnauthorizedHandler } from './api.js';
import { installNativeBridge } from './bridge.js';
import { initPwa } from './pwa.js';
import { onQueueEvent, startQueue } from './queue.js';
import {
  defineRoutes,
  getCurrent,
  navigate,
  refresh,
  setAfterRender,
  setGuard,
  start,
} from './router.js';
import { icon, toast } from './ui.js';
import { escapeHtml, normalizeShareText } from './util.js';
import { homeView } from './views/home.js';
import { pairView } from './views/pair.js';
import { settingsView } from './views/settings.js';
import { taskView } from './views/task.js';

const ROUTE_TITLES = {
  '/home': '收件箱',
  '/pair': '配对电脑',
  '/settings': '设置',
};

let authRedirecting = false;

/* ------------------------------------------------------------------ */
/* 分享入口                                                            */
/* ------------------------------------------------------------------ */

/**
 * PWA share_target 会把内容 GET 到 /share?title=..&text=..&url=..
 * 部署环境可能：
 * - 静态服务器把 /share 重定向到 share.html（由 share.html 再转 hash）；
 * - Cloudflare 静态资源 SPA 回退直接返回 index.html。
 * 这里统一兜底：识别路径后把内容放进分享草稿，再换到 #/home。
 */
function consumeShareTarget() {
  const pathname = location.pathname || '/';
  if (!/(?:^|\/)share\/?$/.test(pathname)) return false;
  const params = new URLSearchParams(location.search || '');
  const parts = [params.get('title'), params.get('text'), params.get('url')].filter(
    (part) => part && String(part).trim(),
  );
  const text = normalizeShareText(parts.join('\n'));
  if (text) draft.set(text);

  const cleanPath = pathname.replace(/share\/?$/, '') || '/';
  const hash = text ? '#/home?draft=1' : '#/home';
  try {
    history.replaceState(null, '', `${cleanPath}${hash}`);
  } catch (error) {
    // 忽略
  }
  return true;
}

/** 原生分享 / 系统分享进入后的统一处理 */
function handleSharedText(rawText) {
  const text = normalizeShareText(rawText);
  if (!text) return;
  draft.set(text);

  const base = settings.getApiBase();
  const paired = Boolean(base && auth.get(base));
  if (!paired) {
    navigate('/pair');
    toast('已收到分享内容，完成配对后即可发送');
    return;
  }

  const current = getCurrent();
  if (current && current.path === '/home') refresh();
  else navigate('/home');
  toast('已收到分享内容');
}

/* ------------------------------------------------------------------ */
/* 路由                                                                */
/* ------------------------------------------------------------------ */

function configureRouter() {
  defineRoutes({
    '/home': homeView,
    '/pair': pairView,
    '/task/:id': taskView,
    '/settings': settingsView,
  });

  setGuard(({ path }) => {
    const base = settings.getApiBase();
    const paired = Boolean(base && auth.get(base));
    if (!paired && path !== '/pair') {
      if (path !== '/home') navIntent.remember(path);
      return '/pair';
    }
    if (paired && path === '/pair') return '/home';
    return null;
  });

  setAfterRender((route) => {
    const path = route.path || '/home';
    const tabbar = document.getElementById('tabbar');
    const showTabs = path === '/home' || path === '/settings';
    if (tabbar) {
      tabbar.hidden = !showTabs;
      tabbar.querySelectorAll('[data-route]').forEach((item) => {
        const active = item.getAttribute('data-route') === path;
        item.classList.toggle('is-active', active);
        if (active) item.setAttribute('aria-current', 'page');
        else item.removeAttribute('aria-current');
      });
    }
    let title = ROUTE_TITLES[path];
    if (!title && path.startsWith('/task')) title = '任务详情';
    document.title = title ? `${title} · 链记` : '链记 LinkNote';
  });
}

/* ------------------------------------------------------------------ */
/* 全局状态                                                            */
/* ------------------------------------------------------------------ */

function configureUnauthorized() {
  setUnauthorizedHandler((error) => {
    if (authRedirecting) return;
    authRedirecting = true;
    toast((error && error.message) || '登录状态已失效，请重新配对', { type: 'error' });
    const current = getCurrent();
    if (current && current.path && current.path !== '/pair' && current.path !== '/home') {
      navIntent.remember(current.path);
    }
    navigate('/pair', { replace: true });
    setTimeout(() => {
      authRedirecting = false;
    }, 1500);
  });
}

function configureQueue() {
  startQueue();
  onQueueEvent('sent', ({ task }) => {
    if (task && task.id) toast('队列里的任务已发出', { type: 'success' });
  });
  onQueueEvent('failed', ({ error, permanent }) => {
    if (permanent) toast(`发送失败：${(error && error.message) || '请检查链接'}`, { type: 'error' });
  });
}

function configureConnectivity() {
  window.addEventListener('online', () => {
    toast('网络已恢复', { type: 'success' });
  });
  window.addEventListener('offline', () => {
    toast('网络已断开，新任务会先存到本地', { type: 'error' });
  });
}

function configureErrorReporting() {
  window.addEventListener('unhandledrejection', (event) => {
    // 只记录，不打断用户操作。
    console.error('[linknote] 未处理的 Promise 异常', event.reason);
  });
}

/* ------------------------------------------------------------------ */
/* 启动 / 兜底                                                         */
/* ------------------------------------------------------------------ */

function renderFatal(error) {
  console.error('[linknote] 启动失败', error);
  const boot = document.getElementById('boot');
  const view = document.getElementById('view');
  if (view) view.hidden = true;
  if (!boot) return;
  boot.hidden = false;
  boot.innerHTML = `
    <div class="boot-fatal">
      <span class="boot-fatal-icon" aria-hidden="true">${icon('alert', { size: 28 })}</span>
      <h1 class="error-title">链记没能启动</h1>
      <p class="error-text">${escapeHtml((error && error.message) || '未知错误')}</p>
      <button type="button" class="btn btn-primary" id="fatal-reload">重新加载</button>
    </div>`;
  const reload = boot.querySelector('#fatal-reload');
  if (reload) reload.addEventListener('click', () => location.reload());
}

function bootApp() {
  try {
    initPwa();
    consumeShareTarget();
    configureRouter();
    configureUnauthorized();
    configureErrorReporting();
    start({ fallback: '/home' });
    installNativeBridge({ onShare: handleSharedText });
    configureQueue();
    configureConnectivity();
  } catch (error) {
    renderFatal(error);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootApp, { once: true });
} else {
  bootApp();
}

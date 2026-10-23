/**
 * 链记 LinkNote — 极简 hash 路由
 * 路由：#/pair  #/home  #/task/:id  #/settings
 * 支持 hash 查询参数（如 #/home?draft=...），分享入口不经过服务端。
 */
import { escapeHtml } from './util.js';

let routes = [];
let fallbackPath = '/home';
let guard = null;
let afterRender = null;
let activeController = null;
let activeCleanup = null;
let current = null;
let started = false;
let renderSeq = 0;

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function compile(pattern) {
  const keys = [];
  const source = String(pattern)
    .split('/')
    .map((segment) => {
      if (segment.startsWith(':') && segment.length > 1) {
        keys.push(segment.slice(1));
        return '([^/]+)';
      }
      return escapeRegExp(segment);
    })
    .join('/');
  return { regex: new RegExp(`^${source}$`), keys };
}

/** @param {Record<string, (container: HTMLElement, ctx: object) => (void | Function | Promise<void | Function>)>} map */
export function defineRoutes(map) {
  routes = Object.entries(map || {}).map(([pattern, view]) => {
    const compiled = compile(pattern);
    return { pattern, regex: compiled.regex, keys: compiled.keys, view };
  });
}

/** 全局守卫：返回要重定向的路径，或 null 放行 */
export function setGuard(handler) {
  guard = typeof handler === 'function' ? handler : null;
}

/** 每次路由渲染完成后回调（用于更新 tabbar / 标题） */
export function setAfterRender(handler) {
  afterRender = typeof handler === 'function' ? handler : null;
}

/** '#/home?draft=x' → { path:'/home', query: URLSearchParams } */
export function parseHash(hash = typeof location !== 'undefined' ? location.hash : '') {
  const raw = String(hash || '').replace(/^#/, '');
  const questionIndex = raw.indexOf('?');
  const pathPart = questionIndex >= 0 ? raw.slice(0, questionIndex) : raw;
  const queryPart = questionIndex >= 0 ? raw.slice(questionIndex + 1) : '';
  let path = pathPart || '/home';
  if (!path.startsWith('/')) path = `/${path}`;
  if (path.length > 1) path = path.replace(/\/+$/, '');
  if (!path) path = '/';
  return { path, query: new URLSearchParams(queryPart) };
}

function matchRoute(path) {
  for (const route of routes) {
    const result = route.regex.exec(path);
    if (!result) continue;
    const params = {};
    route.keys.forEach((key, index) => {
      const value = result[index + 1];
      try {
        params[key] = decodeURIComponent(value);
      } catch (error) {
        params[key] = value;
      }
    });
    return { ...route, params };
  }
  return null;
}

function normalizeTarget(target) {
  let value = String(target ?? '').replace(/^#/, '');
  if (!value) return '/home';
  if (!value.startsWith('/')) value = `/${value}`;
  return value;
}

/** 当前路由信息（path/params/query/signal） */
export function getCurrent() {
  return current;
}

/**
 * 跳转。replace=true 时不产生历史记录（可用于配对完成后替换）。
 */
export function navigate(target, { replace = false } = {}) {
  const normalized = normalizeTarget(target);
  const hash = `#${normalized}`;
  if (typeof location === 'undefined') return;

  if (location.hash === hash) {
    void render();
    return;
  }
  if (replace) {
    try {
      history.replaceState(null, '', `${location.pathname}${location.search}${hash}`);
    } catch (error) {
      location.replace(hash);
      return;
    }
    void render();
    return;
  }
  location.hash = hash;
}

/** 重新渲染当前路由 */
export function refresh() {
  if (!started) return;
  void render();
}

function showRouteError(container, error) {
  const message = error && error.message ? error.message : '未知错误';
  container.innerHTML = `
    <section class="screen">
      <div class="card error-card" role="alert">
        <h1 class="error-title">页面没有加载成功</h1>
        <p class="error-text">${escapeHtml(message)}</p>
        <div class="error-actions">
          <button type="button" class="btn btn-ghost" data-action="retry">重试</button>
          <a class="btn btn-primary" href="#/home">回到收件箱</a>
        </div>
      </div>
    </section>`;
  const retryButton = container.querySelector('[data-action="retry"]');
  if (retryButton) retryButton.addEventListener('click', () => refresh());
}

async function render() {
  const mySeq = ++renderSeq;
  const container = document.getElementById('view');
  if (!container) return;

  const { path, query } = parseHash();

  if (guard) {
    let target = null;
    try {
      target = guard({ path, params: {}, query });
    } catch (error) {
      console.error('[router] guard 出错', error);
    }
    if (target && target !== path) {
      navigate(target, { replace: true });
      return;
    }
  }

  const matched = matchRoute(path) || matchRoute(fallbackPath);
  if (!matched) {
    showRouteError(container, new Error(`未知页面：${path}`));
    return;
  }

  // 清理上一个页面
  if (activeController) activeController.abort();
  if (typeof activeCleanup === 'function') {
    try {
      activeCleanup();
    } catch (error) {
      console.error('[router] cleanup 出错', error);
    }
  }
  activeCleanup = null;
  activeController = new AbortController();

  const ctx = {
    path,
    pattern: matched.pattern,
    params: matched.params,
    query,
    signal: activeController.signal,
  };
  current = ctx;

  const boot = document.getElementById('boot');
  if (boot) boot.setAttribute('hidden', '');
  container.removeAttribute('hidden');
  container.innerHTML = '';
  window.scrollTo({ top: 0, behavior: 'auto' });
  document.documentElement.setAttribute('data-route', path);

  if (afterRender) {
    try {
      afterRender(ctx);
    } catch (error) {
      console.error('[router] afterRender 出错', error);
    }
  }

  try {
    const result = await matched.view(container, ctx);
    if (mySeq !== renderSeq) {
      // 视图渲染期间用户又导航了：清理刚刚返回的资源。
      if (typeof result === 'function') {
        try {
          result();
        } catch (error) {
          console.error('[router] late cleanup 出错', error);
        }
      }
      return;
    }
    if (typeof result === 'function') activeCleanup = result;
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    if (activeController.signal.aborted) return;
    console.error('[router] 视图渲染出错', error);
    showRouteError(container, error);
  }
}

export function start({ fallback = '/home' } = {}) {
  fallbackPath = fallback || '/home';
  if (started) return;
  started = true;
  window.addEventListener('hashchange', () => {
    void render();
  });
  if (!location.hash || location.hash === '#' || location.hash === '#/') {
    navigate('/home', { replace: true });
    return;
  }
  void render();
}

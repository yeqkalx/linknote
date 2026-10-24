/**
 * 链记 LinkNote — 收件箱
 * 提交链接（断网入本地队列）、最近任务列表、电脑在线状态、分享内容预填。
 */
import { api, isNetworkError } from '../api.js';
import {
  INBOX_REFRESH_MS,
  LIST_LIMIT,
  MAX_SHARE_TEXT,
  PLATFORM_META,
  STAGE_LABELS,
  STATUS_META,
  SUPPORTED_PLATFORMS_TEXT,
} from '../config.js';
import { navigate } from '../router.js';
import {
  auth,
  clearDraft,
  settings,
  takeDraft,
  taskCache,
} from '../store.js';
import {
  agentPill,
  icon,
  listSkeleton,
  platformChip,
  setButtonBusy,
  toast,
} from '../ui.js';
import {
  detectPlatform,
  escapeHtml,
  extractFirstUrl,
  fitSubmitText,
  formatRelativeTime,
  hostOf,
  shortenUrl,
  truncate,
  uuid,
} from '../util.js';
import {
  discardQueued,
  enqueueUrl,
  flushQueue,
  onQueueEvent,
  pendingItems,
  retryQueued,
} from '../queue.js';

const TERMINAL = new Set(['succeeded', 'failed']);

function taskItemHtml(task) {
  const statusMeta = STATUS_META[task.status] || STATUS_META.queued;
  const platform = PLATFORM_META[task.platform];
  const isQueued = task.status === 'queued';
  const isRunning = task.status === 'running';
  const progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
  const title = task.title || (task.url ? `${hostOf(task.url) || '视频'} 视频` : '视频任务');
  const subParts = [];
  if (platform) subParts.push(platform.label);
  if (isQueued) subParts.push('等待电脑领取');
  else if (isRunning) subParts.push(STAGE_LABELS[task.stage] || '处理中');
  else if (task.status === 'succeeded') subParts.push('笔记已就绪');
  else if (task.status === 'failed') subParts.push('生成失败');
  if (task.updated_at || task.created_at) subParts.push(formatRelativeTime(task.updated_at || task.created_at));

  const progressHtml = isRunning
    ? `<div class="progress"><span class="progress-fill" style="width:${progress}%"></span></div><div class="task-progress-text">${progress}%</div>`
    : isQueued
      ? `<div class="progress progress-waiting"><span></span></div>`
      : '';

  return `
    <a class="task-item" href="#/task/${encodeURIComponent(task.id)}" data-task-id="${escapeHtml(task.id)}">
      <div class="task-top">
        <span class="chip chip-${statusMeta.tone}"><span class="chip-dot" aria-hidden="true"></span>${escapeHtml(statusMeta.label)}</span>
        <span class="task-arrow" aria-hidden="true">${icon('chevron-right', { size: 16 })}</span>
      </div>
      <div class="task-title">${escapeHtml(title)}</div>
      <div class="task-sub">${subParts.map((part) => escapeHtml(part)).join(' · ')}</div>
      ${progressHtml}
      ${task.error_message ? `<p class="task-error">${escapeHtml(truncate(task.error_message, 140))}</p>` : ''}
    </a>`;
}

export async function homeView(container, ctx) {
  const signal = ctx.signal;
  const base = settings.getApiBase();
  const authEntry = base ? auth.get(base) : null;

  if (!authEntry) {
    navigate('/pair', { replace: true });
    return () => {};
  }

  const cached = taskCache.read(base);
  const state = {
    base,
    agent: authEntry.agent || null,
    device: authEntry.device || null,
    tasks: cached.items,
    fetchedAt: cached.fetchedAt,
    nextBefore: null,
    listError: '',
    meError: '',
    offline: !navigator.onLine,
    submitting: false,
    loadingTasks: false,
    loadingMe: false,
    pending: pendingItems(),
  };

  container.innerHTML = `
    <section class="screen home-screen">
      <header class="appbar">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true"><img src="./icons/icon-192.png" alt="" /></span>
          <span class="brand-text">
            <span class="brand-name">链记</span>
            <span class="brand-sub">linknote</span>
          </span>
        </div>
        <div class="appbar-actions">
          <span id="agent-pill" class="status-pill" data-state="unknown">
            <span class="status-dot" aria-hidden="true"></span>连接中
          </span>
          <button type="button" class="icon-btn" id="open-settings" aria-label="打开设置">
            ${icon('settings', { size: 20 })}
          </button>
        </div>
      </header>

      <main class="home-main">
        <div id="offline-banner" class="banner banner-warn" role="status" hidden></div>

        <section class="composer card" aria-labelledby="composer-title">
          <div class="composer-head">
            <h2 class="card-title" id="composer-title">发一条链接</h2>
            <span class="composer-hint">${escapeHtml(SUPPORTED_PLATFORMS_TEXT)}</span>
          </div>
          <form id="submit-form" novalidate>
            <label class="sr-only" for="url-input">视频链接或分享文案</label>
            <textarea
              id="url-input"
              class="textarea composer-input"
              rows="3"
              maxlength="${MAX_SHARE_TEXT}"
              placeholder="粘贴视频链接，或直接粘贴整段分享文案…"
              autocomplete="off"
              autocapitalize="off"
              autocorrect="off"
              spellcheck="false"
              enterkeyhint="send"
            ></textarea>
            <div id="url-preview" class="url-preview" hidden></div>
            <p id="composer-error" class="form-error" role="alert" hidden></p>
            <button type="submit" class="btn btn-primary btn-block" id="submit-btn" data-busy-label="正在发送…">
              ${icon('send', { size: 18 })}<span>生成笔记</span>
            </button>
          </form>
          <p class="composer-foot">电脑会自动下载视频、转写并生成 Markdown；链接之外的视频内容不会上传。</p>
        </section>

        <section class="pending-section" id="pending-section" hidden aria-labelledby="pending-title">
          <div class="section-head">
            <h2 class="section-title" id="pending-title">待发送 <span id="pending-count" class="section-count"></span></h2>
            <button type="button" class="btn btn-soft btn-sm" id="pending-send-now">立即发送</button>
          </div>
          <div id="pending-list" class="pending-list"></div>
        </section>

        <section class="agent-card card" id="agent-card" aria-live="polite"></section>

        <section class="tasks-section" aria-labelledby="tasks-title">
          <div class="section-head">
            <h2 class="section-title" id="tasks-title">最近任务</h2>
            <div class="section-actions">
              <span class="cache-note" id="cache-note"></span>
              <button type="button" class="icon-btn icon-btn-sm" id="refresh-btn" aria-label="刷新任务列表">
                ${icon('refresh', { size: 18 })}
              </button>
            </div>
          </div>
          <div id="list-error" class="banner banner-error" role="alert" hidden></div>
          <div id="task-list" class="task-list" aria-live="polite"></div>
          <button type="button" class="btn btn-ghost btn-block" id="load-more" hidden>加载更多</button>
        </section>

        <p class="home-footnote">笔记生成在你自己电脑上的 BiliNote 完成，云端只中转链接、状态和 Markdown。</p>
      </main>
    </section>`;

  const elements = {
    agentPill: container.querySelector('#agent-pill'),
    openSettings: container.querySelector('#open-settings'),
    offlineBanner: container.querySelector('#offline-banner'),
    urlInput: container.querySelector('#url-input'),
    urlPreview: container.querySelector('#url-preview'),
    composerError: container.querySelector('#composer-error'),
    submitButton: container.querySelector('#submit-btn'),
    submitForm: container.querySelector('#submit-form'),
    pendingSection: container.querySelector('#pending-section'),
    pendingList: container.querySelector('#pending-list'),
    pendingCount: container.querySelector('#pending-count'),
    pendingSendNow: container.querySelector('#pending-send-now'),
    agentCard: container.querySelector('#agent-card'),
    refreshButton: container.querySelector('#refresh-btn'),
    listError: container.querySelector('#list-error'),
    taskList: container.querySelector('#task-list'),
    loadMore: container.querySelector('#load-more'),
    cacheNote: container.querySelector('#cache-note'),
  };

  /* ------------------------------------------------------------------ */
  /* 渲染                                                                */
  /* ------------------------------------------------------------------ */

  function renderPill() {
    const online = Boolean(state.agent && state.agent.online);
    elements.agentPill.setAttribute('data-state', online ? 'online' : 'offline');
    elements.agentPill.innerHTML = `<span class="status-dot" aria-hidden="true"></span>${online ? '电脑在线' : '电脑离线'}`;
    elements.agentPill.title = state.agent && state.agent.name ? state.agent.name : '我的电脑';
  }

  function renderAgentCard() {
    const agent = state.agent;
    const online = Boolean(agent && agent.online);
    const name = (agent && agent.name) || '我的电脑';
    const lastSeen = agent && agent.last_seen_at ? formatRelativeTime(agent.last_seen_at) : '';

    if (state.meError && !agent) {
      elements.agentCard.innerHTML = `
        <div class="agent-row">
          <span class="agent-icon" aria-hidden="true">${icon('cloud-off', { size: 20 })}</span>
          <div class="agent-info">
            <p class="agent-name">暂时读不到电脑状态</p>
            <p class="agent-sub">${escapeHtml(state.meError)}</p>
          </div>
        </div>
        <button type="button" class="btn btn-ghost btn-sm" id="agent-retry">重试</button>`;
    } else {
      elements.agentCard.innerHTML = `
        <div class="agent-row">
          <span class="agent-icon ${online ? 'is-online' : ''}" aria-hidden="true">${icon(online ? 'cloud' : 'cloud-off', { size: 20 })}</span>
          <div class="agent-info">
            <p class="agent-name">${escapeHtml(name)}</p>
            <p class="agent-sub">${online ? '在线，随时可以接收任务' : lastSeen ? `最近在线：${escapeHtml(lastSeen)}` : '还没有连接过'}</p>
          </div>
          ${agentPill({ online })}
        </div>
        ${
          online
            ? ''
            : '<p class="agent-hint">电脑离线时任务会留在队列里，Agent 上线后自动开始生成。你可以在设置里查看配对状态。</p>'
        }`;
    }
    renderPill();
    const retry = elements.agentCard.querySelector('#agent-retry');
    if (retry) retry.addEventListener('click', () => void loadMe(), { signal });
  }

  function renderOffline() {
    const offline = state.offline && typeof navigator !== 'undefined' && navigator.onLine !== false;
    const noNetwork = typeof navigator !== 'undefined' && navigator.onLine === false;
    if (!noNetwork && !offline) {
      elements.offlineBanner.hidden = true;
      return;
    }
    elements.offlineBanner.hidden = false;
    elements.offlineBanner.innerHTML = `${icon('wifi-off', { size: 16 })}<span>${
      noNetwork
        ? '当前没有网络，显示的是本地缓存；新任务会先存到本地，联网后自动发送。'
        : '连不上 Relay，显示的是本地缓存；新任务会先存到本地，恢复连接后自动发送。'
    }</span>`;
  }

  function renderPending() {
    state.pending = pendingItems();
    const items = state.pending;
    elements.pendingSection.hidden = items.length === 0;
    if (!items.length) {
      elements.pendingList.innerHTML = '';
      return;
    }
    elements.pendingCount.textContent = `（${items.length}）`;
    elements.pendingSendNow.hidden = false;
    elements.pendingSendNow.disabled = typeof navigator !== 'undefined' && navigator.onLine === false;
    elements.pendingList.innerHTML = items
      .map((item) => {
        const label = item.permanent
          ? `发送失败：${item.lastError || '请检查链接后重试'}`
          : item.sending
            ? '正在发送…'
            : item.lastError
              ? item.lastError
              : '等待网络恢复后自动发送';
        return `
          <div class="pending-item" data-client-key="${escapeHtml(item.clientKey)}">
            <div class="pending-info">
              <p class="pending-url">${escapeHtml(shortenUrl(item.url, 70) || truncate(item.url, 70))}</p>
              <p class="pending-meta ${item.permanent ? 'is-error' : ''}">${escapeHtml(label)}</p>
            </div>
            <div class="pending-actions">
              <button type="button" class="btn btn-ghost btn-xs" data-pending-action="retry" data-client-key="${escapeHtml(item.clientKey)}">重试</button>
              <button type="button" class="btn btn-ghost btn-xs btn-quiet-danger" data-pending-action="discard" data-client-key="${escapeHtml(item.clientKey)}">删除</button>
            </div>
          </div>`;
      })
      .join('');
  }

  function renderListError() {
    if (!state.listError) {
      elements.listError.hidden = true;
      return;
    }
    elements.listError.hidden = false;
    elements.listError.innerHTML = `<span>${escapeHtml(state.listError)}</span><button type="button" class="btn btn-ghost btn-xs" id="list-retry">重试</button>`;
    const retry = elements.listError.querySelector('#list-retry');
    if (retry) retry.addEventListener('click', () => void loadTasks(), { signal });
  }

  function renderTasks() {
    const tasks = state.tasks;
    elements.loadMore.hidden = !state.nextBefore;
    if (!tasks.length) {
      if (state.loadingTasks) {
        elements.taskList.innerHTML = listSkeleton(3);
      } else if (state.listError) {
        elements.taskList.innerHTML = `
          <div class="card error-card">
            <h3 class="error-title">任务列表没加载出来</h3>
            <p class="error-text">${escapeHtml(state.listError)}</p>
            <button type="button" class="btn btn-ghost btn-block" id="list-empty-retry">重试</button>
          </div>`;
        const retry = elements.taskList.querySelector('#list-empty-retry');
        if (retry) retry.addEventListener('click', () => void loadTasks(), { signal });
      } else {
        elements.taskList.innerHTML = `<div class="card empty-card">${emptyTasksHtml()}</div>`;
      }
    } else {
      elements.taskList.innerHTML = tasks.map((task) => taskItemHtml(task)).join('');
    }
    renderListError();
    renderCacheNote();
    renderOffline();
  }

  function emptyTasksHtml() {
    return `
      <div class="empty">
        <div class="empty-mark" aria-hidden="true">${icon('inbox', { size: 28 })}</div>
        <h3 class="empty-title">还没有任务</h3>
        <p class="empty-text">把视频链接粘贴到上面发送，家里的电脑会自动生成 Markdown 笔记。</p>
        <p class="empty-hint">在别的 App 里分享链接到「链记」也可以直接进来。</p>
      </div>`;
  }

  function renderCacheNote() {
    if (state.offline && state.fetchedAt) {
      elements.cacheNote.textContent = `离线缓存 · ${formatRelativeTime(new Date(state.fetchedAt).toISOString())}`;
      elements.cacheNote.hidden = false;
    } else if (!state.offline && state.fetchedAt) {
      elements.cacheNote.textContent = '';
      elements.cacheNote.hidden = true;
    } else {
      elements.cacheNote.hidden = true;
    }
  }

  function renderAll() {
    renderPill();
    renderAgentCard();
    renderPending();
    renderTasks();
  }

  function setRefreshBusy(busy) {
    elements.refreshButton.disabled = busy;
    elements.refreshButton.classList.toggle('is-spinning', busy);
  }

  /* ------------------------------------------------------------------ */
  /* 提交                                                                */
  /* ------------------------------------------------------------------ */

  function showComposerError(message) {
    elements.composerError.textContent = message;
    elements.composerError.hidden = false;
  }

  function clearComposerError() {
    if (!elements.composerError.hidden) {
      elements.composerError.hidden = true;
      elements.composerError.textContent = '';
    }
  }

  function updateUrlPreview() {
    const raw = elements.urlInput.value.trim();
    if (!raw) {
      elements.urlPreview.hidden = true;
      elements.urlPreview.innerHTML = '';
      return;
    }
    const url = extractFirstUrl(raw);
    const platform = detectPlatform(url || raw);
    elements.urlPreview.hidden = false;
    if (url) {
      elements.urlPreview.innerHTML = `${icon('link', { size: 14 })}<span>识别到 ${escapeHtml(shortenUrl(url, 56))}</span>${platformChip(platform)}`;
    } else {
      elements.urlPreview.innerHTML = `${icon('alert', { size: 14 })}<span>没有识别到链接，仍可发送，Relay 会再解析一次</span>`;
    }
  }

  function fillDraft() {
    let text = '';
    const queryDraft = ctx.query.get('draft');
    if (queryDraft && queryDraft !== '1') text = queryDraft;
    else {
      const stored = takeDraft();
      if (stored) text = stored.text;
    }
    if (ctx.query.has('draft')) {
      try {
        history.replaceState(null, '', `${location.pathname}${location.search}#/home`);
      } catch (error) {
        // 忽略
      }
    }
    if (!text) return;
    elements.urlInput.value = text.slice(0, MAX_SHARE_TEXT);
    if (queryDraft) clearDraft();
    updateUrlPreview();
    clearComposerError();
    toast('已填入分享内容，确认链接后发送');
  }

  function highlightTask(taskId) {
    const node = elements.taskList.querySelector(`[data-task-id="${taskId}"]`);
    if (!node) return;
    node.classList.add('is-new');
    node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    setTimeout(() => node.classList.remove('is-new'), 2600);
  }

  elements.submitForm.addEventListener(
    'submit',
    async (event) => {
      event.preventDefault();
      if (state.submitting) return;
      clearComposerError();

      const fitted = fitSubmitText(elements.urlInput.value);
      if (!fitted.ok) {
        showComposerError(
          fitted.reason === 'empty'
            ? '请先粘贴视频链接'
            : `内容太长了（上限 2048 字符），请只粘贴链接`,
        );
        elements.urlInput.focus();
        return;
      }

      const clientKey = uuid();
      state.submitting = true;
      setButtonBusy(elements.submitButton, true, { label: '正在发送…' });

      const sendToQueue = () => {
        enqueueUrl(fitted.text, { clientKey });
        elements.urlInput.value = '';
        updateUrlPreview();
        renderPending();
        toast('已存到本地队列，联网后自动发送', { type: 'error' });
      };

      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        sendToQueue();
        state.submitting = false;
        if (!signal.aborted) setButtonBusy(elements.submitButton, false);
        return;
      }

      try {
        const data = await api.createTask({
          url: fitted.text,
          clientKey,
          base: state.base,
          signal,
          timeout: 20000,
        });
        const task = data && data.task ? data.task : null;
        if (task) {
          taskCache.upsert(state.base, task);
          state.tasks = taskCache.read(state.base).items;
          state.offline = false;
          renderTasks();
          renderOffline();
        }
        elements.urlInput.value = '';
        updateUrlPreview();
        toast('已发送，等待电脑领取任务');
        if (task) highlightTask(task.id);
        void loadTasks({ silent: true });
      } catch (error) {
        if (error && error.code === 'aborted') return;
        if (error && error.code === 'unauthorized') return;
        if (isNetworkError(error)) {
          sendToQueue();
        } else {
          showComposerError(error.message || '发送失败，请稍后重试');
        }
      } finally {
        state.submitting = false;
        if (!signal.aborted) setButtonBusy(elements.submitButton, false);
      }
    },
    { signal },
  );

  elements.urlInput.addEventListener(
    'input',
    () => {
      clearComposerError();
      updateUrlPreview();
    },
    { signal },
  );

  /* ------------------------------------------------------------------ */
  /* 数据加载                                                            */
  /* ------------------------------------------------------------------ */

  async function loadMe() {
    if (state.loadingMe) return;
    state.loadingMe = true;
    try {
      const data = await api.me({ base: state.base, signal, timeout: 10000 });
      if (data) {
        if (data.device) {
          state.device = data.device;
          auth.update(state.base, { device: data.device });
        }
        if (data.agent) {
          state.agent = data.agent;
          auth.update(state.base, { agent: data.agent });
        }
      }
      state.meError = '';
      renderPill();
      renderAgentCard();
    } catch (error) {
      if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
      state.meError = error.message || '读取电脑状态失败';
      if (isNetworkError(error)) {
        state.offline = true;
        renderOffline();
      }
      renderAgentCard();
    } finally {
      state.loadingMe = false;
    }
  }

  async function loadTasks({ silent = false, append = false } = {}) {
    if (state.loadingTasks) return;
    state.loadingTasks = true;
    if (!silent && !state.tasks.length) renderTasks();
    setRefreshBusy(true);
    try {
      const data = await api.listTasks({
        limit: LIST_LIMIT,
        before: append ? state.nextBefore : null,
        base: state.base,
        signal,
        timeout: 12000,
      });
      const items = data && Array.isArray(data.items) ? data.items : [];
      // 合并服务端结果与本地缓存：刚提交的任务不会因为竞态而从列表消失。
      const merged = taskCache.merge(state.base, items);
      state.tasks = merged.items;
      state.fetchedAt = merged.fetchedAt;
      state.nextBefore = (data && data.next_before) || null;
      state.listError = '';
      state.offline = false;
      renderTasks();
      renderOffline();
    } catch (error) {
      if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
      state.listError = error.message || '加载任务列表失败';
      if (isNetworkError(error)) {
        state.offline = true;
        renderOffline();
      }
      renderTasks();
    } finally {
      state.loadingTasks = false;
      setRefreshBusy(false);
    }
  }

  /* ------------------------------------------------------------------ */
  /* 事件绑定                                                            */
  /* ------------------------------------------------------------------ */

  elements.openSettings.addEventListener('click', () => navigate('/settings'), { signal });

  elements.refreshButton.addEventListener(
    'click',
    () => {
      void loadMe();
      void loadTasks();
    },
    { signal },
  );

  elements.loadMore.addEventListener(
    'click',
    () => {
      if (!state.nextBefore) return;
      void loadTasks({ append: true });
    },
    { signal },
  );

  elements.pendingSendNow.addEventListener(
    'click',
    () => {
      toast('正在尝试发送队列…');
      void flushQueue();
    },
    { signal },
  );

  elements.pendingList.addEventListener(
    'click',
    (event) => {
      const button = event.target instanceof Element ? event.target.closest('[data-pending-action]') : null;
      if (!button) return;
      const clientKey = button.getAttribute('data-client-key') || '';
      if (button.getAttribute('data-pending-action') === 'retry') retryQueued(clientKey);
      else discardQueued(clientKey);
      renderPending();
    },
    { signal },
  );

  const onOnline = () => {
    state.offline = false;
    renderOffline();
    renderPending();
    void loadMe();
    void loadTasks({ silent: true });
  };
  const onOffline = () => {
    state.offline = true;
    renderOffline();
    renderPending();
  };
  window.addEventListener('online', onOnline, { signal });
  window.addEventListener('offline', onOffline, { signal });

  const onVisibility = () => {
    if (document.visibilityState === 'visible') {
      void loadMe();
      void loadTasks({ silent: true });
    }
  };
  document.addEventListener('visibilitychange', onVisibility, { signal });

  const refreshTimer = setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    void loadMe();
    void loadTasks({ silent: true });
  }, INBOX_REFRESH_MS);

  const unsubscribers = [
    onQueueEvent('change', () => {
      renderPending();
    }),
    onQueueEvent('sent', ({ task }) => {
      if (task && task.id) {
        taskCache.upsert(state.base, task);
        state.tasks = taskCache.read(state.base).items;
        renderTasks();
        highlightTask(task.id);
      }
      toast('本地队列里的任务已发出', { type: 'success' });
      void loadTasks({ silent: true });
    }),
    onQueueEvent('failed', ({ error, permanent }) => {
      if (permanent) toast(`发送失败：${error && error.message ? error.message : '请检查链接'}`, { type: 'error' });
      renderPending();
    }),
    onQueueEvent('auth-error', () => {
      // 全局 401 处理器会负责跳转，这里只需刷新队列展示。
      renderPending();
    }),
  ];

  /* ------------------------------------------------------------------ */
  /* 首次渲染与初始加载                                                   */
  /* ------------------------------------------------------------------ */

  renderAll();
  fillDraft();
  updateUrlPreview();
  if (!state.tasks.length) renderTasks();

  void Promise.allSettled([loadMe(), loadTasks()]);

  return () => {
    clearInterval(refreshTimer);
    unsubscribers.forEach((unsub) => {
      try {
        unsub();
      } catch (error) {
        // 忽略
      }
    });
  };
}

/**
 * 链记 LinkNote — 收件箱
 *
 * 实时架构（docs/realtime-protocol.md）：
 * - 任务状态/电脑在线状态由 WebSocket 推送，页面不再 3s/15s 轮询。
 * - 首次进入 / 重连成功后只做一次 REST 快照兜底，避免白屏。
 * - 提交任务仍走 REST（POST /tasks），之后等待 WS 的 task_updated。
 */
import { api, isNetworkError } from '../api.js';
import {
  LIST_LIMIT,
  MAX_SHARE_TEXT,
  PLATFORM_META,
  STAGE_LABELS,
  STATUS_META,
  SUPPORTED_PLATFORMS_TEXT,
} from '../config.js';
import {
  describeRealtime,
  getRealtimeSnapshot,
  isLegacyMode,
  onRealtime,
  probeRealtime,
  startRealtime,
} from '../realtime.js';
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

/** 顶部胶囊：实时在线状态（连接中/重连中/在线/离线） */
function pillInfo(state) {
  const rt = getRealtimeSnapshot();
  if (rt.mode === 'legacy') {
    // 旧 Relay 无实时连接：退回 REST 快照里的 agent.online。
    const online = state.agent && typeof state.agent.online === 'boolean' ? state.agent.online : null;
    if (online === true) return { label: '电脑在线', tone: 'online', detail: '兼容模式：按需刷新电脑状态' };
    if (online === false) return { label: '电脑离线', tone: 'offline', detail: '兼容模式：按需刷新电脑状态' };
    return { label: '兼容模式', tone: 'legacy', detail: '当前 Relay 不支持实时推送' };
  }
  return describeRealtime(rt);
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
          <span id="agent-pill" class="status-pill" data-state="unknown" aria-live="polite">
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

        <p class="home-footnote">笔记生成在你自己电脑上的 BiliNote 完成；状态与笔记正文通过加密连接实时推送到这台手机。</p>
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
    const info = pillInfo(state);
    const tone = info.tone === 'legacy' ? 'offline' : info.tone;
    elements.agentPill.setAttribute('data-state', tone);
    elements.agentPill.innerHTML = `<span class="status-dot" aria-hidden="true"></span>${escapeHtml(info.label)}`;
    elements.agentPill.title = info.detail ? `${info.label} · ${info.detail}` : info.label;
  }

  function currentPresenceOnline() {
    const rt = getRealtimeSnapshot();
    if (typeof rt.presence.online === 'boolean') return rt.presence.online;
    return state.agent ? state.agent.online === true : null;
  }

  function renderAgentCard() {
    const agent = state.agent;
    const rt = getRealtimeSnapshot();
    const rtDown = rt.mode !== 'legacy' && (rt.state === 'connecting' || rt.state === 'reconnecting');
    const online = currentPresenceOnline();
    const name = (agent && agent.name) || '我的电脑';
    const lastSeenValue =
      rt.presence.lastSeen || (agent && agent.last_seen_at ? agent.last_seen_at : null);
    const lastSeen = lastSeenValue ? formatRelativeTime(lastSeenValue) : '';

    let subText;
    if (rtDown) subText = rt.state === 'connecting' ? '正在连接电脑…' : '实时连接中断，正在自动重连…';
    else if (online === true) subText = '在线，随时可以接收任务';
    else if (lastSeen) subText = `最近在线：${lastSeen}`;
    else subText = '还没有连接过';

    const pillHtml = rtDown
      ? `<span class="status-pill" data-state="${rt.state}"><span class="status-dot" aria-hidden="true"></span>${rt.state === 'connecting' ? '连接中' : '重连中'}</span>`
      : agentPill({ online: online === true });

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
          <span class="agent-icon ${online && !rtDown ? 'is-online' : ''}" aria-hidden="true">${icon(online && !rtDown ? 'cloud' : 'cloud-off', { size: 20 })}</span>
          <div class="agent-info">
            <p class="agent-name">${escapeHtml(name)}</p>
            <p class="agent-sub">${escapeHtml(subText)}</p>
          </div>
          ${pillHtml}
        </div>
        ${
          online && !rtDown
            ? ''
            : rtDown
              ? '<p class="agent-hint">实时连接中断，正在自动重连；任务会继续在电脑上处理，状态恢复后自动同步。</p>'
              : '<p class="agent-hint">电脑离线时任务会留在队列里，Agent 上线后自动开始生成。你可以在设置里查看配对状态。</p>'
        }`;
    }
    renderPill();
    const retry = elements.agentCard.querySelector('#agent-retry');
    if (retry) retry.addEventListener('click', () => void loadMe(), { signal });
  }

  function renderOffline() {
    const noNetwork = typeof navigator !== 'undefined' && navigator.onLine === false;
    const rt = getRealtimeSnapshot();
    const reconnecting =
      rt.mode !== 'legacy' && (rt.state === 'connecting' || rt.state === 'reconnecting');
    const restDown = state.offline;
    if (!noNetwork && !reconnecting && !restDown) {
      elements.offlineBanner.hidden = true;
      return;
    }
    elements.offlineBanner.hidden = false;
    const text = noNetwork
      ? '当前没有网络，显示的是本地缓存；新任务会先存到本地，联网后自动发送。'
      : reconnecting
        ? '实时连接中断，正在自动重连；显示的是本地缓存，恢复后自动同步。'
        : '连不上 Relay，显示的是本地缓存；新任务会先存到本地，恢复连接后自动发送。';
    elements.offlineBanner.innerHTML = `${icon('wifi-off', { size: 16 })}<span>${text}</span>`;
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
    const rt = getRealtimeSnapshot();
    const staled =
      state.offline ||
      (typeof navigator !== 'undefined' && navigator.onLine === false) ||
      (rt.mode !== 'legacy' && (rt.state === 'connecting' || rt.state === 'reconnecting'));
    if (staled && state.fetchedAt) {
      elements.cacheNote.textContent = `缓存 · ${formatRelativeTime(new Date(state.fetchedAt).toISOString())}`;
      elements.cacheNote.hidden = false;
    } else {
      elements.cacheNote.textContent = '';
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
        // 不轮询：创建响应先展示 queued，后续进度由 WS task_updated 推送。
        toast('已发送，等待电脑处理');
        if (task) highlightTask(task.id);
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
  /* REST 快照（首屏 / 重连后 / 手动刷新；不是轮询）                      */
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
  /* WebSocket 实时更新                                                  */
  /* ------------------------------------------------------------------ */

  async function applyRealtimeTask(update) {
    if (!update || typeof update.id !== 'string' || !update.id) return;
    const known = state.tasks.some((item) => item.id === update.id) || taskCache.find(state.base, update.id);
    taskCache.upsert(state.base, update);
    if (!known) {
      // 未知任务：用一次 REST 详情补全 url/platform/created_at（不是轮询）。
      try {
        const data = await api.getTask(update.id, { base: state.base, signal, timeout: 10000 });
        if (data && data.task) taskCache.upsert(state.base, data.task);
      } catch (error) {
        // 快照失败就先用 WS 给的字段展示
      }
    }
    state.tasks = taskCache.read(state.base).items;
    renderTasks();
    if (update.status === 'succeeded') {
      toast('笔记已生成，点开任务查看', { type: 'success' });
    } else if (update.status === 'failed') {
      toast(`任务失败：${update.error_message || '打开任务查看原因'}`, { type: 'error' });
    }
  }

  async function snapshotAfterReconnect() {
    // 重连成功 / 首次 hello 后做一次 REST 快照兜底，然后继续走 WS。
    await Promise.allSettled([loadMe(), loadTasks({ silent: true })]);
  }

  const unsubRealtimeEvents = [
    onRealtime('state', () => {
      renderPill();
      renderAgentCard();
      renderOffline();
      renderCacheNote();
    }),
    onRealtime('presence', ({ online, lastSeen }) => {
      const knownAgent = state.agent || { name: '我的电脑' };
      state.agent = {
        ...knownAgent,
        online: Boolean(online),
        last_seen_at: lastSeen || knownAgent.last_seen_at || null,
      };
      renderPill();
      renderAgentCard();
    }),
    onRealtime('hello', () => {
      void snapshotAfterReconnect();
    }),
    onRealtime('legacy', () => {
      renderPill();
      renderAgentCard();
      renderOffline();
      renderCacheNote();
      // 旧 Relay：只用一次 REST 快照，不轮询。
      void Promise.allSettled([loadMe(), loadTasks({ silent: true })]);
    }),
    onRealtime('task_updated', ({ task }) => {
      void applyRealtimeTask(task);
    }),
    onRealtime('ticket-rejected', () => {
      toast('实时连接暂时不可用，正在自动重试', { type: 'error' });
    }),
  ];

  /* ------------------------------------------------------------------ */
  /* 事件绑定                                                            */
  /* ------------------------------------------------------------------ */

  elements.openSettings.addEventListener('click', () => navigate('/settings'), { signal });

  elements.refreshButton.addEventListener(
    'click',
    () => {
      probeRealtime();
      void Promise.allSettled([loadMe(), loadTasks()]);
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
    // WS 会自动重连并在 hello 后做快照；旧 Relay 兼容模式下补一次快照。
    if (isLegacyMode()) void loadTasks({ silent: true });
  };
  const onOffline = () => {
    state.offline = true;
    renderOffline();
    renderPending();
  };
  window.addEventListener('online', onOnline, { signal });
  window.addEventListener('offline', onOffline, { signal });

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
    }),
    onQueueEvent('failed', ({ error, permanent }) => {
      if (permanent) toast(`发送失败：${error && error.message ? error.message : '请检查链接'}`, { type: 'error' });
      renderPending();
    }),
    onQueueEvent('auth-error', () => {
      renderPending();
    }),
  ];

  /* ------------------------------------------------------------------ */
  /* 首次渲染与初始快照                                                   */
  /* ------------------------------------------------------------------ */

  renderAll();
  fillDraft();
  updateUrlPreview();
  if (!state.tasks.length) renderTasks();

  // 确保实时连接已建立（app.js 启动时可能早于配对完成）。
  startRealtime();

  // 首屏只做一次 REST 快照；此后由 WS 推送更新。
  void Promise.allSettled([loadMe(), loadTasks()]);

  return () => {
    unsubscribers.forEach((unsub) => {
      try {
        unsub();
      } catch (error) {
        // 忽略
      }
    });
    unsubRealtimeEvents.forEach((unsub) => {
      try {
        unsub();
      } catch (error) {
        // 忽略
      }
    });
  };
}

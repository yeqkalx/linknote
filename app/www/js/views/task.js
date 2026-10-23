/**
 * 链记 LinkNote — 任务详情 / 阅读器
 * - 前台每 3 秒轮询 GET /tasks/{id}（页面隐藏时暂停）
 * - 成功后 GET /note，用 marked + DOMPurify 渲染
 * - 操作：复制全文、下载 .md、系统分享、重试、移除、返回
 */
import { api, isNetworkError } from '../api.js';
import {
  POLL_INTERVAL_MS,
  STAGES,
  STAGE_LABELS,
  TERMINAL_STATUSES,
} from '../config.js';
import { renderMarkdown } from '../markdown.js';
import { navigate } from '../router.js';
import { auth, noteCache, settings, taskCache } from '../store.js';
import {
  confirmDialog,
  icon,
  platformChip,
  statusChip,
  toast,
} from '../ui.js';
import {
  copyText,
  downloadTextFile,
  elapsedBetween,
  escapeHtml,
  formatDateTime,
  formatElapsed,
  formatRelativeTime,
  hostOf,
  safeFilename,
  truncate,
} from '../util.js';

function isTerminal(task) {
  return Boolean(task && TERMINAL_STATUSES.includes(task.status));
}

function stageIndex(stage) {
  return STAGES.findIndex((item) => item.key === stage);
}

function sourceUrlOf(task) {
  if (!task || typeof task.url !== 'string') return '';
  const value = task.url.trim();
  if (/^https?:\/\//i.test(value)) return value;
  return '';
}

function timelineHtml(task) {
  const currentIndex = stageIndex(task.stage);
  const done = task.status === 'succeeded' || task.stage === 'done';
  return `
    <section class="card timeline-card">
      <h2 class="card-title">生成阶段</h2>
      <ol class="timeline">
        ${STAGES.map((stage, index) => {
          let state = 'pending';
          if (done) state = 'done';
          else if (currentIndex >= 0 && index < currentIndex) state = 'done';
          else if (currentIndex >= 0 && index === currentIndex) state = 'active';
          const dot =
            state === 'done'
              ? icon('check', { size: 12 })
              : state === 'active'
                ? icon('sparkles', { size: 12 })
                : '';
          return `
            <li class="timeline-item is-${state}">
              <span class="timeline-dot" aria-hidden="true">${dot}</span>
              <div class="timeline-body">
                <span class="timeline-label">${escapeHtml(stage.label)}</span>
                <span class="timeline-hint">${escapeHtml(stage.hint)}</span>
              </div>
            </li>`;
        }).join('')}
      </ol>
    </section>`;
}

function agentStatusHtml(agent) {
  if (!agent) return '';
  const online = agent.online === true;
  return `
    <div class="task-agent ${online ? '' : 'is-offline'}">
      <span class="agent-icon ${online ? 'is-online' : ''}" aria-hidden="true">${icon(online ? 'cloud' : 'cloud-off', { size: 18 })}</span>
      <div class="agent-info">
        <p class="agent-name">${escapeHtml(agent.name || '我的电脑')}</p>
        <p class="agent-sub">${online ? '电脑在线' : agent.last_seen_at ? `最近在线：${escapeHtml(formatRelativeTime(agent.last_seen_at))}` : '电脑当前离线'}</p>
      </div>
    </div>`;
}

export async function taskView(container, ctx) {
  const signal = ctx.signal;
  const taskId = ctx.params.id || '';
  const base = settings.getApiBase();
  const authEntry = base ? auth.get(base) : null;

  if (!authEntry) {
    navigate('/pair', { replace: true });
    return () => {};
  }
  if (!taskId) {
    navigate('/home', { replace: true });
    return () => {};
  }

  let task = taskCache.find(base, taskId);
  let agent = authEntry.agent || null;
  let note = noteCache.read(base, taskId);
  let noteError = '';
  let stateError = '';
  let removed = false;
  let noteLoading = false;
  let refreshing = false;
  let disposed = false;
  let pollTimer = null;
  let actionBusy = false;

  container.innerHTML = `
    <section class="screen task-screen">
      <header class="appbar">
        <button type="button" class="icon-btn" id="back-btn" aria-label="返回收件箱">${icon('back', { size: 20 })}</button>
        <div class="appbar-title">任务详情</div>
        <button type="button" class="icon-btn" id="manual-refresh" aria-label="刷新任务状态">${icon('refresh', { size: 20 })}</button>
      </header>
      <main class="task-main" id="task-main"></main>
      <div class="action-bar" id="action-bar" hidden></div>
    </section>`;

  const main = container.querySelector('#task-main');
  const actionBar = container.querySelector('#action-bar');
  const refreshButton = container.querySelector('#manual-refresh');

  /* ------------------------------------------------------------------ */
  /* 渲染                                                                */
  /* ------------------------------------------------------------------ */

  function renderActionBar() {
    if (!task) {
      actionBar.hidden = true;
      actionBar.innerHTML = '';
      return;
    }
    const actions = [];
    if (task.status === 'succeeded') {
      actions.push(
        { action: 'copy', label: '复制', iconName: 'copy' },
        { action: 'download', label: '下载', iconName: 'download' },
        { action: 'share', label: '分享', iconName: 'share' },
        { action: 'retry', label: '重试', iconName: 'retry' },
        { action: 'remove', label: '移除', iconName: 'trash', quietDanger: true },
      );
    } else if (task.status === 'failed') {
      actions.push(
        { action: 'retry', label: '重试', iconName: 'retry', primary: true },
        { action: 'remove', label: '移除', iconName: 'trash', quietDanger: true },
        { action: 'back', label: '返回', iconName: 'back' },
      );
    }
    if (!actions.length) {
      actionBar.hidden = true;
      actionBar.innerHTML = '';
      return;
    }
    actionBar.hidden = false;
    actionBar.innerHTML = actions
      .map(
        (item) => `
        <button type="button" class="action-btn ${item.primary ? 'is-primary' : ''} ${item.quietDanger ? 'is-quiet-danger' : ''}" data-action="${item.action}">
          ${icon(item.iconName, { size: 20 })}
          <span>${escapeHtml(item.label)}</span>
        </button>`,
      )
      .join('');
  }

  function noteReaderHtml() {
    const title = (note && note.title) || task.title || '未命名笔记';
    const completedAt = (note && note.completed_at) || task.completed_at || null;
    const markdown = note ? note.markdown : '';
    const sizeText = markdown ? `约 ${markdown.length} 字` : '';
    const cachedText = stateError ? ' · 离线缓存' : '';
    return `
      <article class="note-card card">
        <header class="note-head">
          <div class="note-meta-row">
            ${platformChip(task.platform)}
            <span>${escapeHtml(formatDateTime(completedAt) || '已完成')}</span>
          </div>
          <h1 class="note-title">${escapeHtml(title)}</h1>
          ${sizeText || cachedText ? `<p class="note-sub">${sizeText}${cachedText}</p>` : ''}
        </header>
        <div class="reader" id="reader">${renderMarkdown(markdown)}</div>
      </article>`;
  }

  function succeededHtml() {
    if (note) {
      return `${stateErrorBanner()}${noteReaderHtml()}`;
    }
    if (noteError) {
      return `${stateErrorBanner()}<div class="card error-card">
        <h2 class="error-title">还没读到笔记</h2>
        <p class="error-text">${escapeHtml(noteError)}</p>
        <button type="button" class="btn btn-primary btn-block" data-action="reload-note">重新获取笔记</button>
      </div>`;
    }
    return `${stateErrorBanner()}<div class="card note-loading" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <span>正在获取笔记…</span>
    </div>`;
  }

  function stateErrorBanner() {
    if (!stateError) return '';
    return `<div class="banner banner-warn" role="status">${icon('wifi-off', { size: 16 })}<span>${escapeHtml(stateError)}</span></div>`;
  }

  function progressHtml() {
    const progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
    const message = task.message || STAGE_LABELS[task.stage] || '处理中';
    const elapsed = elapsedBetween(task.created_at);
    const source = sourceUrlOf(task);
    return `
      ${stateErrorBanner()}
      <section class="card status-card">
        <div class="status-head">
          ${statusChip(task.status)}
          <span class="status-percent">${progress}%</span>
        </div>
        <div class="progress progress-lg" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}">
          <span class="progress-fill" style="width:${progress}%"></span>
        </div>
        <p class="status-message">${escapeHtml(message)}</p>
        <div class="status-meta">
          <span class="status-time">${icon('clock', { size: 14 })}<span>已等待 <span id="elapsed-time">${escapeHtml(formatElapsed(elapsed))}</span></span></span>
          <span>${escapeHtml(formatRelativeTime(task.updated_at || task.created_at))}更新</span>
        </div>
        ${task.title ? `<p class="status-video-title">${escapeHtml(truncate(task.title, 80))}</p>` : ''}
        ${
          source
            ? `<a class="source-link" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${icon('external', { size: 14 })}<span>${escapeHtml(truncate(source, 64))}</span></a>`
            : ''
        }
      </section>
      ${agentStatusHtml(agent)}
      ${
        agent && agent.online === false
          ? `<div class="banner banner-warn">${icon('cloud-off', { size: 16 })}<span>电脑当前离线，任务会在 Agent 上线后继续，不需要一直开着这个页面。</span></div>`
          : ''
      }
      ${timelineHtml(task)}`;
  }

  function failedHtml() {
    const source = sourceUrlOf(task);
    return `
      ${stateErrorBanner()}
      <section class="card fail-card">
        <div class="fail-head">
          <span class="fail-icon" aria-hidden="true">${icon('alert', { size: 22 })}</span>
          <div>
            <h2 class="error-title">生成失败</h2>
            <p class="fail-sub">${escapeHtml(formatRelativeTime(task.completed_at || task.updated_at))}结束</p>
          </div>
        </div>
        <p class="fail-message">${escapeHtml(task.error_message || '电脑端没有返回具体原因，可以重试一次。')}</p>
        ${task.error_code ? `<p class="fail-code">错误码：${escapeHtml(task.error_code)}</p>` : ''}
        ${
          source
            ? `<a class="source-link" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${icon('external', { size: 14 })}<span>${escapeHtml(truncate(source, 64))}</span></a>`
            : ''
        }
        <p class="fail-hint">可以点下面的「重试」重新生成；如果一直失败，请检查电脑上的 BiliNote 与 Agent 是否正常。</p>
      </section>
      ${agentStatusHtml(agent)}`;
  }

  function missingHtml() {
    return `<div class="card error-card">
      <h1 class="error-title">任务不存在</h1>
      <p class="error-text">${escapeHtml(removed ? '这个任务已经被移除，或者不属于当前设备。' : '找不到这个任务，可能已经被移除。')}</p>
      <a class="btn btn-primary btn-block" href="#/home">回到收件箱</a>
    </div>`;
  }

  function skeletonHtml() {
    return `<div class="card task-loading" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <span>正在加载任务…</span>
    </div>`;
  }

  function render() {
    if (disposed) return;
    refreshButton.classList.toggle('is-spinning', refreshing);
    if (!task) {
      main.innerHTML = removed ? missingHtml() : skeletonHtml();
      renderActionBar();
      return;
    }
    if (task.status === 'succeeded') {
      main.innerHTML = succeededHtml();
      removeDuplicateTitle();
    } else if (task.status === 'failed') main.innerHTML = failedHtml();
    else main.innerHTML = progressHtml();
    renderActionBar();
  }

  /** 阅读器里正文通常自带 H1 标题，避免和卡片标题重复。 */
  function removeDuplicateTitle() {
    const reader = main.querySelector('#reader');
    if (!reader) return;
    const first = reader.firstElementChild;
    if (!first || first.tagName !== 'H1') return;
    const noteTitle = String((note && note.title) || task.title || '').trim();
    const firstText = String(first.textContent || '').trim();
    if (noteTitle && firstText === noteTitle) first.remove();
  }

  /* ------------------------------------------------------------------ */
  /* 数据加载                                                            */
  /* ------------------------------------------------------------------ */

  function schedulePoll(delay = POLL_INTERVAL_MS) {
    clearTimeout(pollTimer);
    if (disposed || isTerminal(task)) return;
    pollTimer = setTimeout(() => {
      if (document.visibilityState === 'visible') void refreshTask();
      else schedulePoll();
    }, delay);
  }

  async function refreshTask({ manual = false } = {}) {
    if (disposed || refreshing) return;
    refreshing = true;
    refreshButton.classList.add('is-spinning');
    try {
      const data = await api.getTask(taskId, { base, signal, timeout: 12000 });
      if (data && data.task) {
        task = data.task;
        taskCache.upsert(base, task);
      }
      if (data && data.agent) {
        agent = data.agent;
        auth.update(base, { agent });
      }
      stateError = '';
      render();
      if (task && task.status === 'succeeded' && !note && !noteLoading) {
        void loadNote();
      }
      schedulePoll();
    } catch (error) {
      if (error && error.code === 'aborted') return;
      if (error && error.code === 'unauthorized') return;
      if (error && error.status === 404) {
        removed = true;
        task = null;
        render();
        renderActionBar();
        return;
      }
      stateError = `${error.message || '刷新失败'}，显示的是上次状态`;
      if (isNetworkError(error)) stateError = '连不上 Relay，显示的是上次状态';
      render();
      schedulePoll(POLL_INTERVAL_MS * 2);
    } finally {
      refreshing = false;
      if (!disposed) refreshButton.classList.remove('is-spinning');
      if (manual) refreshButton.disabled = false;
    }
  }

  async function loadNote() {
    if (disposed || noteLoading) return;
    noteLoading = true;
    render();
    try {
      const data = await api.getNote(taskId, { base, signal, timeout: 30000 });
      if (!data || typeof data.markdown !== 'string') {
        throw new Error('笔记内容为空');
      }
      note = {
        title: data.title || (task && task.title) || '',
        markdown: data.markdown,
        completed_at: data.completed_at || (task && task.completed_at) || null,
        cachedAt: Date.now(),
      };
      noteError = '';
      noteCache.write(base, taskId, note);
    } catch (error) {
      if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
      if (error && error.status === 409) noteError = '笔记还在准备中，请稍后重试';
      else if (isNetworkError(error) && note) noteError = '';
      else noteError = error.message || '获取笔记失败';
    } finally {
      noteLoading = false;
      if (!disposed) render();
    }
  }

  /* ------------------------------------------------------------------ */
  /* 操作                                                                */
  /* ------------------------------------------------------------------ */

  async function handleCopy() {
    if (!note) return;
    const ok = await copyText(note.markdown);
    toast(ok ? '已复制 Markdown 全文' : '复制失败，请长按选择文本', { type: ok ? 'success' : 'error' });
  }

  function handleDownload() {
    if (!note) return;
    const title = note.title || task.title || '链记笔记';
    const filename = `${safeFilename(title, '链记笔记')}.md`;
    const ok = downloadTextFile(filename, note.markdown);
    toast(ok ? `已下载 ${filename}` : '下载失败，请改用复制', { type: ok ? 'success' : 'error' });
  }

  async function handleShare() {
    if (!note) return;
    const title = note.title || task.title || '链记笔记';
    const text = note.markdown;
    const canShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function';
    if (!canShare) {
      const ok = await copyText(text);
      toast(ok ? '系统分享不可用，已复制全文' : '分享不可用', { type: ok ? 'info' : 'error' });
      return;
    }
    try {
      if (typeof File === 'function' && navigator.canShare) {
        const file = new File([text], `${safeFilename(title)}.md`, { type: 'text/markdown' });
        if (navigator.canShare({ files: [file] })) {
          await navigator.share({ title, files: [file] });
          return;
        }
      }
      await navigator.share({ title, text });
    } catch (error) {
      if (error && error.name === 'AbortError') return;
      const ok = await copyText(text);
      toast(ok ? '分享失败，已复制全文' : '分享失败', { type: ok ? 'info' : 'error' });
    }
  }

  async function handleRetry() {
    if (actionBusy) return;
    const confirmed = await confirmDialog({
      title: '重新生成笔记？',
      message: '会创建一个新任务，用同一个视频链接重新生成。原来的任务会保留。',
      confirmText: '重新生成',
    });
    if (!confirmed) return;
    actionBusy = true;
    try {
      const data = await api.retryTask(taskId, { base, signal });
      const nextTask = data && data.task ? data.task : null;
      if (nextTask && nextTask.id) {
        taskCache.upsert(base, nextTask);
        toast('已创建新任务');
        navigate(`/task/${encodeURIComponent(nextTask.id)}`, { replace: true });
      } else {
        toast('已创建新任务');
        navigate('/home', { replace: true });
      }
    } catch (error) {
      if (error && error.code === 'aborted') return;
      if (error && error.code === 'unauthorized') return;
      if (error && error.status === 404) {
        removed = true;
        task = null;
        render();
        renderActionBar();
        toast('任务不存在或已被移除', { type: 'error' });
        return;
      }
      toast(error.message || '重试失败，请稍后再试', { type: 'error' });
    } finally {
      actionBusy = false;
    }
  }

  async function handleRemove() {
    if (actionBusy) return;
    const confirmed = await confirmDialog({
      title: '从列表移除任务？',
      message: '移除后任务不再出现在收件箱里，笔记数据仍保留在 Relay。',
      confirmText: '移除',
      danger: true,
    });
    if (!confirmed) return;
    actionBusy = true;
    try {
      await api.removeTask(taskId, { base, signal });
      taskCache.remove(base, taskId);
      noteCache.remove(base, taskId);
      toast('已从列表移除');
      navigate('/home', { replace: true });
    } catch (error) {
      if (error && error.code === 'aborted') return;
      if (error && (error.status === 404 || error.code === 'unauthorized')) {
        taskCache.remove(base, taskId);
        noteCache.remove(base, taskId);
        navigate('/home', { replace: true });
        return;
      }
      toast(error.message || '移除失败，请稍后再试', { type: 'error' });
    } finally {
      actionBusy = false;
    }
  }

  async function handleAction(action) {
    switch (action) {
      case 'copy':
        await handleCopy();
        break;
      case 'download':
        handleDownload();
        break;
      case 'share':
        await handleShare();
        break;
      case 'retry':
        await handleRetry();
        break;
      case 'remove':
        await handleRemove();
        break;
      case 'back':
        navigate('/home');
        break;
      case 'reload-note':
        if (noteError) noteError = '';
        void loadNote();
        break;
      default:
        break;
    }
  }

  main.addEventListener(
    'click',
    (event) => {
      const target = event.target instanceof Element ? event.target.closest('[data-action]') : null;
      if (!target) return;
      const action = target.getAttribute('data-action') || '';
      if (action) void handleAction(action);
    },
    { signal },
  );

  actionBar.addEventListener(
    'click',
    (event) => {
      const target = event.target instanceof Element ? event.target.closest('[data-action]') : null;
      if (!target) return;
      const action = target.getAttribute('data-action') || '';
      if (action) void handleAction(action);
    },
    { signal },
  );

  container.querySelector('#back-btn').addEventListener('click', () => navigate('/home'), { signal });

  refreshButton.addEventListener(
    'click',
    () => {
      refreshButton.disabled = true;
      void refreshTask({ manual: true }).finally(() => {
        if (!disposed) refreshButton.disabled = false;
      });
    },
    { signal },
  );

  const onVisibility = () => {
    if (document.visibilityState === 'visible' && !isTerminal(task)) void refreshTask();
  };
  document.addEventListener('visibilitychange', onVisibility, { signal });

  const elapsedTimer = setInterval(() => {
    const node = document.getElementById('elapsed-time');
    if (!node || !task || isTerminal(task)) return;
    node.textContent = formatElapsed(elapsedBetween(task.created_at));
  }, 1000);

  signal.addEventListener(
    'abort',
    () => {
      disposed = true;
      clearTimeout(pollTimer);
      clearInterval(elapsedTimer);
    },
    { once: true },
  );

  /* ------------------------------------------------------------------ */
  /* 启动                                                                */
  /* ------------------------------------------------------------------ */

  render();
  if (task && task.status === 'succeeded' && !note) {
    void loadNote();
  }
  // 有缓存先展示；后台刷新以服务端为准。
  void refreshTask();

  return () => {
    disposed = true;
    clearTimeout(pollTimer);
    clearInterval(elapsedTimer);
  };
}

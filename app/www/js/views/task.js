/**
 * 链记 LinkNote — 任务详情 / 阅读器（v0.3）
 *
 * - 任务状态由 WS `task_updated` 推送，不轮询；主视图只保留三态摘要
 *   （等待电脑 / 获取内容 / 生成笔记），详细阶段折叠，不展示不可靠的百分比。
 * - 笔记正文只从 WS `note` 收：写入手机本地缓存（IndexedDB 优先）后由 realtime
 *   回 ack persisted；保存失败会提示“未保存到手机，可重试”并支持补存 + 补 ack。
 * - 阅读器底部精简为 3 组：复制 / 导出（下载+分享）/ 更多（固定、重新生成、移除…）。
 * - 电脑状态卡同时展示 readiness（在线 · 可生成 / 需要处理 N 项）。
 */
import { api, isNetworkError } from '../api.js';
import {
  READINESS_ISSUE_LABELS,
  REALTIME_NOTE_TIMEOUT_MS,
  STAGES,
  STAGE_LABELS,
  STAGE_PHASE,
  TASK_PHASES,
  TERMINAL_STATUSES,
} from '../config.js';
import { renderMarkdown } from '../markdown.js';
import {
  getRealtimeSnapshot,
  isLegacyMode,
  isRealtimeOpen,
  onRealtime,
  probeRealtime,
  requestNote,
  sendAckNote,
  startRealtime,
} from '../realtime.js';
import { navigate } from '../router.js';
import { auth, noteCache, settings, taskCache } from '../store.js';
import {
  confirmDialog,
  icon,
  openSheet,
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
  safeFilename,
  truncate,
} from '../util.js';

function isTerminal(task) {
  return Boolean(task && TERMINAL_STATUSES.includes(task.status));
}

function stageIndex(stage) {
  return STAGES.findIndex((item) => item.key === stage);
}

function phaseIndexOf(task) {
  if (!task) return 0;
  if (task.status === 'queued') return 0;
  const phase = STAGE_PHASE[task.stage] || 'generating';
  const index = TASK_PHASES.findIndex((item) => item.key === phase);
  return index >= 0 ? index : 0;
}

function sourceUrlOf(task) {
  if (!task || typeof task.url !== 'string') return '';
  const value = task.url.trim();
  if (/^https?:\/\//i.test(value)) return value;
  return '';
}

function issueLabelOf(issue) {
  if (issue && typeof issue.label === 'string' && issue.label.trim()) return issue.label.trim();
  const code = issue && typeof issue.code === 'string' ? issue.code : '';
  return READINESS_ISSUE_LABELS[code] || '需要处理一项设置';
}

function timelineListHtml(task) {
  const currentIndex = stageIndex(task.stage);
  const done = task.status === 'succeeded' || task.stage === 'done';
  return `
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
    </ol>`;
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
  let note = null;
  // idle | loading | waiting-connection | unavailable | timeout | ready
  let noteState = 'idle';
  let noteError = '';
  let noteSaved = null; // true=已保存到手机 false=保存失败 null=未知/未收到
  let noteSaveError = '';
  let pinned = false;
  let stateError = '';
  let removed = false;
  let disposed = false;
  let snapshotRefreshing = false;
  let noteRequestTimer = null;
  let actionBusy = false;

  container.innerHTML = `
    <section class="screen task-screen">
      <header class="appbar">
        <button type="button" class="icon-btn" id="back-btn" aria-label="返回收件箱">${icon('back', { size: 20 })}</button>
        <div class="appbar-title">任务详情</div>
        <span id="task-presence" class="status-pill status-pill-sm" data-state="unknown">
          <span class="status-dot" aria-hidden="true"></span>连接中
        </span>
        <button type="button" class="icon-btn" id="manual-refresh" aria-label="刷新任务状态">${icon('refresh', { size: 20 })}</button>
      </header>
      <main class="task-main" id="task-main"></main>
      <div class="action-bar" id="action-bar" hidden></div>
    </section>`;

  const main = container.querySelector('#task-main');
  const actionBar = container.querySelector('#action-bar');
  const refreshButton = container.querySelector('#manual-refresh');
  const presencePill = container.querySelector('#task-presence');

  /* ------------------------------------------------------------------ */
  /* 在线状态 / readiness                                                */
  /* ------------------------------------------------------------------ */

  function presenceOnline() {
    const rt = getRealtimeSnapshot();
    if (typeof rt.presence.online === 'boolean') return rt.presence.online;
    if (agent && typeof agent.online === 'boolean') return agent.online;
    return null;
  }

  function readinessInfo() {
    const rt = getRealtimeSnapshot();
    if (rt.mode !== 'legacy') {
      return {
        ready: rt.readiness.ready,
        issues: Array.isArray(rt.readiness.issues) ? rt.readiness.issues : [],
      };
    }
    if (agent && typeof agent.ready === 'boolean') {
      return { ready: agent.ready, issues: Array.isArray(agent.issues) ? agent.issues : [] };
    }
    return { ready: null, issues: [] };
  }

  function agentUi() {
    const rt = getRealtimeSnapshot();
    const online = presenceOnline();
    const rtDown = rt.mode !== 'legacy' && (rt.state === 'connecting' || rt.state === 'reconnecting');
    const baseAgent = agent || { name: '我的电脑' };
    return {
      ...baseAgent,
      online: online === true && !rtDown,
      reconnecting: rtDown,
      last_seen_at: rt.presence.lastSeen || baseAgent.last_seen_at || null,
    };
  }

  function agentStatusHtml(uiAgent) {
    if (!uiAgent) return '';
    const online = uiAgent.online === true;
    const reconnecting = uiAgent.reconnecting === true;
    const readiness = readinessInfo();
    const issues =
      online && !reconnecting && readiness.ready === false
        ? readiness.issues.length
          ? readiness.issues
          : [{ code: 'unknown', label: '需要处理一项设置' }]
        : [];
    let subText;
    if (reconnecting) subText = '实时连接中断，正在重连…';
    else if (!online) {
      subText = uiAgent.last_seen_at
        ? `最近在线：${escapeHtml(formatRelativeTime(uiAgent.last_seen_at))}`
        : '电脑当前离线';
    } else if (issues.length) subText = `在线 · 需要处理 ${issues.length} 项`;
    else if (readiness.ready === true) subText = '在线 · 可生成';
    else subText = '在线';
    return `
      <div class="task-agent ${online && !reconnecting ? '' : 'is-offline'}">
        <span class="agent-icon ${online && !reconnecting ? 'is-online' : ''}" aria-hidden="true">${icon(
          online && !reconnecting ? 'cloud' : 'cloud-off',
          { size: 18 },
        )}</span>
        <div class="agent-info">
          <p class="agent-name">${escapeHtml(uiAgent.name || '我的电脑')}</p>
          <p class="agent-sub">${subText}</p>
          ${
            issues.length
              ? `<ul class="agent-issue-list">${issues
                  .map((issue) => `<li>${escapeHtml(issueLabelOf(issue))}</li>`)
                  .join('')}</ul>`
              : ''
          }
        </div>
      </div>`;
  }

  function connectionBanner() {
    const rt = getRealtimeSnapshot();
    if (rt.mode === 'legacy') return '';
    if (rt.state === 'connecting') {
      return `<div class="banner banner-warn" role="status">${icon('refresh', { size: 16 })}<span>正在连接电脑…</span></div>`;
    }
    if (rt.state === 'reconnecting') {
      return `<div class="banner banner-warn" role="status">${icon('wifi-off', { size: 16 })}<span>实时连接中断，正在自动重连；状态与笔记会在恢复后自动同步。</span></div>`;
    }
    return '';
  }

  /** 顶栏实时 presence（在线/离线/连接中/重连中），明显但不抢眼 */
  function renderPresencePill() {
    if (!presencePill) return;
    const rt = getRealtimeSnapshot();
    const rtDown = rt.mode !== 'legacy' && (rt.state === 'connecting' || rt.state === 'reconnecting');
    let label = '连接中';
    let tone = 'connecting';
    if (rtDown) {
      label = rt.state === 'connecting' ? '连接中' : '重连中';
      tone = rt.state === 'connecting' ? 'connecting' : 'reconnecting';
    } else if (rt.mode === 'legacy') {
      const online = presenceOnline();
      label = online === true ? '在线' : online === false ? '离线' : '兼容';
      tone = online === true ? 'online' : 'offline';
    } else {
      const online = presenceOnline();
      label = online === true ? '在线' : online === false ? '离线' : '在线?';
      tone = online === true ? 'online' : 'offline';
    }
    presencePill.setAttribute('data-state', tone);
    presencePill.innerHTML = `<span class="status-dot" aria-hidden="true"></span>${escapeHtml(label)}`;
    presencePill.title = `电脑${label}`;
  }

  /* ------------------------------------------------------------------ */
  /* 渲染：操作栏 / 阅读器 / 三态摘要 / 失败                             */
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
        { action: 'export', label: '导出', iconName: 'export' },
        { action: 'more', label: '更多', iconName: 'more' },
      );
    } else if (task.status === 'failed') {
      actions.push(
        { action: 'regenerate', label: '重新生成', iconName: 'retry', primary: true },
        { action: 'more', label: '更多', iconName: 'more' },
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
        <button type="button" class="action-btn ${item.primary ? 'is-primary' : ''}" data-action="${item.action}">
          ${icon(item.iconName, { size: 20 })}
          <span>${escapeHtml(item.label)}</span>
        </button>`,
      )
      .join('');
  }

  function noteBadgesHtml() {
    if (!note) return '';
    const parts = [];
    if (pinned) parts.push(`<span class="note-badge is-pinned">${icon('pin', { size: 11 })}已固定</span>`);
    if (noteSaved === true) {
      parts.push(`<span class="note-badge is-saved">${icon('check', { size: 11 })}已保存到手机</span>`);
    } else if (noteSaved === false) {
      parts.push(`<span class="note-badge is-missing">${icon('alert', { size: 11 })}未保存到手机</span>`);
    }
    return parts.length ? `<div class="note-badges">${parts.join('')}</div>` : '';
  }

  function noteReaderHtml() {
    const title = (note && note.title) || task.title || '未命名笔记';
    const completedAt = (note && note.completed_at) || task.completed_at || null;
    const markdown = note ? note.markdown : '';
    const sizeText = markdown ? `约 ${markdown.length} 字` : '';
    const cacheText =
      !isRealtimeOpen() && note ? ' · 本地缓存' : isLegacyMode() ? ' · 兼容模式' : '';
    const saveWarning =
      noteSaved === false
        ? `<div class="banner banner-error note-save-warning" role="alert">
            ${icon('alert', { size: 16 })}
            <span>${escapeHtml(noteSaveError || '未保存到手机，可重试')}</span>
            <button type="button" class="btn btn-ghost btn-xs" data-action="retry-save">重试保存</button>
          </div>`
        : '';
    return `
      <article class="note-card card">
        <header class="note-head">
          <div class="note-meta-row">
            ${platformChip(task.platform)}
            <span>${escapeHtml(formatDateTime(completedAt) || '已完成')}</span>
          </div>
          <h1 class="note-title">${escapeHtml(title)}</h1>
          <p class="note-sub">${sizeText}${cacheText}</p>
          ${noteBadgesHtml()}
          ${saveWarning}
        </header>
        <div class="reader" id="reader">${renderMarkdown(markdown)}</div>
      </article>`;
  }

  function notePendingHtml() {
    if (noteState === 'loading') {
      return `<div class="card note-loading" role="status">
        <span class="spinner" aria-hidden="true"></span>
        <span>正在从电脑获取笔记…</span>
      </div>`;
    }
    if (noteState === 'waiting-connection') {
      return `<div class="card note-state" role="status">
        ${icon('cloud-off', { size: 22 })}
        <p class="note-state-title">正在连接电脑</p>
        <p class="note-state-text">实时连接建立后会自动向电脑请求这篇笔记，不需要停留在当前页面。</p>
        <button type="button" class="btn btn-ghost btn-sm" data-action="reload-note">立即重试</button>
      </div>`;
    }
    if (noteState === 'unavailable') {
      return `<div class="card note-state" role="status">
        ${icon('cloud-off', { size: 22 })}
        <p class="note-state-title">电脑离线，稍后重试</p>
        <p class="note-state-text">${escapeHtml(noteError || '笔记正文只保存在你的电脑和手机上，电脑上线后可以重新获取。')}</p>
        <button type="button" class="btn btn-primary btn-sm" data-action="reload-note">重试获取</button>
      </div>`;
    }
    return `<div class="card note-state" role="status">
      ${icon('file', { size: 22 })}
      <p class="note-state-title">还没有收到笔记</p>
      <p class="note-state-text">${escapeHtml(noteError || '正文可能还在从电脑发送中，可以再请求一次。')}</p>
      <button type="button" class="btn btn-primary btn-sm" data-action="reload-note">重新获取</button>
    </div>`;
  }

  function succeededHtml() {
    const body = note ? noteReaderHtml() : notePendingHtml();
    return `${connectionBanner()}${stateErrorBanner()}${body}`;
  }

  function stateErrorBanner() {
    if (!stateError) return '';
    return `<div class="banner banner-warn" role="status">${icon('wifi-off', { size: 16 })}<span>${escapeHtml(stateError)}</span></div>`;
  }

  /** v0.3 三态摘要：等待电脑 → 获取内容 → 生成笔记；不展示假百分比。 */
  function summaryHtml() {
    const phaseIndex = phaseIndexOf(task);
    const source = sourceUrlOf(task);
    const phase = TASK_PHASES[phaseIndex] || TASK_PHASES[0];
    const message =
      task.message || STAGE_LABELS[task.stage] || (task.status === 'queued' ? '排队中，电脑上线后会自动开始' : '处理中');
    const elapsed = elapsedBetween(task.created_at);
    const elapsedText = elapsed < 2500 ? '刚刚开始' : formatElapsed(elapsed);
    const uiAgent = agentUi();
    const presence = presenceOnline();
    return `
      ${connectionBanner()}
      ${stateErrorBanner()}
      <section class="card status-card task-summary">
        <div class="status-head">
          ${statusChip(task.status)}
          <span class="status-updated">${escapeHtml(formatRelativeTime(task.updated_at || task.created_at))}更新</span>
        </div>
        <ol class="phase-steps" aria-label="生成进度">
          ${TASK_PHASES.map((item, index) => {
            const phaseState = index < phaseIndex ? 'done' : index === phaseIndex ? 'current' : 'pending';
            return `<li class="phase-step is-${phaseState}">
              <span class="phase-dot" aria-hidden="true">${phaseState === 'done' ? icon('check', { size: 11 }) : ''}</span>
              <span class="phase-label">${escapeHtml(item.label)}</span>
            </li>`;
          }).join('')}
        </ol>
        <div class="progress progress-waiting" aria-hidden="true"><span></span></div>
        <p class="status-message">${escapeHtml(message)}</p>
        <div class="status-meta">
          <span class="status-time">${icon('clock', { size: 14 })}<span>已等待 <span id="elapsed-time">${escapeHtml(elapsedText)}</span></span></span>
          <span>${escapeHtml(phase.hint)}</span>
        </div>
        ${task.title ? `<p class="status-video-title">${escapeHtml(truncate(task.title, 80))}</p>` : ''}
        ${
          source
            ? `<a class="source-link" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${icon('external', { size: 14 })}<span>${escapeHtml(truncate(source, 64))}</span></a>`
            : ''
        }
      </section>
      ${agentStatusHtml(uiAgent)}
      ${
        presence === false
          ? `<div class="banner banner-warn">${icon('cloud-off', { size: 16 })}<span>电脑当前离线，任务会在 Agent 上线后继续，不需要一直开着这个页面。</span></div>`
          : ''
      }
      <details class="card stage-details">
        <summary>
          <span>查看详细阶段</span>
          <span class="stage-details-current">${escapeHtml(STAGE_LABELS[task.stage] || '处理中')}</span>
        </summary>
        ${timelineListHtml(task)}
      </details>`;
  }

  function failedHtml() {
    const source = sourceUrlOf(task);
    return `
      ${connectionBanner()}
      ${stateErrorBanner()}
      <section class="card fail-card">
        <div class="fail-head">
          <span class="fail-icon" aria-hidden="true">${icon('alert', { size: 22 })}</span>
          <div>
            <h2 class="error-title">生成失败</h2>
            <p class="fail-sub">${escapeHtml(formatRelativeTime(task.completed_at || task.updated_at))}结束</p>
          </div>
        </div>
        <p class="fail-message">${escapeHtml(task.error_message || '电脑端没有返回具体原因，可以重新生成一次。')}</p>
        ${task.error_code ? `<p class="fail-code">错误码：${escapeHtml(task.error_code)}</p>` : ''}
        ${
          source
            ? `<a class="source-link" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${icon('external', { size: 14 })}<span>${escapeHtml(truncate(source, 64))}</span></a>`
            : ''
        }
        <p class="fail-hint">点「重新生成」会创建一个新任务并重新调用模型；如果一直失败，请检查电脑上的 BiliNote 与 Agent 是否正常。</p>
      </section>
      ${agentStatusHtml(agentUi())}`;
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
    refreshButton.classList.toggle('is-spinning', snapshotRefreshing);
    renderPresencePill();
    if (!task) {
      main.innerHTML = removed ? missingHtml() : skeletonHtml();
      renderActionBar();
      return;
    }
    if (task.status === 'succeeded') {
      main.innerHTML = succeededHtml();
      removeDuplicateTitle();
    } else if (task.status === 'failed') main.innerHTML = failedHtml();
    else main.innerHTML = summaryHtml();
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
  /* 笔记：只读本地缓存；缺失时发 note_request（兼容模式走 REST 兜底）     */
  /* ------------------------------------------------------------------ */

  function clearNoteRequestTimer() {
    if (noteRequestTimer) {
      clearTimeout(noteRequestTimer);
      noteRequestTimer = null;
    }
  }

  async function loadLegacyNote() {
    if (disposed || noteState === 'loading') return;
    noteState = 'loading';
    noteError = '';
    render();
    try {
      const data = await api.getNote(taskId, { base, signal, timeout: 30000 });
      if (!data || typeof data.markdown !== 'string') throw new Error('笔记内容为空');
      note = {
        title: data.title || (task && task.title) || '',
        markdown: data.markdown,
        completed_at: data.completed_at || (task && task.completed_at) || null,
      };
      const saved = await noteCache.put(base, taskId, note, { pinned });
      noteSaved = saved === true;
      if (!noteSaved) noteSaveError = '未保存到手机，可重试';
      noteState = 'ready';
      noteError = '';
    } catch (error) {
      if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
      if (error && error.status === 409) noteError = '笔记还在准备中，请稍后重试';
      else if (error && error.status === 410) noteError = '当前 Relay 不再保存笔记正文，需要电脑重新发送';
      else if (isNetworkError(error)) noteError = '连不上 Relay，稍后重试';
      else noteError = error.message || '获取笔记失败';
      noteState = 'unavailable';
    } finally {
      if (!disposed) render();
    }
  }

  async function ensureNote() {
    if (disposed || !task || task.status !== 'succeeded' || note) return;
    if (noteState === 'loading') return;

    if (isLegacyMode()) {
      void loadLegacyNote();
      return;
    }

    if (!isRealtimeOpen()) {
      noteState = 'waiting-connection';
      noteError = '';
      render();
      startRealtime();
      return;
    }

    noteState = 'loading';
    noteError = '';
    render();
    const sent = requestNote(taskId);
    if (!sent) {
      noteState = 'waiting-connection';
      render();
      return;
    }
    clearNoteRequestTimer();
    noteRequestTimer = setTimeout(() => {
      if (disposed || note || noteState !== 'loading') return;
      noteState = 'timeout';
      noteError = '还没有收到笔记，可能是电脑还在发送。';
      render();
    }, REALTIME_NOTE_TIMEOUT_MS);
  }

  /** 补存到手机（成功后补发 ack persisted:true） */
  async function retrySaveNote() {
    if (!note) return;
    const saved = await noteCache.put(base, taskId, note, { pinned });
    if (saved) {
      noteSaved = true;
      noteSaveError = '';
      sendAckNote(taskId, true);
      toast('已保存到手机', { type: 'success' });
    } else {
      noteSaved = false;
      noteSaveError = '仍然无法保存，请检查手机存储空间或浏览器权限';
      toast(noteSaveError, { type: 'error' });
    }
    render();
  }

  async function togglePin() {
    if (!note) return;
    const nextPinned = !pinned;
    const ok = await noteCache.setPinned(base, taskId, nextPinned);
    if (!ok) {
      toast('固定失败，请稍后重试', { type: 'error' });
      return;
    }
    pinned = nextPinned;
    toast(pinned ? '已固定到手机，不会被自动清理' : '已取消固定');
    render();
  }

  /* ------------------------------------------------------------------ */
  /* 任务元数据：首屏 / 重连后 / 手动刷新各一次 REST 快照（不轮询）        */
  /* ------------------------------------------------------------------ */

  async function loadTaskSnapshot() {
    if (disposed || snapshotRefreshing) return;
    snapshotRefreshing = true;
    refreshButton.classList.add('is-spinning');
    try {
      const data = await api.getTask(taskId, { base, signal, timeout: 12000 });
      if (data && data.task) {
        task = { ...(task || {}), ...data.task };
        taskCache.upsert(base, task);
        stateError = '';
      }
      if (data && data.agent) {
        agent = { ...(agent || {}), ...data.agent };
      }
      render();
      if (task && task.status === 'succeeded' && !note) void ensureNote();
    } catch (error) {
      if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
      if (error && error.status === 404) {
        removed = true;
        task = null;
        render();
        renderActionBar();
        return;
      }
      stateError = isNetworkError(error)
        ? '连不上 Relay，显示的是上次状态'
        : `${error.message || '刷新失败'}，显示的是上次状态`;
      render();
    } finally {
      snapshotRefreshing = false;
      if (!disposed) refreshButton.classList.remove('is-spinning');
    }
  }

  /* ------------------------------------------------------------------ */
  /* 实时事件                                                            */
  /* ------------------------------------------------------------------ */

  const unsubRealtimeEvents = [
    onRealtime('state', () => {
      render();
    }),
    onRealtime('presence', () => {
      render();
      if (presenceOnline() === true && task && task.status === 'succeeded' && !note) {
        void ensureNote();
      }
    }),
    onRealtime('readiness', () => {
      render();
    }),
    onRealtime('hello', () => {
      void loadTaskSnapshot();
      if (task && task.status === 'succeeded' && !note) void ensureNote();
    }),
    onRealtime('legacy', () => {
      render();
      if (task && task.status === 'succeeded' && !note) void ensureNote();
    }),
    onRealtime('task_updated', ({ task: update }) => {
      if (!update || update.id !== taskId) return;
      task = { ...(task || {}), ...update };
      taskCache.upsert(base, task);
      render();
      if (task.status === 'succeeded' && !note) void ensureNote();
    }),
    onRealtime('note', (payload) => {
      if (!payload || payload.taskId !== taskId) return;
      clearNoteRequestTimer();
      note = {
        title: payload.title || (task && task.title) || '',
        markdown: payload.markdown,
        completed_at: (task && task.completed_at) || new Date().toISOString(),
      };
      noteState = 'ready';
      noteError = '';
      noteSaved = payload.cached === true;
      if (noteSaved) {
        noteSaveError = '';
      } else {
        noteSaveError = '未保存到手机，可重试；离开页面后需要重新从电脑获取';
      }
      render();
      if (!noteSaved) toast('笔记已收到，但没保存到手机，可点「重试保存」', { type: 'error' });
    }),
    onRealtime('note_unavailable', (payload) => {
      if (!payload || payload.taskId !== taskId) return;
      clearNoteRequestTimer();
      noteState = 'unavailable';
      noteError =
        payload.reason === 'agent_offline'
          ? '电脑离线，稍后重试'
          : '笔记暂时取不到，可以稍后再试';
      render();
    }),
    onRealtime('ticket-rejected', () => {
      toast('实时连接暂时不可用，正在自动重试', { type: 'error' });
    }),
  ];

  /* ------------------------------------------------------------------ */
  /* 操作                                                                */
  /* ------------------------------------------------------------------ */

  async function handleCopy() {
    if (!note) {
      toast('笔记还没准备好，稍后再试');
      return;
    }
    const ok = await copyText(note.markdown);
    toast(ok ? '已复制 Markdown 全文' : '复制失败，请长按选择文本', { type: ok ? 'success' : 'error' });
  }

  function handleDownload() {
    if (!note) {
      toast('笔记还没准备好，稍后再试');
      return;
    }
    const title = note.title || task.title || '链记笔记';
    const filename = `${safeFilename(title, '链记笔记')}.md`;
    const ok = downloadTextFile(filename, note.markdown);
    toast(ok ? `已下载 ${filename}` : '下载失败，请改用复制', { type: ok ? 'success' : 'error' });
  }

  async function handleShare() {
    if (!note) {
      toast('笔记还没准备好，稍后再试');
      return;
    }
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

  async function handleExportSheet() {
    if (!note) {
      toast('笔记还没准备好，稍后再试');
      return;
    }
    const choice = await openSheet({
      title: '导出笔记',
      subtitle: note.title || task.title || '',
      actions: [
        { key: 'download', label: '下载 .md 文件', iconName: 'download' },
        { key: 'share', label: '系统分享', iconName: 'share', hint: '分享给其他 App 或保存到云盘' },
      ],
    });
    if (choice === 'download') handleDownload();
    else if (choice === 'share') await handleShare();
  }

  async function handleMoreSheet() {
    if (!task) return;
    const actions = [];
    if (task.status === 'succeeded') {
      if (note) {
        actions.push({
          key: 'pin',
          label: pinned ? '取消固定' : '固定到手机',
          iconName: 'pin',
          hint: pinned ? '固定笔记不参与自动清理' : '固定后不会被自动清理',
        });
      }
      if (note && noteSaved === false) {
        actions.push({
          key: 'retry-save',
          label: '重试保存到手机',
          iconName: 'download',
          hint: '上次没有保存成功',
        });
      }
      actions.push({
        key: 'retry',
        label: '重新生成',
        iconName: 'retry',
        hint: '会重新调用模型，创建一个新任务',
      });
      actions.push({ key: 'remove', label: '从列表移除', iconName: 'trash', danger: true });
    } else if (task.status === 'failed') {
      actions.push({ key: 'remove', label: '从列表移除', iconName: 'trash', danger: true });
      actions.push({ key: 'back', label: '返回收件箱', iconName: 'back' });
    }
    if (!actions.length) return;
    const choice = await openSheet({ title: '更多操作', subtitle: task.title || '', actions });
    switch (choice) {
      case 'pin':
        await togglePin();
        break;
      case 'retry-save':
        await retrySaveNote();
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
      default:
        break;
    }
  }

  async function handleRetry() {
    if (actionBusy) return;
    const confirmed = await confirmDialog({
      title: '重新生成笔记？',
      message: '会创建一个新任务并重新调用模型生成；原来的任务会保留。',
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
      toast(error.message || '重新生成失败，请稍后再试', { type: 'error' });
    } finally {
      actionBusy = false;
    }
  }

  async function handleRemove() {
    if (actionBusy) return;
    const confirmed = await confirmDialog({
      title: '从列表移除任务？',
      message: pinned
        ? '这是一个固定笔记，移除后会同时删除本机缓存（固定状态也会清除）。'
        : '移除后任务不再出现在收件箱里。笔记正文只保存在你本机，移除后本地缓存也会被清理。',
      confirmText: '移除',
      danger: true,
    });
    if (!confirmed) return;
    actionBusy = true;
    try {
      await api.removeTask(taskId, { base, signal });
      taskCache.remove(base, taskId);
      await noteCache.remove(base, taskId);
      toast('已从列表移除');
      navigate('/home', { replace: true });
    } catch (error) {
      if (error && error.code === 'aborted') return;
      if (error && (error.status === 404 || error.code === 'unauthorized')) {
        taskCache.remove(base, taskId);
        await noteCache.remove(base, taskId);
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
      case 'export':
        await handleExportSheet();
        break;
      case 'more':
        await handleMoreSheet();
        break;
      case 'download':
        handleDownload();
        break;
      case 'share':
        await handleShare();
        break;
      case 'retry-save':
        await retrySaveNote();
        break;
      case 'retry':
      case 'regenerate':
        await handleRetry();
        break;
      case 'remove':
        await handleRemove();
        break;
      case 'back':
        navigate('/home');
        break;
      case 'reload-note':
        clearNoteRequestTimer();
        noteState = 'idle';
        noteError = '';
        render();
        if (isLegacyMode()) void loadLegacyNote();
        else if (isRealtimeOpen()) await ensureNote();
        else {
          probeRealtime();
          void ensureNote();
        }
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
      probeRealtime();
      void loadTaskSnapshot().finally(() => {
        if (!disposed) refreshButton.disabled = false;
      });
    },
    { signal },
  );

  const elapsedTimer = setInterval(() => {
    const node = document.getElementById('elapsed-time');
    if (!node || !task || isTerminal(task)) return;
    const elapsed = elapsedBetween(task.created_at);
    node.textContent = elapsed < 2500 ? '刚刚开始' : formatElapsed(elapsed);
  }, 1000);

  signal.addEventListener(
    'abort',
    () => {
      disposed = true;
      clearNoteRequestTimer();
      clearInterval(elapsedTimer);
    },
    { once: true },
  );

  /* ------------------------------------------------------------------ */
  /* 启动                                                                */
  /* ------------------------------------------------------------------ */

  startRealtime();
  render();

  // 1) 先读本地缓存（阅读器只读缓存，不请求 REST 正文）。
  try {
    const cachedNote = await noteCache.get(base, taskId);
    if (disposed) {
      return () => {};
    }
    if (cachedNote && !note) {
      note = cachedNote;
      noteState = 'ready';
      noteError = '';
      noteSaved = true;
      pinned = cachedNote.pinned === true;
      render();
    }
  } catch (error) {
    // 缓存读失败就继续走 note_request
  }

  // 2) 首屏一次 REST 元数据快照（不是轮询），随后完全由 WS 推送。
  void loadTaskSnapshot();

  // 3) 若任务已知且已完成但没缓存正文，立刻按需请求。
  if (task && task.status === 'succeeded' && !note) {
    void ensureNote();
  }

  return () => {
    disposed = true;
    clearNoteRequestTimer();
    clearInterval(elapsedTimer);
    unsubRealtimeEvents.forEach((unsub) => {
      try {
        unsub();
      } catch (error) {
        // 忽略
      }
    });
  };
}

/**
 * 链记 LinkNote — UI 小工具（图标、Toast、确认框、通用 HTML 片段）
 * 所有用户内容都必须经过 escapeHtml 再拼进模板。
 */
import {
  PLATFORM_META,
  STAGE_LABELS,
  STATUS_META,
  SUPPORTED_PLATFORMS_TEXT,
} from './config.js';
import { escapeHtml } from './util.js';

/* ------------------------------------------------------------------ */
/* 图标（内联 SVG，无外部资源）                                         */
/* ------------------------------------------------------------------ */

const ICONS = {
  inbox:
    '<path d="M4 11.2 5.9 5.6A1.8 1.8 0 0 1 7.6 4.4h8.8a1.8 1.8 0 0 1 1.7 1.2l1.9 5.6"/><path d="M4 11.2v6a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6"/><path d="M4 11.2h4.4l1.3 2.1h4.6l1.3-2.1H20"/>',
  settings:
    '<circle cx="12" cy="12" r="3.1"/><path d="M19.4 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a1.9 1.9 0 1 1-2.7 2.7l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5v.2a1.9 1.9 0 1 1-3.8 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a1.9 1.9 0 1 1-2.7-2.7l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a1.9 1.9 0 1 1 0-3.8h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a1.9 1.9 0 1 1 2.7-2.7l.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.5V3a1.9 1.9 0 1 1 3.8 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a1.9 1.9 0 1 1 2.7 2.7l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1h.2a1.9 1.9 0 1 1 0 3.8h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  refresh:
    '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4.6V11h-6.2"/>',
  link:
    '<path d="M10.6 13.4a3.6 3.6 0 0 0 5.1 0l2.4-2.4a3.6 3.6 0 0 0-5.1-5.1l-1.2 1.2"/><path d="M13.4 10.6a3.6 3.6 0 0 0-5.1 0L5.9 13a3.6 3.6 0 0 0 5.1 5.1l1.2-1.2"/>',
  copy:
    '<rect x="8.5" y="8.5" width="11" height="11" rx="2.4"/><path d="M15.5 8.5V6.9a2.4 2.4 0 0 0-2.4-2.4H6.9a2.4 2.4 0 0 0-2.4 2.4v6.2a2.4 2.4 0 0 0 2.4 2.4h1.6"/>',
  download:
    '<path d="M12 4.5v10.2"/><path d="m7.8 10.9 4.2 4.2 4.2-4.2"/><path d="M5 18.8h14"/>',
  share:
    '<circle cx="17.5" cy="6" r="2.4"/><circle cx="6.5" cy="12" r="2.4"/><circle cx="17.5" cy="18" r="2.4"/><path d="m8.7 10.8 6.6-3.6"/><path d="m8.7 13.2 6.6 3.6"/>',
  retry:
    '<path d="M4.5 12a7.5 7.5 0 1 1 2.6 5.7"/><path d="M4.5 17.5v-5.4h5.4"/>',
  trash:
    '<path d="M5 7h14"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6.5 7 7.4 19a1.6 1.6 0 0 0 1.6 1.4h6a1.6 1.6 0 0 0 1.6-1.4L17.5 7"/><path d="M9.4 7V5.2A1.2 1.2 0 0 1 10.6 4h2.8a1.2 1.2 0 0 1 1.2 1.2V7"/>',
  back: '<path d="m14.5 5.5-6.5 6.5 6.5 6.5"/>',
  check: '<path d="m5.5 12.5 4.2 4.2 9-9"/>',
  alert:
    '<path d="M12 4.6 3.8 19h16.4L12 4.6Z"/><path d="M12 9.6v4.2"/><path d="M12 16.8h.01"/>',
  'wifi-off':
    '<path d="M3 4.5 21 20"/><path d="M8.6 15.6a5 5 0 0 1 5.1-1.1"/><path d="M5.2 12.3a9.6 9.6 0 0 1 3-1.8"/><path d="M15.8 10.5a9.6 9.6 0 0 1 3 1.8"/><path d="M2.8 8.6a15 15 0 0 1 4.5-2.6"/><path d="M16.7 6a15 15 0 0 1 4.5 2.6"/><path d="M12 18.6h.01"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  send: '<path d="M5 12 20 5l-4.2 15L11 14.5 5 12Z"/><path d="m11 14.5 4-5"/>',
  qr:
    '<rect x="4" y="4" width="6" height="6" rx="1.2"/><rect x="14" y="4" width="6" height="6" rx="1.2"/><rect x="4" y="14" width="6" height="6" rx="1.2"/><path d="M14 14h2.5v2.5"/><path d="M20 14v6h-6"/><path d="M17 17h3"/>',
  camera:
    '<path d="M4.5 8.5h2.2l1.4-2.2h7.8l1.4 2.2h2.2a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17V10a1.5 1.5 0 0 1 1.5-1.5Z"/><circle cx="12" cy="13.2" r="3.2"/>',
  close: '<path d="m6 6 12 12"/><path d="m18 6-12 12"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 1.8"/>',
  sparkles:
    '<path d="M12 3.5 13.8 9l5.5 1.8-5.5 1.8L12 18.2l-1.8-5.6L4.7 10.8 10.2 9 12 3.5Z"/><path d="M18.5 16.5 19.2 18.8l2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z"/>',
  external:
    '<path d="M13.5 5H19v5.5"/><path d="M19 5 10.5 13.5"/><path d="M17 13.8V17a2 2 0 0 1-2 2H6.8a2 2 0 0 1-2-2V7.8a2 2 0 0 1 2-2h3.2"/>',
  book:
    '<path d="M4.5 5.8A2.3 2.3 0 0 1 6.8 3.5H19v14.8H6.8a2.3 2.3 0 0 0-2.3 2.2V5.8Z"/><path d="M4.5 18.5A2.3 2.3 0 0 1 6.8 20.5H19"/>',
  cloud:
    '<path d="M7.2 18.5h9.6a3.7 3.7 0 0 0 .5-7.4 5.4 5.4 0 0 0-10.4-1.2 3.8 3.8 0 0 0 .3 8.6Z"/>',
  'cloud-off':
    '<path d="M3 3.5 21 21"/><path d="M8.2 5.1A5.4 5.4 0 0 1 17.3 11a3.8 3.8 0 0 1 1.8 6.5"/><path d="M7.2 18.5h9.6"/><path d="M6.5 8.1A3.8 3.8 0 0 0 7.2 18.5"/>',
  install:
    '<path d="M12 3.8v10.8"/><path d="m7.6 10.3 4.4 4.4 4.4-4.4"/><path d="M5 16.2v2.6A1.8 1.8 0 0 0 6.8 20.6h10.4a1.8 1.8 0 0 0 1.8-1.8v-2.6"/>',
  'arrow-right': '<path d="M5 12h14"/><path d="m13.5 6.5 5.5 5.5-5.5 5.5"/>',
  'chevron-right': '<path d="m9.5 5.5 6.5 6.5-6.5 6.5"/>',
  file:
    '<path d="M13.5 3.5H7.2a1.8 1.8 0 0 0-1.8 1.8v13.4a1.8 1.8 0 0 0 1.8 1.8h9.6a1.8 1.8 0 0 0 1.8-1.8V8.5L13.5 3.5Z"/><path d="M13.5 3.5v5h5.1"/>',
};

/**
 * 内联图标
 * @param {string} name
 * @param {{ size?: number, className?: string, strokeWidth?: number }} options
 */
export function icon(name, { size = 20, className = 'icon', strokeWidth = 1.8 } = {}) {
  const paths = ICONS[name];
  if (!paths) return '';
  return `<svg class="${className}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

export function spinner(className = 'spinner') {
  return `<span class="${className}" aria-hidden="true"></span>`;
}

/* ------------------------------------------------------------------ */
/* Toast                                                              */
/* ------------------------------------------------------------------ */

export function toast(message, { type = 'info', duration } = {}) {
  const region = typeof document !== 'undefined' ? document.getElementById('toast-region') : null;
  if (!region || !message) return;
  const node = document.createElement('div');
  node.className = `toast toast-${type}`;
  node.setAttribute('role', type === 'error' ? 'alert' : 'status');
  node.textContent = String(message);
  region.appendChild(node);
  while (region.children.length > 3) region.firstElementChild.remove();
  const ms = duration || (type === 'error' ? 4500 : 2800);
  setTimeout(() => {
    node.classList.add('toast-out');
    setTimeout(() => node.remove(), 240);
  }, ms);
}

/* ------------------------------------------------------------------ */
/* 确认对话框                                                          */
/* ------------------------------------------------------------------ */

/**
 * @returns {Promise<boolean>}
 */
export function confirmDialog({
  title = '确认',
  message = '',
  confirmText = '确定',
  cancelText = '取消',
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    const root =
      (typeof document !== 'undefined' && document.getElementById('dialog-root')) || document.body;
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    overlay.innerHTML = `
      <div class="dialog-card" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <h2 class="dialog-title" id="dialog-title"></h2>
        <p class="dialog-message"></p>
        <div class="dialog-actions">
          <button type="button" class="btn btn-ghost" data-action="cancel"></button>
          <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-action="confirm"></button>
        </div>
      </div>`;
    overlay.querySelector('.dialog-title').textContent = title;
    overlay.querySelector('.dialog-message').textContent = message;
    const cancelButton = overlay.querySelector('[data-action="cancel"]');
    const confirmButton = overlay.querySelector('[data-action="confirm"]');
    cancelButton.textContent = cancelText;
    confirmButton.textContent = confirmText;

    let settled = false;
    const close = (value) => {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close(false);
      }
    };

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close(false);
    });
    cancelButton.addEventListener('click', () => close(false));
    confirmButton.addEventListener('click', () => close(true));
    document.addEventListener('keydown', onKey);

    root.appendChild(overlay);
    requestAnimationFrame(() => confirmButton.focus());
  });
}

/* ------------------------------------------------------------------ */
/* 按钮 loading 状态                                                   */
/* ------------------------------------------------------------------ */

export function setButtonBusy(button, busy, { label, idleLabel } = {}) {
  if (!button) return;
  if (busy) {
    if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
    if (idleLabel) button.dataset.busyLabel = idleLabel;
    button.disabled = true;
    button.classList.add('is-busy');
    button.setAttribute('aria-busy', 'true');
    button.innerHTML = `${spinner()}<span>${escapeHtml(label || button.dataset.busyLabel || '处理中…')}</span>`;
  } else {
    button.disabled = false;
    button.classList.remove('is-busy');
    button.removeAttribute('aria-busy');
    if (button.dataset.idleHtml) {
      button.innerHTML = button.dataset.idleHtml;
      delete button.dataset.idleHtml;
    }
  }
}

/* ------------------------------------------------------------------ */
/* 通用片段                                                           */
/* ------------------------------------------------------------------ */

export function statusChip(status) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  return `<span class="chip chip-${meta.tone}"><span class="chip-dot" aria-hidden="true"></span>${escapeHtml(meta.label)}</span>`;
}

export function platformChip(platform) {
  const meta = PLATFORM_META[platform];
  if (!meta) return '';
  return `<span class="platform-chip"><span class="platform-dot" style="--platform-color:${meta.color}" aria-hidden="true"></span>${escapeHtml(meta.label)}</span>`;
}

export function stageLabel(stage) {
  return STAGE_LABELS[stage] || '';
}

export function supportedPlatformsText() {
  return SUPPORTED_PLATFORMS_TEXT;
}

export function emptyState({ title = '还没有内容', text = '', iconName = 'link', hint = '' } = {}) {
  return `
    <div class="empty">
      <div class="empty-mark" aria-hidden="true">${icon(iconName, { size: 30 })}</div>
      <h3 class="empty-title">${escapeHtml(title)}</h3>
      ${text ? `<p class="empty-text">${escapeHtml(text)}</p>` : ''}
      ${hint ? `<p class="empty-hint">${escapeHtml(hint)}</p>` : ''}
    </div>`;
}

export function listSkeleton(count = 3) {
  return Array.from({ length: count })
    .map(
      () => `
      <div class="task-item task-skeleton" aria-hidden="true">
        <div class="skeleton-line skeleton-line-sm"></div>
        <div class="skeleton-line skeleton-line-lg"></div>
        <div class="skeleton-line skeleton-line-md"></div>
      </div>`,
    )
    .join('');
}

export function agentPill(agent) {
  const online = Boolean(agent && agent.online);
  return `<span class="status-pill" data-state="${online ? 'online' : 'offline'}">
    <span class="status-dot" aria-hidden="true"></span>${online ? '电脑在线' : '电脑离线'}
  </span>`;
}

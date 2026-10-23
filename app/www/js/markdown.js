/**
 * 链记 LinkNote — Markdown 渲染
 *
 * marked 负责解析，DOMPurify 负责清洗（禁止脚本/事件属性）。
 * 清洗后链接统一 target=_blank + rel=noopener noreferrer，图片懒加载。
 */
import { marked } from '../vendor/marked.esm.js';
import DOMPurify from '../vendor/purify.es.mjs';

marked.setOptions({
  gfm: true,
  breaks: true,
  headerIds: false,
  mangle: false,
});

let hooked = false;

function installHook() {
  if (hooked) return;
  hooked = true;
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'A') {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer nofollow');
    }
    if (node.tagName === 'IMG') {
      node.setAttribute('loading', 'lazy');
      node.setAttribute('decoding', 'async');
      node.setAttribute('referrerpolicy', 'no-referrer');
    }
  });
}

const PURIFY_CONFIG = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: [
    'style',
    'form',
    'input',
    'button',
    'textarea',
    'select',
    'option',
    'iframe',
    'object',
    'embed',
    'link',
    'meta',
    'base',
    'frame',
    'frameset',
  ],
  FORBID_ATTR: ['style', 'srcset', 'ping'],
  ADD_ATTR: ['target', 'loading', 'decoding', 'referrerpolicy'],
  ALLOW_DATA_ATTR: false,
};

/** Markdown → 安全的 HTML 字符串 */
export function renderMarkdown(markdown) {
  const source = typeof markdown === 'string' ? markdown : String(markdown ?? '');
  if (!source.trim()) {
    return '<p class="reader-empty">这篇笔记没有正文内容。</p>';
  }
  installHook();
  let rawHtml;
  try {
    rawHtml = marked.parse(source);
  } catch (error) {
    return `<p class="reader-empty">笔记解析失败：${escapeForHtml(error && error.message ? error.message : '未知错误')}</p>`;
  }
  try {
    return DOMPurify.sanitize(rawHtml, PURIFY_CONFIG);
  } catch (error) {
    return '<p class="reader-empty">笔记内容无法安全渲染。</p>';
  }
}

function escapeForHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    switch (char) {
      case '&':
        return '&amp;';
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '"':
        return '&quot;';
      default:
        return '&#39;';
    }
  });
}

/**
 * 链记 LinkNote — 原生分享桥
 *
 * APK（Capacitor）约定（与 Android 壳对齐）：
 * - 冷/热启动都会注入 window.__LINKNOTE_SHARED_TEXT__；
 *   热启动还会 dispatch CustomEvent('linknote:share', { detail: { text } })。
 * - window.LinkNoteNative.getSharedText() 返回 Promise<string|null>，取完即清。
 *
 * 兜底轮询 + 回前台补读，保证 WebView 注入晚于脚本时也不会漏掉分享内容。
 */
import { normalizeShareText } from './util.js';

/**
 * @param {{ onShare: (text: string, source: string) => void }} options
 * @returns {() => void} 卸载函数
 */
export function installNativeBridge({ onShare } = {}) {
  if (typeof onShare !== 'function') return () => {};

  const delivered = new Set();
  const deliver = (raw, source) => {
    const text = normalizeShareText(raw);
    if (!text) return false;
    const key = `${text.length}:${text.slice(0, 160)}:${text.slice(-32)}`;
    if (delivered.has(key)) return false;
    delivered.add(key);
    if (delivered.size > 20) {
      const oldest = delivered.values().next().value;
      delivered.delete(oldest);
    }
    try {
      onShare(text, source);
    } catch (error) {
      console.error('[bridge] 处理分享内容失败', error);
    }
    return true;
  };

  const readInjected = () => {
    try {
      const value = window.__LINKNOTE_SHARED_TEXT__;
      if (typeof value === 'string' && value.trim()) {
        window.__LINKNOTE_SHARED_TEXT__ = '';
        return value;
      }
      if (value && typeof value === 'object' && typeof value.text === 'string' && value.text.trim()) {
        window.__LINKNOTE_SHARED_TEXT__ = '';
        return value.text;
      }
    } catch (error) {
      // 忽略
    }
    return '';
  };

  const readFromNative = () => {
    const native = window.LinkNoteNative;
    if (!native || typeof native.getSharedText !== 'function') return;
    try {
      const result = native.getSharedText();
      if (result && typeof result.then === 'function') {
        result.then((value) => deliver(value, 'native')).catch(() => {});
      } else if (typeof result === 'string') {
        deliver(result, 'native');
      }
    } catch (error) {
      // 原生调用失败不影响页面
    }
  };

  // 1) 启动即读（原生可能在页面脚本之前就注入）
  deliver(readInjected(), 'injected');

  // 2) 短轮询兜底（最多 20 秒）
  let attempts = 0;
  const pollTimer = setInterval(() => {
    attempts += 1;
    const text = readInjected();
    if (text) {
      deliver(text, 'injected-late');
      clearInterval(pollTimer);
      return;
    }
    if (attempts >= 40) clearInterval(pollTimer);
  }, 500);

  // 3) 原生桥（Promise，取完即清）
  readFromNative();

  // 4) 热启动事件
  const onShareEvent = (event) => {
    const detail = event ? event.detail : null;
    let text = '';
    if (typeof detail === 'string') text = detail;
    else if (detail && typeof detail === 'object') {
      text = detail.text || detail.url || detail.sharedText || '';
    }
    const handled = deliver(text, 'event');
    // 事件里没有正文时，从原生桥补一次。
    if (!handled) readFromNative();
  };
  window.addEventListener('linknote:share', onShareEvent);

  // 5) 回到前台补读（部分机型冷启动的注入晚于首屏）
  const onVisibility = () => {
    if (document.visibilityState !== 'visible') return;
    deliver(readInjected(), 'injected-resume');
    readFromNative();
  };
  document.addEventListener('visibilitychange', onVisibility);

  return () => {
    clearInterval(pollTimer);
    window.removeEventListener('linknote:share', onShareEvent);
    document.removeEventListener('visibilitychange', onVisibility);
  };
}

/** 供调试/测试：手动投递一条分享内容 */
export function emitNativeShare(text) {
  window.dispatchEvent(new CustomEvent('linknote:share', { detail: { text } }));
}

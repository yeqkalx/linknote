/**
 * 链记 LinkNote — PWA 能力封装
 * - Service Worker 注册（壳缓存 / 离线打开）
 * - 安装提示（beforeinstallprompt）
 * - 是否已安装 / 是否原生壳
 */
import { isIos, isNativePlatform, isStandalone } from './util.js';

let deferredPrompt = null;
let registration = null;
let installedThisSession = false;
let initialized = false;

export function initPwa() {
  if (initialized) return;
  initialized = true;

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    window.dispatchEvent(new CustomEvent('linknote:installable'));
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    installedThisSession = true;
    window.dispatchEvent(new CustomEvent('linknote:installed'));
  });

  void registerServiceWorker();
}

export function canPromptInstall() {
  return Boolean(deferredPrompt);
}

export function isInstalled() {
  return installedThisSession || isStandalone();
}

export function isNativeApp() {
  return isNativePlatform();
}

/** 安装引导文案（不同平台差异很大） */
export function installHintText() {
  if (isNativeApp()) return '当前是 Android App 版本，已支持系统分享。';
  if (isInstalled()) return '已安装为桌面应用，可以从主屏幕直接打开。';
  if (isIos()) return '在 Safari 中点击「分享」按钮，然后选择「添加到主屏幕」。';
  return '在浏览器菜单中选择「安装应用 / 添加到主屏幕」，即可获得全屏体验与分享入口。';
}

/**
 * 触发原生安装提示。
 * @returns {Promise<'accepted'|'dismissed'|'unavailable'|'error'>}
 */
export async function promptInstall() {
  if (!deferredPrompt) return 'unavailable';
  const prompt = deferredPrompt;
  deferredPrompt = null;
  try {
    prompt.prompt();
    const choice = await prompt.userChoice;
    return choice && choice.outcome ? choice.outcome : 'dismissed';
  } catch (error) {
    return 'error';
  }
}

export async function registerServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null;
  if (!/^https?:$/.test(location.protocol)) return null; // file:// 不支持 SW
  try {
    registration = await navigator.serviceWorker.register('./sw.js', { scope: './' });
    window.dispatchEvent(new CustomEvent('linknote:sw-registered'));
    return registration;
  } catch (error) {
    console.warn('[pwa] Service Worker 注册失败（离线能力不可用）', error);
    return null;
  }
}

export function getRegistration() {
  return registration;
}

export function hasServiceWorker() {
  return Boolean(registration || (typeof navigator !== 'undefined' && navigator.serviceWorker && navigator.serviceWorker.controller));
}

/** 向 SW 询问版本（用于设置页展示） */
export function getServiceWorkerVersion(timeout = 1500) {
  return new Promise((resolve) => {
    const active = registration && registration.active;
    if (!active) {
      resolve(null);
      return;
    }
    const channel = new MessageChannel();
    const timer = setTimeout(() => resolve(null), timeout);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      resolve(event.data || null);
    };
    try {
      active.postMessage({ type: 'GET_VERSION' }, [channel.port2]);
    } catch (error) {
      clearTimeout(timer);
      resolve(null);
    }
  });
}

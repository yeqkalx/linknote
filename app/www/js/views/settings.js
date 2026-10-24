/**
 * 链记 LinkNote — 设置
 * Relay 地址、设备名、解除配对、电脑在线信息、PWA 安装、离线缓存、版本。
 */
import { api, isNetworkError } from '../api.js';
import { APP_NAME, APP_NAME_EN, APP_VERSION, NATIVE_DEFAULT_RELAY } from '../config.js';
import {
  canPromptInstall,
  getRegistration,
  installHintText,
  isInstalled,
  isNativeApp,
  promptInstall,
} from '../pwa.js';
import {
  describeRealtime,
  getRealtimeSnapshot,
  onRealtime,
  probeRealtime,
  restartRealtime,
  startRealtime,
  stopRealtime,
} from '../realtime.js';
import { navigate } from '../router.js';
import {
  auth,
  cacheSummary,
  clearCaches,
  settings,
} from '../store.js';
import {
  confirmDialog,
  icon,
  setButtonBusy,
  toast,
} from '../ui.js';
import {
  devicePlatform,
  escapeHtml,
  formatBytes,
  formatDateTime,
  formatRelativeTime,
  normalizeApiBase,
} from '../util.js';

function platformLabel(platform) {
  if (platform === 'android') return 'Android';
  if (platform === 'ios') return 'iOS';
  if (platform === 'windows') return 'Windows';
  if (platform === 'macos') return 'macOS';
  if (platform === 'linux') return 'Linux';
  if (platform === 'web') return '网页';
  return platform || '未知';
}

export async function settingsView(container, ctx) {
  const signal = ctx.signal;
  const base = settings.getApiBase();
  const authEntry = base ? auth.get(base) : null;

  if (!authEntry) {
    navigate('/pair', { replace: true });
    return () => {};
  }

  let device = authEntry.device || null;
  let agent = authEntry.agent || null;
  let meError = '';
  let loadingMe = false;
  let testingRelay = false;

  container.innerHTML = `
    <section class="screen settings-screen">
      <header class="appbar">
        <button type="button" class="icon-btn" id="back-btn" aria-label="返回收件箱">${icon('back', { size: 20 })}</button>
        <div class="appbar-title">设置</div>
        <span class="appbar-spacer" aria-hidden="true"></span>
      </header>

      <main class="settings-main">
        <section class="card settings-card" aria-labelledby="pairing-title">
          <div class="settings-head">
            <h2 class="card-title" id="pairing-title">配对与设备</h2>
            <span class="settings-head-actions">
              <button type="button" class="icon-btn icon-btn-sm" id="refresh-me" aria-label="刷新电脑状态">${icon('refresh', { size: 18 })}</button>
            </span>
          </div>
          <div id="pairing-body" aria-live="polite"></div>
        </section>

        <section class="card settings-card" aria-labelledby="relay-title">
          <h2 class="card-title" id="relay-title">服务器（Relay）</h2>
          <p class="card-sub">接口固定为 <code>/api/v1</code>。同源部署时留空即可。</p>
          <label class="field" for="relay-input">
            <span class="field-label">Relay 地址</span>
            <input
              id="relay-input"
              class="input"
              type="url"
              inputmode="url"
              autocomplete="url"
              spellcheck="false"
              placeholder="${escapeHtml(settings.getDefaultApiBase() || 'http://127.0.0.1:8787')}"
              value="${escapeHtml(settings.getApiBaseOverride())}"
            />
          </label>
          <p class="field-help">原生 App 默认使用 <code>${escapeHtml(NATIVE_DEFAULT_RELAY)}</code>；局域网调试可填 <code>http://电脑IP:8787</code>。</p>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost" id="relay-test">
              ${icon('refresh', { size: 16 })}<span>测试连接</span>
            </button>
            <button type="button" class="btn btn-primary" id="relay-save">保存地址</button>
          </div>
          <p id="relay-status" class="relay-status" role="status" hidden></p>
          <button type="button" class="link-btn" id="relay-reset">恢复默认地址</button>
        </section>

        <section class="card settings-card" aria-labelledby="app-title">
          <h2 class="card-title" id="app-title">应用</h2>
          <div class="kv"><span class="kv-key">版本</span><span class="kv-value">${escapeHtml(APP_VERSION)}</span></div>
          <div class="kv"><span class="kv-key">当前服务器</span><span class="kv-value mono" id="effective-base">${escapeHtml(base)}</span></div>
          <div class="kv"><span class="kv-key">离线访问</span><span class="kv-value" id="sw-state">检查中…</span></div>
          <div class="kv"><span class="kv-key">实时连接</span><span class="kv-value" id="rt-state">检查中…</span></div>
          <div class="kv"><span class="kv-key">网络</span><span class="kv-value" id="network-state">${navigator.onLine ? '在线' : '离线'}</span></div>
          <div class="install-area" id="install-area"></div>
          <div class="settings-divider"></div>
          <div class="kv"><span class="kv-key">离线缓存</span><span class="kv-value" id="cache-summary">—</span></div>
          <div class="btn-row">
            <button type="button" class="btn btn-ghost" id="clear-cache">清除离线缓存</button>
          </div>
        </section>

        <section class="card settings-card danger-card" aria-labelledby="unpair-title">
          <h2 class="card-title" id="unpair-title">解除配对</h2>
          <p class="card-sub">解除后这台手机将无法提交任务，需要重新输入配对码。任务与笔记仍保留在 Relay。</p>
          <button type="button" class="btn btn-danger btn-block" id="unpair-btn">解除配对</button>
        </section>

        <footer class="settings-foot">
          <p>${escapeHtml(APP_NAME)} ${escapeHtml(APP_NAME_EN)} v${escapeHtml(APP_VERSION)}</p>
          <p>视频、音频和 AI Key 都在你的电脑上处理，云端只中转链接与 Markdown。</p>
          <p>Markdown 渲染：marked + DOMPurify · 上游基于 BiliNote（MIT）</p>
        </footer>
      </main>
    </section>`;

  const pairingBody = container.querySelector('#pairing-body');
  const refreshMeButton = container.querySelector('#refresh-me');
  const relayInput = container.querySelector('#relay-input');
  const relayTestButton = container.querySelector('#relay-test');
  const relaySaveButton = container.querySelector('#relay-save');
  const relayResetButton = container.querySelector('#relay-reset');
  const relayStatus = container.querySelector('#relay-status');
  const effectiveBase = container.querySelector('#effective-base');
  const swState = container.querySelector('#sw-state');
  const rtState = container.querySelector('#rt-state');
  const networkState = container.querySelector('#network-state');
  const installArea = container.querySelector('#install-area');
  const cacheSummaryEl = container.querySelector('#cache-summary');
  const clearCacheButton = container.querySelector('#clear-cache');
  const unpairButton = container.querySelector('#unpair-btn');

  /* ------------------------------------------------------------------ */
  /* 配对与设备                                                          */
  /* ------------------------------------------------------------------ */

  function renderPairing() {
    const presence = getRealtimeSnapshot().presence.online;
    const online = typeof presence === 'boolean' ? presence : Boolean(agent && agent.online);
    const deviceName = (device && device.name) || settings.getDeviceName();
    const platform = (device && device.platform) || devicePlatform();
    const pairedAt = device && device.paired_at ? formatDateTime(device.paired_at) : '';
    const deviceId = device && device.id ? `${device.id.slice(0, 8)}…` : '—';

    pairingBody.innerHTML = `
      <div class="agent-row">
        <span class="agent-icon ${online ? 'is-online' : ''}" aria-hidden="true">${icon(online ? 'cloud' : 'cloud-off', { size: 20 })}</span>
        <div class="agent-info">
          <p class="agent-name">${escapeHtml((agent && agent.name) || '我的电脑')}</p>
          <p class="agent-sub">${
            online
              ? '电脑在线，可以接收任务'
              : agent && agent.last_seen_at
                ? `最近在线：${escapeHtml(formatRelativeTime(agent.last_seen_at))}`
                : '电脑离线，任务会排队'
          }</p>
        </div>
        <span class="status-pill" data-state="${online ? 'online' : 'offline'}">
          <span class="status-dot" aria-hidden="true"></span>${online ? '在线' : '离线'}
        </span>
      </div>
      ${
        meError
          ? `<div class="banner banner-warn" role="status">${icon('wifi-off', { size: 16 })}<span>${escapeHtml(meError)}</span></div>`
          : ''
      }
      <form class="device-form" id="device-form">
        <label class="field" for="device-name-input">
          <span class="field-label">设备名称</span>
          <span class="input-inline">
            <input id="device-name-input" class="input" type="text" maxlength="40" value="${escapeHtml(deviceName)}" enterkeyhint="done" />
            <button type="submit" class="btn btn-ghost" id="device-save">保存</button>
          </span>
        </label>
      </form>
      <div class="settings-divider"></div>
      <div class="kv"><span class="kv-key">设备系统</span><span class="kv-value">${escapeHtml(platformLabel(platform))}</span></div>
      ${pairedAt ? `<div class="kv"><span class="kv-key">配对时间</span><span class="kv-value">${escapeHtml(pairedAt)}</span></div>` : ''}
      <div class="kv"><span class="kv-key">设备 ID</span><span class="kv-value mono">${escapeHtml(deviceId)}</span></div>`;
  }

  /** 实时事件到来时，避免打断用户正在编辑的设备名 */
  function refreshPairingLive() {
    const active = document.activeElement;
    if (active && pairingBody.contains(active)) return;
    renderPairing();
  }

  async function loadMe() {
    if (loadingMe) return;
    loadingMe = true;
    refreshMeButton.classList.add('is-spinning');
    try {
      const data = await api.me({ base, signal, timeout: 10000 });
      if (data) {
        if (data.device) {
          device = data.device;
          auth.update(base, { device });
        }
        if (data.agent) {
          agent = data.agent;
          auth.update(base, { agent });
        }
      }
      meError = '';
    } catch (error) {
      if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
      meError = isNetworkError(error) ? '连不上 Relay，显示的是上次状态' : error.message || '读取配对信息失败';
    } finally {
      loadingMe = false;
      if (!signal.aborted) {
        refreshMeButton.classList.remove('is-spinning');
        renderPairing();
      }
    }
  }

  pairingBody.addEventListener(
    'submit',
    async (event) => {
      event.preventDefault();
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.id !== 'device-form') return;
      const input = pairingBody.querySelector('#device-name-input');
      const saveButton = pairingBody.querySelector('#device-save');
      const value = (input && input.value ? input.value : '').trim();
      if (!value) {
        toast('设备名称不能为空', { type: 'error' });
        input && input.focus();
        return;
      }
      if (value.length > 40) {
        toast('设备名称最多 40 个字符', { type: 'error' });
        return;
      }
      setButtonBusy(saveButton, true, { label: '保存中…' });
      try {
        const data = await api.renameDevice(value, { base, signal, timeout: 10000 });
        const nextDevice = data && data.device ? data.device : data;
        if (nextDevice && typeof nextDevice === 'object' && nextDevice.id) {
          device = nextDevice;
          auth.update(base, { device });
        } else {
          device = { ...(device || { id: '', platform: devicePlatform() }), name: value };
          auth.update(base, { device });
        }
        settings.setDeviceName(value);
        toast('设备名称已更新', { type: 'success' });
        renderPairing();
      } catch (error) {
        if (error && (error.code === 'aborted' || error.code === 'unauthorized')) return;
        toast(error.message || '保存失败，请稍后再试', { type: 'error' });
      } finally {
        if (!signal.aborted) setButtonBusy(saveButton, false);
      }
    },
    { signal },
  );

  refreshMeButton.addEventListener(
    'click',
    () => {
      probeRealtime();
      void loadMe();
    },
    { signal },
  );

  /* ------------------------------------------------------------------ */
  /* 解除配对                                                            */
  /* ------------------------------------------------------------------ */

  unpairButton.addEventListener(
    'click',
    async () => {
      const confirmed = await confirmDialog({
        title: '解除配对？',
        message: '解除后这台手机将无法提交任务，需要重新输入电脑上显示的配对码。',
        confirmText: '解除配对',
        danger: true,
      });
      if (!confirmed) return;
      setButtonBusy(unpairButton, true, { label: '解除中…' });
      try {
        await api.unpair({ base, signal, timeout: 10000 });
      } catch (error) {
        if (error && error.code === 'aborted') return;
        // 401/404 表示 token 已经无效，等同于解除成功。
        const alreadyInvalid = error && (error.status === 401 || error.status === 404);
        if (!alreadyInvalid) {
          if (!signal.aborted) setButtonBusy(unpairButton, false);
          toast(error.message || '解除配对失败，请稍后再试', { type: 'error' });
          return;
        }
      }
      auth.clear(base);
      stopRealtime();
      toast('已解除配对');
      navigate('/pair', { replace: true });
    },
    { signal },
  );

  /* ------------------------------------------------------------------ */
  /* Relay 地址                                                          */
  /* ------------------------------------------------------------------ */

  function setRelayStatus(message, state) {
    relayStatus.hidden = false;
    relayStatus.dataset.state = state;
    relayStatus.textContent = message;
  }

  function refreshBaseDisplay() {
    effectiveBase.textContent = settings.getApiBase() || '未设置';
    relayResetButton.hidden = !settings.getApiBaseOverride();
  }

  relayTestButton.addEventListener(
    'click',
    async () => {
      if (testingRelay) return;
      const normalized = normalizeApiBase(relayInput.value);
      if (normalized === null) {
        setRelayStatus('地址格式不对，请填写 http(s)://域名:端口', 'error');
        return;
      }
      const target = normalized || settings.getDefaultApiBase();
      if (!target) {
        setRelayStatus('请填写完整的 Relay 地址', 'error');
        return;
      }
      testingRelay = true;
      setButtonBusy(relayTestButton, true, { label: '测试中…' });
      setRelayStatus('正在连接…', 'pending');
      const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
      try {
        const data = await api.health({ base: target, signal, timeout: 8000 });
        const elapsed = Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt);
        const version = data && data.version ? `v${data.version}` : '未知版本';
        setRelayStatus(`连接成功 · Relay ${version} · ${elapsed} ms`, 'ok');
      } catch (error) {
        if (error && error.code === 'aborted') return;
        setRelayStatus(`连接失败：${error.message || '请检查地址与网络'}`, 'error');
      } finally {
        testingRelay = false;
        if (!signal.aborted) setButtonBusy(relayTestButton, false);
      }
    },
    { signal },
  );

  relaySaveButton.addEventListener(
    'click',
    () => {
      const raw = relayInput.value;
      const normalized = normalizeApiBase(raw);
      if (normalized === null) {
        setRelayStatus('地址格式不对，请填写 http(s)://域名:端口', 'error');
        return;
      }
      const previousBase = settings.getApiBase();
      const result = raw.trim() === '' ? { ok: true, base: settings.resetApiBase() } : settings.setApiBase(normalized);
      if (!result.ok) {
        setRelayStatus('地址格式不对，请填写 http(s)://域名:端口', 'error');
        return;
      }
      const nextBase = settings.getApiBase();
      refreshBaseDisplay();
      relayInput.value = settings.getApiBaseOverride();
      if (nextBase === previousBase) {
        toast('服务器地址没有变化');
        return;
      }
      if (nextBase && auth.has(nextBase)) {
        toast('已切换服务器', { type: 'success' });
        restartRealtime();
        navigate('/home', { replace: true });
        return;
      }
      stopRealtime();
      toast('地址已保存，请重新配对', { type: 'success' });
      navigate('/pair', { replace: true });
    },
    { signal },
  );

  relayResetButton.addEventListener(
    'click',
    () => {
      const previousBase = settings.getApiBase();
      const nextBase = settings.resetApiBase();
      relayInput.value = '';
      refreshBaseDisplay();
      relayStatus.hidden = true;
      if (nextBase === previousBase) {
        toast('已恢复默认地址');
        return;
      }
      if (nextBase && auth.has(nextBase)) {
        toast('已恢复默认地址', { type: 'success' });
        restartRealtime();
        navigate('/home', { replace: true });
        return;
      }
      stopRealtime();
      toast('已恢复默认地址，请重新配对');
      navigate('/pair', { replace: true });
    },
    { signal },
  );

  /* ------------------------------------------------------------------ */
  /* 安装 / 离线能力 / 缓存                                              */
  /* ------------------------------------------------------------------ */

  function renderInstall() {
    if (isNativeApp()) {
      installArea.innerHTML = `<p class="field-help">当前是 Android App 版本，已支持系统分享。</p>`;
      return;
    }
    if (isInstalled()) {
      installArea.innerHTML = `<div class="kv"><span class="kv-key">安装</span><span class="kv-value ok-text">已安装到桌面</span></div>`;
      return;
    }
    if (canPromptInstall()) {
      installArea.innerHTML = `<button type="button" class="btn btn-soft btn-block" id="install-btn">${icon('install', { size: 18 })}<span>安装到主屏幕</span></button>`;
      const button = installArea.querySelector('#install-btn');
      if (button) {
        button.addEventListener(
          'click',
          async () => {
            setButtonBusy(button, true, { label: '正在打开…' });
            const outcome = await promptInstall();
            if (!signal.aborted) {
              setButtonBusy(button, false);
              renderInstall();
            }
            if (outcome === 'accepted') toast('安装成功，可以从主屏幕打开', { type: 'success' });
            else if (outcome === 'dismissed') toast('已取消安装');
            else if (outcome === 'unavailable') toast(installHintText());
          },
          { signal },
        );
      }
      return;
    }
    installArea.innerHTML = `<p class="field-help">${escapeHtml(installHintText())}</p>`;
  }

  function renderSwState() {
    const registration = getRegistration();
    if (registration) swState.textContent = '已启用，可离线打开';
    else if (typeof navigator !== 'undefined' && 'serviceWorker' in navigator) swState.textContent = '注册中…';
    else swState.textContent = '不可用（需要 HTTPS 或 localhost）';
  }

  /** 实时连接状态：已连接 / 重连中 / 兼容模式 / 未连接 */
  function renderRealtimeInfo() {
    const snapshot = getRealtimeSnapshot();
    const info = describeRealtime(snapshot);
    let label = info.label;
    if (snapshot.mode !== 'legacy' && snapshot.state === 'open') {
      label = snapshot.presence.online === false ? '已连接 · 电脑离线' : '已连接 · 电脑在线';
    } else if (snapshot.mode === 'legacy') {
      label = '兼容模式（按需刷新）';
    }
    rtState.textContent = label;
    rtState.classList.toggle('ok-text', snapshot.mode !== 'legacy' && snapshot.state === 'open');
    rtState.title = info.detail || '';
  }

  async function renderCacheSummary() {
    const summary = (await cacheSummary(base)) || { taskCount: 0, noteCount: 0, noteChars: 0 };
    if (signal.aborted) return;
    const size = summary.noteChars ? `，约 ${formatBytes(summary.noteChars * 2)}` : '';
    cacheSummaryEl.textContent = `${summary.taskCount} 个任务 / ${summary.noteCount} 篇笔记${size}`;
  }

  clearCacheButton.addEventListener(
    'click',
    async () => {
      const confirmed = await confirmDialog({
        title: '清除离线缓存？',
        message: '会删除本机缓存的任务列表与笔记。配对关系、待发送队列不受影响。',
        confirmText: '清除',
        danger: true,
      });
      if (!confirmed) return;
      await clearCaches(base);
      await renderCacheSummary();
      toast('已清除离线缓存', { type: 'success' });
    },
    { signal },
  );

  window.addEventListener('linknote:installable', renderInstall, { signal });
  window.addEventListener('linknote:installed', renderInstall, { signal });
  window.addEventListener('linknote:sw-registered', renderSwState, { signal });

  const onNetworkChange = () => {
    networkState.textContent = navigator.onLine ? '在线' : '离线';
  };
  window.addEventListener('online', onNetworkChange, { signal });
  window.addEventListener('offline', onNetworkChange, { signal });

  const unsubRealtimeEvents = [
    onRealtime('state', () => {
      renderRealtimeInfo();
      refreshPairingLive();
    }),
    onRealtime('presence', () => {
      renderRealtimeInfo();
      refreshPairingLive();
    }),
    onRealtime('hello', () => {
      // 重连后补一次一次性快照（不是轮询）
      void loadMe();
    }),
  ];

  /* ------------------------------------------------------------------ */
  /* 启动                                                                */
  /* ------------------------------------------------------------------ */

  container.querySelector('#back-btn').addEventListener('click', () => navigate('/home'), { signal });

  startRealtime();
  renderPairing();
  refreshBaseDisplay();
  renderInstall();
  renderSwState();
  renderRealtimeInfo();
  void renderCacheSummary();
  void loadMe();

  return () => {
    unsubRealtimeEvents.forEach((unsub) => {
      try {
        unsub();
      } catch (error) {
        // 忽略
      }
    });
  };
}

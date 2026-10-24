/**
 * 链记 LinkNote — 配对页
 * 输入 8 位配对码（忽略大小写/横线）+ 设备名，POST /api/v1/pairings/claim。
 * 高级选项里可以修改 Relay 地址（APK / 局域网调试必须）。
 */
import { api, isNetworkError } from '../api.js';
import { APP_NAME, APP_TAGLINE, DEFAULT_DEVICE_NAME, NATIVE_DEFAULT_RELAY } from '../config.js';
import { navigate } from '../router.js';
import { auth, settings, takeNavIntent } from '../store.js';
import { icon, setButtonBusy, toast } from '../ui.js';
import {
  devicePlatform,
  escapeHtml,
  normalizeApiBase,
  normalizePairCode,
} from '../util.js';

const PAIR_ERRORS = {
  invalid_code: '配对码格式不对，请检查电脑上显示的 8 位配对码',
  pairing_not_found: '找不到这个配对码，请确认电脑上显示的是最新配对码（10 分钟内有效）',
  pairing_expired: '配对码已过期，请在电脑上重新生成一个',
  pairing_already_claimed: '这个配对码已经被使用，请在电脑上重新生成',
  rate_limited: '尝试太频繁了，请等一会儿再试',
  origin_not_allowed: '当前页面来源不被 Relay 允许，请检查 Relay 的 ALLOWED_ORIGINS 配置',
  forbidden: '当前页面来源不被 Relay 允许',
  no_relay: '请先在「服务器地址」里填写 Relay 地址',
  network: '连不上 Relay，请检查网络与「服务器地址」',
  timeout: '连接 Relay 超时，请检查网络与「服务器地址」',
  bad_response: 'Relay 返回了无法识别的内容，请确认地址指向 LinkNote Relay',
};

function describePairError(error) {
  if (!error) return '配对失败，请稍后重试';
  if (error.code && PAIR_ERRORS[error.code]) return PAIR_ERRORS[error.code];
  if (isNetworkError(error)) return `${error.message || '连不上 Relay'}，请检查网络与「服务器地址」`;
  return error.message || '配对失败，请稍后重试';
}

export async function pairView(container, ctx) {
  const signal = ctx.signal;
  const storedSettings = settings.get();
  const defaultBase = settings.getDefaultApiBase();
  const currentBase = settings.getApiBase();

  container.innerHTML = `
    <section class="screen pair-screen">
      <header class="pair-hero">
        <span class="pair-logo" aria-hidden="true"><img src="./icons/icon-192.png" alt="" /></span>
        <h1 class="pair-brand">${escapeHtml(APP_NAME)}</h1>
        <p class="pair-tagline">${escapeHtml(APP_TAGLINE)}</p>
      </header>

      <div class="card pair-card">
        <h2 class="card-title">配对这台电脑</h2>
        <p class="card-sub">在电脑上打开 LinkNote Agent，选择「添加手机」，把屏幕上显示的配对码输入到这里。</p>

        <form id="pair-form" class="pair-form" novalidate>
          <label class="field" for="pair-code">
            <span class="field-label">配对码</span>
            <input
              id="pair-code"
              class="input code-input"
              type="text"
              inputmode="text"
              autocomplete="one-time-code"
              autocapitalize="characters"
              spellcheck="false"
              placeholder="XXXX-XXXX"
              maxlength="9"
              enterkeyhint="go"
              aria-describedby="pair-code-help"
            />
          </label>
          <p id="pair-code-help" class="field-help">8 位字母数字，忽略大小写和横线，10 分钟内有效。</p>

          <label class="field" for="pair-device-name">
            <span class="field-label">设备名称</span>
            <input
              id="pair-device-name"
              class="input"
              type="text"
              maxlength="40"
              placeholder="${escapeHtml(DEFAULT_DEVICE_NAME)}"
              autocomplete="nickname"
              enterkeyhint="done"
            />
          </label>

          <p id="pair-error" class="form-error" role="alert" hidden></p>

          <button type="submit" class="btn btn-primary btn-block" id="pair-submit">
            ${icon('send', { size: 18 })}<span>开始配对</span>
          </button>
          <button type="button" class="btn btn-ghost btn-block" id="pair-scan" hidden>
            ${icon('qr', { size: 18 })}<span>扫码输入</span>
          </button>
        </form>

        <details class="advanced" id="pair-advanced"${currentBase ? '' : ' open'}>
          <summary>服务器地址（Relay）</summary>
          <div class="advanced-body">
            <label class="field" for="pair-relay">
              <span class="field-label">Relay 地址</span>
              <input
                id="pair-relay"
                class="input"
                type="url"
                inputmode="url"
                autocomplete="url"
                spellcheck="false"
                placeholder="${escapeHtml(defaultBase || 'http://127.0.0.1:8787')}"
                value="${escapeHtml(settings.getApiBaseOverride())}"
              />
            </label>
            <p class="field-help">
              接口固定为 <code>/api/v1</code>。留空表示使用当前页面地址；原生 App 默认使用
              <code>${escapeHtml(NATIVE_DEFAULT_RELAY)}</code>。
            </p>
            <div class="btn-row">
              <button type="button" class="btn btn-ghost" id="pair-relay-test">
                ${icon('refresh', { size: 16 })}<span>保存并测试连接</span>
              </button>
            </div>
            <p id="pair-relay-status" class="relay-status" role="status" hidden></p>
          </div>
        </details>
      </div>

      <div class="pair-privacy">
        ${icon('cloud', { size: 16 })}
        <span>视频、音频和 AI Key 都留在你的电脑上，云端只中转链接与 Markdown。</span>
      </div>
      <p class="pair-footnote">配对一次即可长期使用；换手机或撤销后重新配对即可，不需要注册账号。</p>
    </section>`;

  const form = container.querySelector('#pair-form');
  const codeInput = container.querySelector('#pair-code');
  const nameInput = container.querySelector('#pair-device-name');
  const errorEl = container.querySelector('#pair-error');
  const submitButton = container.querySelector('#pair-submit');
  const scanButton = container.querySelector('#pair-scan');
  const advanced = container.querySelector('#pair-advanced');
  const relayInput = container.querySelector('#pair-relay');
  const relayTestButton = container.querySelector('#pair-relay-test');
  const relayStatus = container.querySelector('#pair-relay-status');

  let submitting = false;
  let testingRelay = false;
  let stopScanner = () => {};

  if (storedSettings.deviceName && storedSettings.deviceName !== DEFAULT_DEVICE_NAME) {
    nameInput.value = storedSettings.deviceName;
  }

  // 电脑端二维码内容：https://relay.../#/pair?code=XXXX-XXXX，扫码打开后自动填码。
  try {
    const presetCode = normalizePairCode(ctx.query?.get('code') || '');
    if (presetCode.valid) {
      codeInput.value = presetCode.formatted;
      clearError();
      codeInput.focus();
      codeInput.select();
    }
  } catch (error) {
    // 忽略无效的预填参数
  }

  if (!currentBase) {
    advanced.open = true;
    relayStatus.hidden = false;
    relayStatus.dataset.state = 'error';
    relayStatus.textContent = '还没有可用的 Relay 地址，请先填写并测试连接。';
  }

  function showError(message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  function clearError() {
    if (!errorEl.hidden) {
      errorEl.hidden = true;
      errorEl.textContent = '';
    }
  }

  function setRelayStatus(message, state) {
    relayStatus.hidden = false;
    relayStatus.dataset.state = state;
    relayStatus.textContent = message;
  }

  codeInput.addEventListener(
    'input',
    () => {
      const normalized = normalizePairCode(codeInput.value);
      if (codeInput.value !== normalized.formatted) codeInput.value = normalized.formatted;
      clearError();
    },
    { signal },
  );

  form.addEventListener(
    'submit',
    async (event) => {
      event.preventDefault();
      if (submitting) return;
      clearError();

      const normalized = normalizePairCode(codeInput.value);
      if (!normalized.valid) {
        showError('请输入 8 位配对码，例如 K7M4-9QXB');
        codeInput.focus();
        return;
      }

      const deviceName = (nameInput.value || '').trim() || DEFAULT_DEVICE_NAME;
      if (deviceName.length > 40) {
        showError('设备名称最多 40 个字符');
        nameInput.focus();
        return;
      }

      const base = settings.getApiBase();
      if (!base) {
        advanced.open = true;
        showError('请先在「服务器地址」里填写 Relay 地址');
        relayInput.focus();
        return;
      }

      submitting = true;
      setButtonBusy(submitButton, true, { label: '配对中…' });
      try {
        const data = await api.claimPairing({
          code: normalized.formatted,
          deviceName,
          platform: devicePlatform(),
          base,
          signal,
          timeout: 15000,
        });
        if (!data || typeof data.device_token !== 'string' || !data.device_token) {
          throw new Error('Relay 没有返回配对凭证，请重试');
        }
        settings.setDeviceName(deviceName);
        auth.save(base, {
          token: data.device_token,
          device: {
            id: data.device_id,
            name: deviceName,
            platform: devicePlatform(),
            paired_at: new Date().toISOString(),
          },
          agent: data.agent || null,
        });
        const intent = takeNavIntent();
        toast('配对成功，欢迎使用链记', { type: 'success' });
        navigate(intent || '/home', { replace: true });
      } catch (error) {
        if (error && error.code === 'aborted') return;
        showError(describePairError(error));
        if (error && error.code === 'invalid_code') {
          codeInput.focus();
          codeInput.select();
        }
      } finally {
        submitting = false;
        if (!signal.aborted) setButtonBusy(submitButton, false);
      }
    },
    { signal },
  );

  relayTestButton.addEventListener(
    'click',
    async () => {
      if (testingRelay) return;
      const raw = relayInput.value;
      const normalized = normalizeApiBase(raw);
      if (normalized === null) {
        setRelayStatus('地址格式不对，请填写 http(s)://域名:端口（例如 http://127.0.0.1:8787）', 'error');
        return;
      }
      const target = normalized || settings.getDefaultApiBase();
      if (!target) {
        setRelayStatus('请填写完整的 Relay 地址，例如 http://192.168.1.10:8787', 'error');
        return;
      }

      const previousBase = settings.getApiBase();
      testingRelay = true;
      setButtonBusy(relayTestButton, true, { label: '测试中…' });
      setRelayStatus('正在连接…', 'pending');
      const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
      try {
        const data = await api.health({ base: target, signal, timeout: 8000 });
        const elapsed = Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt);
        if (raw.trim() === '') settings.resetApiBase();
        else settings.setApiBase(target);
        relayInput.value = settings.getApiBaseOverride();
        const version = data && data.version ? `v${data.version}` : '未知版本';
        setRelayStatus(`连接成功 · Relay ${version} · ${elapsed} ms`, 'ok');
        if (settings.getApiBase() !== previousBase) {
          toast('服务器地址已更新', { type: 'success' });
        }
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

  /* ---------------- 扫码（可选，能力不支持时静默隐藏） ---------------- */
  const canScan =
    typeof window.BarcodeDetector === 'function' &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function';

  if (canScan) {
    scanButton.hidden = false;
    let stream = null;
    let timer = null;
    let overlay = null;
    let video = null;
    let detector = null;

    const stop = () => {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      if (stream) {
        stream.getTracks().forEach((track) => {
          try {
            track.stop();
          } catch (error) {
            // 忽略
          }
        });
        stream = null;
      }
      if (overlay) {
        overlay.remove();
        overlay = null;
      }
      video = null;
      detector = null;
    };
    stopScanner = stop;

    const openScanner = async () => {
      stop();
      try {
        detector = new window.BarcodeDetector({ formats: ['qr_code'] });
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        });
        overlay = document.createElement('div');
        overlay.className = 'scanner-overlay';
        overlay.innerHTML = `
          <div class="scanner-card">
            <div class="scanner-head">
              <span>扫描配对二维码</span>
              <button type="button" class="icon-btn" data-close aria-label="关闭扫码">${icon('close')}</button>
            </div>
            <div class="scanner-video">
              <video playsinline muted autoplay></video>
              <div class="scanner-frame" aria-hidden="true"></div>
            </div>
            <p class="scanner-hint">没有二维码的话，直接输入电脑上显示的配对码即可。</p>
          </div>`;
        document.body.appendChild(overlay);
        video = overlay.querySelector('video');
        video.srcObject = stream;
        await video.play().catch(() => {});
        overlay.querySelector('[data-close]').addEventListener('click', stop);
        overlay.addEventListener('click', (event) => {
          if (event.target === overlay) stop();
        });

        timer = setInterval(async () => {
          if (!detector || !video || video.readyState < 2) return;
          try {
            const codes = await detector.detect(video);
            if (!codes || !codes.length) return;
            for (const code of codes) {
              const normalized = normalizePairCode(code.rawValue || '');
              if (normalized.valid) {
                codeInput.value = normalized.formatted;
                codeInput.dispatchEvent(new Event('input', { bubbles: true }));
                stop();
                toast('已识别配对码');
                return;
              }
            }
          } catch (error) {
            // 单帧识别失败忽略
          }
        }, 450);
      } catch (error) {
        stop();
        toast('无法打开摄像头，请手动输入配对码', { type: 'error' });
      }
    };

    scanButton.addEventListener('click', () => void openScanner(), { signal });
  }

  signal.addEventListener('abort', () => stopScanner(), { once: true });

  return () => {
    stopScanner();
  };
}



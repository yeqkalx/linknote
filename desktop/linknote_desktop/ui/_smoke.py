"""LinkNote Desktop UI 冒烟测试（不启动真实引擎）。

用法（在 desktop 目录下）::

    $env:QT_QPA_PLATFORM = "offscreen"
    .\\.venv\\Scripts\\python.exe -m linknote_desktop.ui._smoke

或在仓库根目录::

    desktop\\.venv\\Scripts\\python.exe desktop\\linknote_desktop\\ui\\_smoke.py

脚本会：
1. 用临时 LOCALAPPDATA 实例化 DesktopController（不碰用户真实配置）；
2. 实例化 MainWindow + 6 个页面 + TrayIcon，跑 500ms 事件循环；
3. 用伪造的状态/供应商/转写数据驱动页面渲染（不访问真实引擎与 Relay）；
4. 实例化 OnboardingWizard 并走到第 2 步（安装 ffmpeg，不实际下载）；
5. 截图保存到 assets/desktop-ui*.png。

offscreen 平台在 Windows 上默认没有字体，截图会全是方框；脚本会自动设置
QT_QPA_FONTDIR=C:\\Windows\\Fonts 以获得正常中文渲染（仍使用 offscreen 平台）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UI_FILE = Path(__file__).resolve()
DESKTOP_DIR = UI_FILE.parents[2]
REPO_ROOT = UI_FILE.parents[3]

FAKE_PROVIDERS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "logo": "deepseek",
        "type": "built-in",
        "enabled": 1,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-demo-1234567890abcdef",
        "created_at": "2026-10-01T00:00:00",
    },
    {
        "id": "qwen",
        "name": "通义千问",
        "logo": "qwen",
        "type": "built-in",
        "enabled": 1,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-demo-qwen-000000",
        "created_at": "2026-10-01T00:00:00",
    },
]
FAKE_MODELS = [
    {"id": 1, "provider_id": "deepseek", "model_name": "deepseek-v4-flash"},
    {"id": 2, "provider_id": "deepseek", "model_name": "deepseek-v4-pro"},
    {"id": 3, "provider_id": "qwen", "model_name": "qwen-max"},
]
FAKE_TRANSCRIBER_CONFIG = {
    "transcriber_type": "fast-whisper",
    "whisper_model_size": "small",
    "available_types": [
        {"value": "fast-whisper", "label": "Faster Whisper（本地）"},
        {"value": "bcut", "label": "必剪（在线）"},
        {"value": "kuaishou", "label": "快手（在线）"},
        {"value": "groq", "label": "Groq（在线）"},
    ],
    "whisper_model_sizes": ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"],
    "whisper_builtin_models": {},
    "whisper_custom_models": {},
    "mlx_whisper_available": False,
}
FAKE_TRANSCRIBER_STATUS = {
    "whisper": [
        {"model_size": "tiny", "downloaded": True, "downloading": False, "failed": False},
        {"model_size": "base", "downloaded": True, "downloading": False, "failed": False},
        {"model_size": "small", "downloaded": False, "downloading": False, "failed": False},
        {"model_size": "medium", "downloaded": False, "downloading": False, "failed": False},
        {"model_size": "large-v3", "downloaded": False, "downloading": False, "failed": False},
        {
            "model_size": "large-v3-turbo",
            "downloaded": False,
            "downloading": True,
            "failed": False,
        },
    ],
    "mlx_whisper": [],
    "mlx_available": False,
}
FAKE_SYS_HEALTH = {
    "backend": "ok",
    "ffmpeg": "ok",
    "db": "ok",
    "whisper_model": {
        "size": "small",
        "type": "fast-whisper",
        "downloaded": False,
        "checked": True,
    },
}


class FakeEngineAPI:
    """本地假 BiliNote API：让页面渲染出完整状态且不访问网络。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def providers(self) -> list[dict[str, Any]]:
        self.calls.append("providers")
        return [dict(item) for item in FAKE_PROVIDERS]

    def models(self) -> list[dict[str, Any]]:
        self.calls.append("models")
        return [dict(item) for item in FAKE_MODELS]

    def update_provider(self, provider_id: str, **fields: Any) -> Any:
        self.calls.append("update_provider")
        return {"id": provider_id, **fields}

    def connect_test(self, provider_id: str, model: str | None = None) -> Any:
        self.calls.append("connect_test")
        return True

    def transcriber_config(self) -> dict[str, Any]:
        self.calls.append("transcriber_config")
        return dict(FAKE_TRANSCRIBER_CONFIG)

    def update_transcriber_config(self, transcriber_type: str, whisper_model_size: str | None = None) -> Any:
        self.calls.append("update_transcriber_config")
        return {"transcriber_type": transcriber_type, "whisper_model_size": whisper_model_size}

    def transcriber_status(self) -> dict[str, Any]:
        self.calls.append("transcriber_status")
        return dict(FAKE_TRANSCRIBER_STATUS)

    def download_whisper(self, model_size: str, transcriber_type: str = "fast-whisper") -> Any:
        self.calls.append("download_whisper")
        return {"msg": "模型下载已开始"}

    def sys_health(self) -> dict[str, Any]:
        self.calls.append("sys_health")
        return dict(FAKE_SYS_HEALTH)

    def update_proxy_config(self, enabled: bool, url: str | None = None) -> Any:
        self.calls.append("update_proxy_config")
        return {"enabled": enabled, "url": url or ""}

    def proxy_config(self) -> dict[str, Any]:
        self.calls.append("proxy_config")
        return {"enabled": False, "url": "", "effective": ""}


def _fake_status() -> dict[str, Any]:
    engine_path = r"C:\Program Files\LinkNote\engine\BiliNoteBackend.exe"
    ffmpeg_path = r"C:\Program Files\LinkNote\ffmpeg\bin\ffmpeg.exe"
    return {
        "engine": {
            "state": "running",
            "message": "BiliNote 引擎运行中",
            "pid": 4242,
            "owned": True,
            "path": engine_path,
            "alive": True,
        },
        "agent": {"running": True, "pid": 5150, "paired": True, "error": ""},
        "relay": {
            "url": "https://relay.xykong.xyz",
            "online": True,
            "agent": {
                "id": "agent-demo",
                "name": "我的电脑",
                "platform": "windows",
                "version": "0.2.0",
                "online": True,
            },
            "queue": {"queued": 1, "running": 0},
            "devices": [
                {
                    "id": "device-demo-1",
                    "name": "Pixel 8",
                    "platform": "android",
                    "paired_at": "2026-10-20T08:30:00Z",
                    "last_seen_at": "2026-10-24T05:20:00Z",
                },
                {
                    "id": "device-demo-2",
                    "name": "iPad Air",
                    "platform": "ipados",
                    "paired_at": "2026-10-22T12:00:00Z",
                    "last_seen_at": None,
                },
            ],
            "error": "",
        },
        "ffmpeg": {
            "available": True,
            "path": ffmpeg_path,
            "bin_dir": r"C:\Program Files\LinkNote\ffmpeg\bin",
            "version": "ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers",
        },
    }


def _fake_pairing() -> dict[str, Any]:
    expires = datetime.now(timezone.utc) + timedelta(minutes=8)
    return {
        "pairing_id": "pairing-demo",
        "code": "K7M4-9QXB",
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "status": "pending",
        "relay_url": "https://relay.xykong.xyz",
    }


def _prepare_environment() -> Path:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if fonts.is_dir():
            os.environ.setdefault("QT_QPA_FONTDIR", str(fonts))
    temp_root = Path(tempfile.mkdtemp(prefix="linknote-ui-smoke-"))
    local_app_data = temp_root / "LocalAppData"
    local_app_data.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(local_app_data)
    os.environ["APPDATA"] = str(temp_root / "AppData")
    (Path(os.environ["APPDATA"])).mkdir(parents=True, exist_ok=True)
    return temp_root


def _pump(app, milliseconds: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(max(0, int(milliseconds)), loop.quit)
    loop.exec()
    app.processEvents()


def _save(widget, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if pixmap.isNull():
        return False
    return bool(pixmap.save(str(path)))


def _grab_main_window(window, path: Path) -> bool:
    """QMainWindow 在 offscreen 下的 grab 会偶发子控件不重绘，改用侧栏+页面合成。"""
    from PySide6.QtGui import QColor, QPainter, QPixmap

    from linknote_desktop.ui import style as ui_style

    sidebar = getattr(window, "_sidebar", None)
    stack = getattr(window, "_stack", None)
    page = stack.currentWidget() if stack is not None else None
    if sidebar is None or page is None:
        return _save(window, path)
    canvas = QPixmap(window.size())
    canvas.fill(QColor(ui_style.PAPER))
    painter = QPainter(canvas)
    try:
        painter.drawPixmap(0, 0, sidebar.grab())
        painter.drawPixmap(sidebar.width(), 0, page.grab())
    finally:
        painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(canvas.save(str(path)))


def _grab_widget(window, path: Path) -> bool:
    return _grab_main_window(window, path)


def main() -> int:
    if str(DESKTOP_DIR) not in sys.path:
        sys.path.insert(0, str(DESKTOP_DIR))

    temp_root = _prepare_environment()
    errors: list[str] = []
    checks: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        checks.append((name, bool(condition)))
        if not condition:
            errors.append(name)

    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        print("[fatal] 无法导入 PySide6", traceback.format_exc())
        return 1

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("LinkNote")
    print(f"[smoke] Qt platform = {os.environ.get('QT_QPA_PLATFORM')}")
    print(f"[smoke] 临时数据目录 = {temp_root}")

    from linknote_desktop.controller import DesktopController
    from linknote_desktop.ui import MainWindow, OnboardingWizard, TrayIcon
    from linknote_desktop.ui import utils as ui_utils

    controller = DesktopController()
    # 冒烟期间不跑真实状态轮询 / 不访问真实引擎
    controller.config.onboarding_done = True  # 主窗口不自动弹出向导，单独测试
    fake_api = FakeEngineAPI()
    controller.engine_api = lambda: fake_api  # type: ignore[method-assign]
    controller.refresh_status_async = lambda: None  # type: ignore[method-assign]
    controller.ffmpeg_ready = lambda: False  # type: ignore[method-assign]

    window = MainWindow(controller)
    window.show()
    status = _fake_status()
    # 通过 controller 内部状态处理函数注入：同时更新 last_status 与 statusChanged
    controller._on_status(status)  # noqa: SLF001 - 冒烟脚本注入模拟状态
    controller.pairingChanged.emit(_fake_pairing())
    _pump(app, 500)

    check("MainWindow 尺寸 1000x720", window.width() == 1000 and window.height() == 720)
    check("6 个页面已创建", len(window.page_keys()) == 6)
    check("默认页为状态页", window.current_page_key() == "status")
    window.show_page("ai")
    _pump(app, 80)
    window.show_page("status")
    _pump(app, 80)

    tray = None
    try:
        tray = TrayIcon(controller, window)
        window.set_tray(tray)
        tray.show()
        tray.update_status(status)
        tray.notify_started()
        print(f"[ok] TrayIcon 创建成功（系统托盘可用: {tray.available()}）")
        check("TrayIcon 创建", True)
        check("托盘已自动注册到主窗口", window._tray is tray)
        try:
            ok_icon = ui_utils.status_icon("ok")
            error_icon = ui_utils.status_icon("error")
            check(
                "托盘绿/红状态图标不同",
                not ok_icon.isNull()
                and not error_icon.isNull()
                and ok_icon.pixmap(32, 32).toImage().cacheKey()
                != error_icon.pixmap(32, 32).toImage().cacheKey(),
            )
            check("托盘菜单状态文案", "引擎运行" in tray._status_action.text())
        except Exception as exc:  # noqa: BLE001
            check(f"托盘图标检查: {exc}", False)
    except Exception as exc:  # noqa: BLE001 - offscreen 下托盘可能不可用
        print(f"[warn] TrayIcon 在 offscreen 下不可用：{exc}")
        check("TrayIcon 创建（offscreen 允许失败）", True)

    # ---- 数据加载验证（走的是真实的 controller.run_async 回调链路）
    window.show_page("ai")
    _pump(app, 400)
    ai_page = window._pages["ai"]
    check("AI 页加载供应商列表", ai_page._provider_combo.count() >= 1)
    check("AI 页加载模型列表", ai_page._model_combo.count() >= 1)
    window.show_page("transcription")
    _pump(app, 400)
    trans_page = window._pages["transcription"]
    check("转写页加载配置", trans_page._size_combo.currentText() == "small")
    check("转写页加载模型状态", trans_page._rows_layout.count() >= 3)
    window.show_page("engine")
    _pump(app, 400)
    check("引擎页加载系统健康", window._pages["engine"]._health_layout.count() >= 2)
    window.show_page("status")
    _pump(app, 200)

    assets = REPO_ROOT / "assets"
    screenshots: list[tuple[Path, bool]] = []
    screenshots.append((assets / "desktop-ui.png", _grab_widget(window, assets / "desktop-ui.png")))
    print(f"[smoke] 截图: {assets / 'desktop-ui.png'}")

    for key in window.page_keys():
        try:
            window.show_page(key)
            _pump(app, 320)
            path = assets / f"desktop-ui-{key}.png"
            ok = _grab_widget(window, path)
            screenshots.append((path, ok))
            print(f"[smoke] 页面 {key:<14} 截图: {path.name} ({'ok' if ok else 'failed'})")
            if key == "transcription":
                trans_page = window._pages["transcription"]
                print(
                    "[debug] transcription combo at grab:",
                    trans_page._size_combo.count(),
                    repr(trans_page._size_combo.currentText()),
                    trans_page._size_combo.currentIndex(),
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"页面 {key} 渲染异常: {exc}")
            print(f"[error] 页面 {key} 渲染异常: {exc}")
    for path, ok in screenshots:
        check(f"截图 {path.name}", ok)

    window.show_page("status")
    _pump(app, 120)

    # ---- 首次配置向导：走到第 2 步（安装 ffmpeg），不实际下载
    wizard = None
    try:
        wizard = OnboardingWizard(controller)
        wizard.show()
        _pump(app, 120)
        wizard._go(wizard.FFMPEG)  # noqa: SLF001 - 冒烟脚本直接驱动步骤
        _pump(app, 450)
        check("向导当前为第 2 步（安装 ffmpeg）", wizard._step == wizard.FFMPEG)
        wizard_path = assets / "desktop-ui-onboarding.png"
        ok = _save(wizard, wizard_path)
        screenshots.append((wizard_path, ok))
        check("向导截图", ok)
        print(f"[ok] OnboardingWizard 第 2 步渲染正常，截图: {wizard_path}")
        # 再走一步看 AI 页（使用 FakeEngineAPI，不访问真实引擎）
        wizard._go(wizard.AI)  # noqa: SLF001
        _pump(app, 400)
        check("向导 AI 步骤加载供应商", wizard._ob_provider_combo.count() >= 1)
        ai_path = assets / "desktop-ui-onboarding-ai.png"
        ok = _save(wizard, ai_path)
        screenshots.append((ai_path, ok))
        print(f"[ok] OnboardingWizard AI 步骤渲染正常，截图: {ai_path}")
        # 完成步骤汇总页（不触发真实下载/配对）
        wizard._go(wizard.FINISH)  # noqa: SLF001
        _pump(app, 200)
        finish_path = assets / "desktop-ui-onboarding-finish.png"
        ok = _save(wizard, finish_path)
        screenshots.append((finish_path, ok))
        print(f"[ok] OnboardingWizard 完成步骤渲染正常，截图: {finish_path}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"OnboardingWizard 异常: {exc}")
        print("[error] OnboardingWizard 异常:", exc)
        traceback.print_exc()
    finally:
        if wizard is not None:
            try:
                wizard.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- 首次配置自动弹出（onboarding_done=False 时主窗口应自动打开向导）
    try:
        controller.config.onboarding_done = False
        window._maybe_show_onboarding()  # noqa: SLF001 - 验证自动弹窗接线
        _pump(app, 250)
        check("首次配置自动弹出向导", window._onboarding is not None)
        auto_wizard = window._onboarding
        if auto_wizard is not None:
            try:
                auto_wizard.close()
            except Exception:  # noqa: BLE001
                pass
            _pump(app, 200)
        check("向导关闭后 onboarding_done 已落盘", bool(controller.config.onboarding_done))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"首次配置自动弹出异常: {exc}")
        print("[error] 首次配置自动弹出异常:", exc)

    # ---- 其它组件单测
    try:
        code = ui_utils.normalize_pair_code("k7m49qxb")
        check("配对码格式化", code == "K7M4-9QXB")
        pixmap = ui_utils.pairing_qr_pixmap(code)
        check("二维码生成", not pixmap.isNull() and pixmap.width() > 100)
        remaining, expired = ui_utils.format_remaining(_fake_pairing()["expires_at"])
        check("配对码倒计时", (not expired) and "剩余" in remaining)
        tray_text = ui_utils.tray_status_text(status)
        check("托盘状态文案", "引擎运行" in tray_text and "Agent 在线" in tray_text)
        check("汇总状态为 green", ui_utils.overall_state(status) == "ok")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"工具函数异常: {exc}")
        print("[error] 工具函数异常:", exc)

    try:
        controller.pool.waitForDone(3000)
    except Exception:  # noqa: BLE001
        pass
    try:
        if tray is not None:
            tray.hide()
        window.hide()
        controller.shutdown()
    except Exception:  # noqa: BLE001
        pass

    print()
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if errors:
        print(f"\n[result] FAILED ({len(errors)} 个失败项)")
        for item in errors:
            print(f"  - {item}")
        return 1
    print(f"\n[result] PASS · {len(checks)} 项检查全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

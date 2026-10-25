"""LinkNote Desktop UI 冒烟测试（不启动真实引擎、不访问网络）。

用法（在 desktop 目录下）::

    $env:QT_QPA_PLATFORM = "offscreen"
    .\\.venv\\Scripts\\python.exe -m linknote_desktop.ui._smoke

或在仓库根目录::

    desktop\\.venv\\Scripts\\python.exe desktop\\linknote_desktop\\ui\\_smoke.py

脚本会：
1. 用临时 LOCALAPPDATA 实例化 DesktopController（不碰用户真实配置）；
2. 写入模拟的 Agent 日志 / 本地笔记 / outbox，驱动「当前任务」「历史笔记」；
3. 实例化 MainWindow（4 一级导航 + 4 二级页面）+ TrayIcon，跑事件循环并截图；
4. 用伪造的状态/供应商/转写数据驱动页面渲染（不访问真实引擎与 Relay）；
5. 实例化 OnboardingWizard，依次验证 ffmpeg / AI / 配对 / 完成步骤；
6. 验证 AI 模型自动刷新、勾选保存、配对成功常驻、历史/任务页数据；
7. 截图保存到 assets/desktop-ui*.png。

offscreen 平台在 Windows 上默认没有字体，截图会全是方框；脚本会自动设置
QT_QPA_FONTDIR=C:\\Windows\\Fonts 以获得正常中文渲染（仍使用 offscreen 平台）。
"""
from __future__ import annotations

import json
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
# 供应商动态可用模型（对应 BiliNote `/model_list/{provider_id}`）
FAKE_PROVIDER_MODELS = {
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"],
    "qwen": ["qwen-max", "qwen-plus"],
}
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
FAKE_RELAY_ME = {
    "online": True,
    "agent": {
        "id": "agent-demo",
        "name": "我的电脑",
        "platform": "windows",
        "version": "0.2.0",
        "online": True,
    },
    "queue": {"queued": 2, "running": 1},
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
}


class FakeEngineAPI:
    """本地假 BiliNote API：让页面渲染出完整状态且不访问网络。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    # ---- 供应商 / 模型
    def providers(self) -> list[dict[str, Any]]:
        self.calls.append("providers")
        return [dict(item) for item in FAKE_PROVIDERS]

    def models(self) -> list[dict[str, Any]]:
        self.calls.append("models")
        return [dict(item) for item in FAKE_MODELS]

    def update_provider(self, provider_id: str, **fields: Any) -> Any:
        self.calls.append("update_provider")
        for provider in FAKE_PROVIDERS:
            if provider["id"] == provider_id:
                provider.update(fields)
        return {"id": provider_id, **fields}

    def connect_test(self, provider_id: str, model: str | None = None) -> Any:
        self.calls.append("connect_test")
        return True

    def add_model(self, provider_id: str, model_name: str) -> Any:
        self.calls.append("add_model")
        if not any(
            str(item.get("provider_id")) == str(provider_id)
            and item.get("model_name") == model_name
            for item in FAKE_MODELS
        ):
            FAKE_MODELS.append(
                {
                    "id": len(FAKE_MODELS) + 1,
                    "provider_id": provider_id,
                    "model_name": model_name,
                }
            )
        return True

    # ---- 转写
    def transcriber_config(self) -> dict[str, Any]:
        self.calls.append("transcriber_config")
        return dict(FAKE_TRANSCRIBER_CONFIG)

    def update_transcriber_config(
        self, transcriber_type: str, whisper_model_size: str | None = None
    ) -> Any:
        self.calls.append("update_transcriber_config")
        return {"transcriber_type": transcriber_type, "whisper_model_size": whisper_model_size}

    def transcriber_status(self) -> dict[str, Any]:
        self.calls.append("transcriber_status")
        return json.loads(json.dumps(FAKE_TRANSCRIBER_STATUS))

    def download_whisper(self, model_size: str, transcriber_type: str = "fast-whisper") -> Any:
        self.calls.append("download_whisper")
        return {"msg": "模型下载已开始"}

    # ---- 健康 / 网络
    def sys_health(self) -> dict[str, Any]:
        self.calls.append("sys_health")
        return dict(FAKE_SYS_HEALTH)

    def update_proxy_config(self, enabled: bool, url: str | None = None) -> Any:
        self.calls.append("update_proxy_config")
        return {"enabled": enabled, "url": url or ""}

    def proxy_config(self) -> dict[str, Any]:
        self.calls.append("proxy_config")
        return {"enabled": False, "url": "", "effective": ""}

    # ---- 只读接口（与 services/bilinote_api.EngineAPI._request 对应）
    def _request(self, method: str, path: str, *, json: Any = None, timeout: float | None = None) -> Any:
        self.calls.append(f"_request:{method}:{path}")
        if path.startswith("/model_list/"):
            provider_id = path.rsplit("/", 1)[-1]
            names = FAKE_PROVIDER_MODELS.get(provider_id, [])
            return {"models": [{"id": name, "object": "model"} for name in names]}
        if path.startswith("/task_status/"):
            return {"status": "TRANSCRIBING", "message": ""}
        if path.startswith("/sys_check"):
            return None
        return None


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
            "url": "https://relay.example.com",
            "online": True,
            "agent": dict(FAKE_RELAY_ME["agent"]),
            "queue": dict(FAKE_RELAY_ME["queue"]),
            "devices": [dict(item) for item in FAKE_RELAY_ME["devices"]],
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
        "relay_url": "https://relay.example.com",
    }


def _fake_claimed_pairing() -> dict[str, Any]:
    data = _fake_pairing()
    data["status"] = "claimed"
    data["device"] = dict(FAKE_RELAY_ME["devices"][0])
    return data


def _seed_local_data(controller) -> None:
    """写入模拟 Agent 日志 / 本地笔记 / outbox，供「当前任务」「历史笔记」读取。"""
    paths = controller.data_paths()
    now = datetime.now()

    notes_dir = Path(paths["notes"])
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "task-demo-001.md").write_text(
        "# 如何用 LinkNote 做视频笔记\n\n"
        "## 摘要\n\n"
        "LinkNote 会把手机发来的视频链接转成结构化 Markdown 笔记。\n\n"
        "## 关键步骤\n\n"
        "1. 手机上分享链接；\n2. 电脑自动下载、转写、生成；\n3. 手机端查看结果。\n",
        encoding="utf-8",
    )
    (notes_dir / "task-demo-002.md").write_text(
        "# 会议纪要：桌面端体验评审\n\n"
        "- 配对成功后需要一直显示已配对设备\n"
        "- AI 模型需要支持自动刷新与勾选\n"
        "- 新增历史笔记与当前任务页\n",
        encoding="utf-8",
    )

    outbox_dir = Path(paths["outbox"])
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "complete-task-demo-003.json").write_text(
        json.dumps(
            {
                "task_id": "task-demo-003",
                "kind": "complete",
                "payload": {
                    "title": "待回传：短视频脚本速记",
                    "markdown": "# 短视频脚本速记\n\n- 开头 3 秒抓注意力\n- 中段给方法\n",
                    "meta": {"platform": "douyin", "url": "https://example.com/v/3"},
                },
                "attempts": 1,
                "created_at": (now - timedelta(minutes=12)).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log_path = Path(paths["agent_log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def ts(minutes_ago: int) -> str:
        value = now - timedelta(minutes=minutes_ago)
        return value.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"{ts(30)},001 [INFO] linknote_agent.runner: 领到任务 task_id=task-demo-old url=https://www.bilibili.com/video/BV1xx platform=bilibili",
        f"{ts(29)},000 [INFO] linknote_agent.runner: 已提交 BiliNote 任务 relay_task=task-demo-old bilinote_task=bili-1001 platform=bilibili",
        f"{ts(26)},001 [INFO] linknote_agent.runner: 任务完成并已回传 task_id=task-demo-old title=旧视频笔记 markdown=1200 字 duration=754 秒",
        f"{ts(18)},001 [WARNING] linknote_agent.runner: 任务 task-demo-fail 的失败状态无法回传：Relay 暂时不可达",
        f"{ts(15)},000 [ERROR] linknote_agent.runner: 任务失败已回传 task_id=task-demo-fail code=quota_exceeded message=模型账户余额不足",
        f"{ts(5)},001 [INFO] linknote_agent.runner: 领到任务 task_id=task-demo-current url=https://www.youtube.com/watch?v=demo platform=youtube",
        f"{ts(5)},002 [INFO] linknote_agent.runner: 已提交 BiliNote 任务 relay_task=task-demo-current bilinote_task=bili-2002 platform=youtube",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    controller.agent.save_pairing(
        {
            "agent_token": "agt_demo_token_123456",
            "agent_id": "agent-demo",
            "paired_at": "2026-10-20T08:30:00Z",
            "device_id": "device-demo-1",
            "device_name": "Pixel 8",
            "device_platform": "android",
        }
    )


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
    """QMainWindow 在 offscreen 下的 grab 会偶发子控件不重绘，改用组件合成。

    主窗口布局 = 左侧栏 + 右侧（顶部就绪横幅 + 页面栈），按真实几何位置拼接，
    保证截图里既能看到横幅，也能看到页面内容。
    """
    from PySide6.QtGui import QColor, QPainter, QPixmap

    from linknote_desktop.ui import style as ui_style

    sidebar = getattr(window, "_sidebar", None)
    stack = getattr(window, "_stack", None)
    banner = getattr(window, "_ready_banner", None)
    page = stack.currentWidget() if stack is not None else None
    if sidebar is None or page is None:
        return _save(window, path)
    canvas = QPixmap(window.size())
    canvas.fill(QColor(ui_style.PAPER))
    painter = QPainter(canvas)
    try:
        painter.drawPixmap(0, 0, sidebar.grab())
        content_left = sidebar.width() + 18
        top = 14
        if banner is not None:
            banner_pix = banner.grab()
            painter.drawPixmap(content_left, top, banner_pix)
            page_top = top + banner_pix.height() + 10
        else:
            page_top = top
        painter.drawPixmap(content_left, page_top, page.grab())
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
    from linknote_desktop import config as config_module

    controller = DesktopController()
    controller.config.onboarding_done = True  # 主窗口不自动弹出向导，单独测试
    fake_api = FakeEngineAPI()
    controller.engine_api = lambda: fake_api  # type: ignore[method-assign]
    controller.refresh_status_async = lambda: None  # type: ignore[method-assign]
    controller.ffmpeg_ready = lambda: False  # type: ignore[method-assign]
    controller._collect_status_safe = _fake_status  # type: ignore[method-assign]

    # 配对 / 任务 / 历史在冒烟里不访问真实 Relay
    controller.relay.me = lambda token: json.loads(json.dumps(FAKE_RELAY_ME))  # type: ignore[method-assign]
    controller.create_pairing_async = lambda agent_name=None: controller.pairingChanged.emit(  # type: ignore[method-assign]
        _fake_pairing()
    )
    controller.cancel_pairing = lambda: controller.pairingChanged.emit({})  # type: ignore[method-assign]
    controller.revoke_device = lambda device_id: None  # type: ignore[method-assign]
    controller.unpair = lambda: controller.pairingChanged.emit({})  # type: ignore[method-assign]
    controller.test_telegram_bot = lambda token: {  # type: ignore[method-assign]
        "ok": True,
        "username": "abclinknote_bot",
    }
    controller.test_relay = lambda url=None: {  # type: ignore[method-assign]
        "ok": True,
        "version": "0.5.1",
        "realtime": True,
        "status": "ok",
    }
    # v0.5：默认 Relay 必须为空（用于冒烟断言），随后再填示例地址让主界面就绪
    default_relay_url = str(controller.config.relay_base_url or "")
    controller.config.relay_base_url = "https://relay.example.com"
    controller.save_config()

    # 冒烟里没有真实 Agent 子进程：把快照里的 Agent 运行态伪装成运行中，
    # 让「当前任务」页展示和真实场景一致的运行中样式。
    _real_collect_tasks = controller.collect_task_snapshot

    def _fake_collect_tasks() -> dict:
        snapshot = _real_collect_tasks()
        snapshot["agent"] = {"running": True, "pid": 5150, "paired": True}
        return snapshot

    controller.collect_task_snapshot = _fake_collect_tasks  # type: ignore[method-assign]

    _seed_local_data(controller)

    window = MainWindow(controller)
    window.show()
    status = _fake_status()
    # 通过 controller 内部状态处理函数注入：同时更新 last_status 与 statusChanged
    controller._on_status(status)  # noqa: SLF001 - 冒烟脚本注入模拟状态
    controller.pairingChanged.emit(_fake_pairing())
    _pump(app, 500)

    check("MainWindow 尺寸 1000x720", window.width() == 1000 and window.height() == 720)
    check("8 个页面已创建（4 一级 + 4 二级）", len(window.page_keys()) == 8)
    check("默认页为笔记库", window.current_page_key() == "history")
    check(
        "一级导航只有 4 项",
        window.primary_page_keys() == ["history", "tasks", "devices", "settings"],
    )
    check(
        "二级页面不在侧栏",
        all(key not in window._nav_buttons for key in ("ai", "transcription", "engine", "logs")),
    )
    check("版本号为 0.5.1", ui_utils.APP_VERSION == "0.5.1")
    check("默认 Relay 为空", default_relay_url == "")
    # v0.5 迁移：作者旧 Relay / 旧 workers.dev 默认必须清空，自定义地址保留
    legacy_host = sorted(config_module._LEGACY_RELAY_HOSTS)[0]
    legacy_path = Path(temp_root) / "legacy-settings.json"
    config_module.DesktopConfig(relay_base_url=f"https://{legacy_host}").save(legacy_path)
    loaded_legacy = config_module.DesktopConfig.load(legacy_path)
    check("旧 Relay 域名迁移清空", loaded_legacy.relay_base_url == "")
    workers_legacy = (
        f"https://{config_module._LEGACY_WORKERS_DEV_PREFIX}.demo"
        f"{config_module._LEGACY_WORKERS_DEV_SUFFIX}"
    )
    workers_path = Path(temp_root) / "legacy-workers-settings.json"
    config_module.DesktopConfig(relay_base_url=workers_legacy).save(workers_path)
    loaded_workers = config_module.DesktopConfig.load(workers_path)
    check("旧 workers.dev Relay 迁移清空", loaded_workers.relay_base_url == "")
    custom_path = Path(temp_root) / "custom-relay-settings.json"
    config_module.DesktopConfig(relay_base_url="https://relay.example.com").save(custom_path)
    loaded_custom = config_module.DesktopConfig.load(custom_path)
    check("自定义 Relay 迁移保留", loaded_custom.relay_base_url == "https://relay.example.com")
    check("自定义 Relay 不标记迁移", getattr(loaded_custom, "_legacy_relay_cleared", True) is False)
    check("就绪横幅-已准备好接收视频", "已准备好接收视频" in window._ready_banner._title.text())

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

    # ---- 就绪横幅需要处理态 + 跳转；设置页二级入口 + 返回
    unready = _fake_status()
    unready["engine"]["state"] = "stopped"
    unready["engine"]["message"] = "引擎未运行"
    unready["agent"]["paired"] = False
    unready["agent"]["running"] = False
    unready["ffmpeg"]["available"] = False
    controller._on_status(unready)  # noqa: SLF001
    _pump(app, 200)
    check("就绪横幅-需要处理", "需要处理" in window._ready_banner._title.text())
    check("就绪横幅跳转目标为设备页", window._ready_banner.target_key == "devices")
    window._ready_banner.clicked.emit()
    _pump(app, 200)
    check("点击横幅跳到设备页", window.current_page_key() == "devices")
    check("设备页有配对面板", hasattr(window._pages["devices"], "_pairing_panel"))
    check(
        "设备页有连接状态卡",
        all(hasattr(window._pages["devices"], name) for name in ("_relay_card", "_agent_card", "_engine_card")),
    )

    window.show_page("settings")
    _pump(app, 220)
    settings_page = window._pages["settings"]
    check(
        "设置页有高级功能入口",
        all(hasattr(settings_page, name) for name in ("_archive_switch", "_archive_export_button")),
    )
    settings_page.goto("ai")
    _pump(app, 320)
    check("设置入口跳到 AI 模型页", window.current_page_key() == "ai")
    ai_page = window._pages["ai"]
    check(
        "二级页面显示返回设置",
        ai_page._back_button is not None and ai_page._back_button.isVisible(),
    )
    ai_page._back_button.click()
    _pump(app, 220)
    check("二级页面可返回设置", window.current_page_key() == "settings")

    controller._on_status(_fake_status())  # noqa: SLF001 - 恢复正常状态
    _pump(app, 200)
    check("就绪横幅恢复已准备好", "已准备好接收视频" in window._ready_banner._title.text())

    # ---- 未配置 Relay 是阻塞项：横幅提示并跳转设置页
    configured_relay = str(controller.config.relay_base_url or "")
    controller.config.relay_base_url = ""
    window.refresh_ready_banner()
    _pump(app, 200)
    check("未配置 Relay 进入就绪横幅", "未配置 Relay" in window._ready_banner._title.text())
    check("未配置 Relay 跳转设置页", window._ready_banner.target_key == "settings")
    window._ready_banner.clicked.emit()
    _pump(app, 200)
    check("点击未配置 Relay 横幅跳到设置页", window.current_page_key() == "settings")
    controller.config.relay_base_url = configured_relay
    window.refresh_ready_banner()
    _pump(app, 200)

    # ---- 数据加载验证（走真实的 controller.run_async 回调链路）
    window.show_page("ai")
    _pump(app, 500)
    ai_page = window._pages["ai"]
    check("AI 页加载供应商列表", ai_page._provider_combo.count() >= 1)
    check("AI 页自动加载可用模型（可勾选）", len(ai_page._available_models) >= 2)
    check("AI 页模型列表为勾选样式", len(ai_page._model_rows) >= 2)
    check(
        "AI 页默认勾选当前模型",
        any(row.isChecked() for row in ai_page._model_rows.values()),
    )
    # 保存 API Key 后应自动重新拉取模型（不需要手动刷新）
    ai_page._api_key_edit.setText("sk-test-abcdefghijklmn")
    ai_page._save_provider()
    _pump(app, 600)
    check("AI 保存 Key 后自动刷新模型列表", len(ai_page._model_rows) >= 2)
    ai_save_msg = ai_page._provider_msg._label.text()
    check(
        "AI 保存 Key 成功提示",
        bool(ai_save_msg) and "失败" not in ai_save_msg,
    )
    # 测试连接成功后也应自动拉取
    ai_page._test_provider()
    _pump(app, 600)
    check("AI 测试连接后自动刷新模型列表", len(ai_page._model_rows) >= 2)
    # 勾选并保存为手机默认
    ai_page._select_model("deepseek-chat")
    check("AI 勾选模型生效", ai_page._selected_model == "deepseek-chat")
    ai_page._save_default_model()
    _pump(app, 700)
    check("AI 保存后手机默认模型更新", controller.config.model_name == "deepseek-chat")
    check(
        "AI 保存后同步 Agent 配置",
        str(controller.agent.read_config().get("model_name")) == "deepseek-chat",
    )
    # 恢复默认模型，保证后续截图/JSON 一致，并重新加载一次模型列表
    controller.config.provider_id = "deepseek"
    controller.config.model_name = "deepseek-v4-flash"
    controller.save_config()
    ai_page._load_models()  # noqa: SLF001
    _pump(app, 500)

    window.show_page("transcription")
    _pump(app, 450)
    trans_page = window._pages["transcription"]
    check("转写页加载配置", trans_page._size_combo.currentText() == "small")
    check("转写页加载模型状态", trans_page._rows_layout.count() >= 3)
    window.show_page("engine")
    _pump(app, 450)
    check("引擎页加载系统健康", window._pages["engine"]._health_layout.count() >= 2)

    # ---- 当前任务页
    window.show_page("tasks")
    _pump(app, 800)
    tasks_page = window._pages["tasks"]
    snapshot = tasks_page._snapshot or {}
    check("当前任务页读取到快照", bool(snapshot.get("generated_label")))
    check("当前任务页识别运行中任务", bool(snapshot.get("current")))
    check("当前任务页显示最近完成/失败", len(snapshot.get("recent") or []) >= 2)
    check("当前任务页显示队列", int((snapshot.get("queue") or {}).get("queued") or 0) >= 1)
    check("当前任务页当前任务卡片可见", not tasks_page._current_empty.isVisible())

    # ---- 笔记库（含搜索过滤、预览）
    window.show_page("history")
    _pump(app, 800)
    history_page = window._pages["history"]
    check("笔记库读取本地笔记", len(history_page._items) >= 2)
    check("笔记库统计字数", int(history_page._counts.get("words") or 0) > 0)
    _pump(app, 250)
    check("笔记库 Markdown 预览", bool(history_page._preview_view.markdown_text().strip()))
    history_page._search_edit.setText("会议")
    _pump(app, 220)
    check("笔记库搜索按标题过滤", len(history_page._items) == 1)
    history_page._search_edit.setText("bilibili")
    _pump(app, 220)
    check("笔记库搜索按平台过滤", len(history_page._items) == 0)
    history_page._search_edit.clear()
    _pump(app, 220)
    check("笔记库搜索清空恢复列表", len(history_page._items) >= 2)

    # ---- 自动归档（Obsidian/本地目录）
    archive_dir = Path(temp_root) / "ObsidianVault"
    archive_dir.mkdir(parents=True, exist_ok=True)
    controller.config.archive_enabled = True
    controller.config.archive_dir = str(archive_dir)
    controller.save_config()
    _pump(app, 150)
    archive_result = controller.archive_notes()
    check("归档导出本地笔记", int(archive_result.get("exported") or 0) == 2)
    exported_files = sorted(archive_dir.glob("*.md"))
    check("归档生成 Markdown 文件", len(exported_files) == 2)
    if exported_files:
        archive_text = exported_files[0].read_text(encoding="utf-8")
        check(
            "归档 frontmatter 字段完整",
            all(
                field in archive_text
                for field in (
                    "title:",
                    "source_url:",
                    "platform:",
                    "created_at:",
                    "model:",
                    "app_version:",
                )
            ),
        )
        check("归档 frontmatter 版本 0.5.1", 'app_version: "0.5.1"' in archive_text)
    else:
        check("归档 frontmatter 字段完整", False)
        check("归档 frontmatter 版本 0.5.1", False)
    second_archive = controller.archive_notes()
    check(
        "归档幂等（重复导出 0 篇）",
        int(second_archive.get("exported") or 0) == 0
        and int(second_archive.get("skipped") or 0) == 2,
    )
    controller.config.archive_dir = str(Path(temp_root) / "NoSuchVault")
    controller.save_config()
    missing_result = controller.archive_notes()
    check("归档目录不存在报错", "不存在" in str(missing_result.get("error") or ""))
    controller.config.archive_dir = "C:\\bad<>dir"
    controller.save_config()
    invalid_result = controller.archive_notes()
    check("归档非法路径报错", "非法字符" in str(invalid_result.get("error") or ""))
    controller.config.archive_dir = str(archive_dir)
    controller.save_config()
    window.show_page("settings")
    _pump(app, 350)
    settings_page = window._pages["settings"]
    check("设置页归档目录已回填", str(settings_page._archive_dir_edit.text()) == str(archive_dir))
    settings_page._export_now()
    _pump(app, 600)
    archive_msg = settings_page._archive_msg._label.text()
    check("设置页立即导出有结果提示", ("跳过" in archive_msg) or ("导出" in archive_msg))

    # ---- Relay 设置（默认空 + 校验 URL + 测试连接 + 旧配置迁移提示）
    saved_relay = str(controller.config.relay_base_url or "")
    controller.config.relay_base_url = ""
    controller.config._legacy_relay_cleared = True
    settings_page.on_show()
    _pump(app, 150)
    check(
        "设置页提示旧 Relay 已清空",
        "旧版" in settings_page._connection_msg._label.text(),
    )
    controller.config.relay_base_url = saved_relay
    controller.config._legacy_relay_cleared = False
    settings_page.on_show()
    _pump(app, 150)
    check(
        "设置页 Relay 地址已回填",
        settings_page._relay_edit.text() == "https://relay.example.com",
    )
    check(
        "Relay 占位符是示例域名",
        "your-relay.example.com" in settings_page._relay_edit.placeholderText(),
    )
    settings_page._test_relay()
    _pump(app, 400)
    check("设置页 Relay 测试成功", "连接成功" in settings_page._connection_msg._label.text())
    settings_page._relay_edit.setText("ftp://invalid-relay")
    settings_page._save_connection()
    _pump(app, 150)
    check("设置页校验 Relay URL", "http" in settings_page._connection_msg._label.text())
    settings_page._relay_edit.setText("https://relay.example.com")
    settings_page._save_connection()
    _pump(app, 300)
    check(
        "设置页保存 Relay",
        str(controller.config.relay_base_url) == "https://relay.example.com",
    )

    # ---- 消息入口（仅 Telegram；QQ 已删除）
    check(
        "设置页有消息入口卡",
        hasattr(settings_page, "_bot_card")
        and hasattr(settings_page, "_telegram_token_edit"),
    )
    check(
        "QQ 入口已删除",
        not hasattr(controller, "test_qq_bot")
        and not hasattr(settings_page, "_qq_app_secret_edit")
        and not hasattr(controller.config, "qq_enabled"),
    )
    controller.config.telegram_enabled = True
    controller.config.telegram_bot_token = "1234567890:AAH-smoke-telegram-token"
    controller.config.telegram_bot_username = "abclinknote_bot"
    controller.config.telegram_allow_groups = True
    settings_page._load_bot_channels()
    _pump(app, 120)
    telegram_mask = settings_page._telegram_token_edit.text()
    check("Telegram Token 显示掩码", bool(telegram_mask) and "…" in telegram_mask)
    check(
        "Telegram Token 未明文显示",
        telegram_mask != controller.config.telegram_bot_token,
    )
    check(
        "消息入口开关已回填",
        settings_page._telegram_enable_switch.isChecked()
        and settings_page._telegram_allow_groups_switch.isChecked(),
    )

    # 测试连接（fake controller 方法，不访问网络）
    settings_page._test_telegram()
    _pump(app, 400)
    telegram_test_msg = settings_page._telegram_msg._label.text()
    check("Telegram 测试连接显示 @username", "@abclinknote_bot" in telegram_test_msg)

    # 修改字段 + 保存：写 config 并同步 agent.json
    new_telegram_token = "9999999999:AAH-new-smoke-token"
    settings_page._telegram_token_edit.setText(new_telegram_token)
    settings_page._save_bot_channels()
    _pump(app, 500)
    check("消息入口保存写入 config", controller.config.telegram_bot_token == new_telegram_token)
    agent_json = controller.agent.read_config()
    check("消息入口同步到 agent.json", agent_json.get("telegram_bot_token") == new_telegram_token)
    check(
        "agent.json 不含 QQ 字段",
        not any(str(key).startswith("qq_") for key in agent_json),
    )
    check(
        "agent.json 保留既有 bot 字段",
        "telegram_device_token" in agent_json or "agent_token" in agent_json,
    )
    check("消息入口保存成功提示", "已保存" in settings_page._bot_msg._label.text())
    settings_page._load_bot_channels()
    _pump(app, 120)
    check(
        "保存后重新掩码显示",
        settings_page._telegram_token_edit.text() != new_telegram_token
        and "…" in settings_page._telegram_token_edit.text(),
    )

    # 滚动到「消息入口」卡截图（设置页较长）
    try:
        scroll = settings_page.verticalScrollBar()
        scroll.setValue(max(0, settings_page._bot_card.y() - 8))
        _pump(app, 250)
        bots_path = REPO_ROOT / "assets" / "desktop-ui-settings-bots.png"
        ok = _grab_widget(window, bots_path)
        check("截图 desktop-ui-settings-bots.png", ok)
        print(f"[ok] 消息入口设置截图: {bots_path}")
        scroll.setValue(0)
        _pump(app, 150)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"消息入口截图异常: {exc}")
        print("[error] 消息入口截图异常:", exc)

    # ---- Telegram 本地模式（无 Relay，v0.5.1）
    saved_relay = str(controller.config.relay_base_url or "")
    controller.config.relay_base_url = ""
    controller.save_config()
    window.refresh_ready_banner()
    _pump(app, 250)
    check("Telegram 本地模式判定", controller.config.telegram_local_mode() is True)
    check(
        "本地模式就绪横幅",
        "Telegram 本地模式（无需 Relay）" in window._ready_banner._title.text(),
    )
    check("本地模式横幅跳转设置", window._ready_banner.target_key == "settings")
    # 本地模式是「未配对」场景：清掉模拟 status 里的已配对/设备
    local_status = _fake_status()
    local_status["agent"]["paired"] = False
    local_status["relay"]["devices"] = []
    controller._on_status(local_status)  # noqa: SLF001
    window.show_page("devices")
    _pump(app, 250)
    devices_page = window._pages["devices"]
    local_panel = devices_page._pairing_panel
    local_panel.refresh()
    _pump(app, 150)
    check("本地模式配对按钮禁用", not local_panel._generate_button.isEnabled())
    check("本地模式配对提示", "本地模式" in local_panel._idle_msg._label.text())
    check("本地模式 Agent 可启动", devices_page._agent_button.isEnabled())

    window.show_page("settings")
    _pump(app, 250)
    settings_page._load_bot_channels()
    _pump(app, 120)
    check(
        "设置页提示本地模式",
        "本地模式" in settings_page._telegram_msg._label.text()
        or "本地模式" in settings_page._connection_msg._label.text(),
    )
    # 本地模式下的「消息入口」截图（Telegram 本地模式提示）
    try:
        scroll = settings_page.verticalScrollBar()
        scroll.setValue(max(0, settings_page._bot_card.y() - 8))
        _pump(app, 200)
        bots_local_path = REPO_ROOT / "assets" / "desktop-ui-settings-bots.png"
        check(
            "截图 desktop-ui-settings-bots.png（本地模式）",
            _grab_widget(window, bots_local_path),
        )
        scroll.setValue(0)
        _pump(app, 120)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"本地模式设置截图异常: {exc}")

    # 本地模式横幅 + 设备页截图
    window.show_page("devices")
    _pump(app, 250)
    local_path = REPO_ROOT / "assets" / "desktop-ui-local-mode.png"
    check("截图 desktop-ui-local-mode.png", _grab_widget(window, local_path))

    # 无 Relay 且 Telegram 未启用 → 仍然阻塞
    controller.config.telegram_enabled = False
    window.refresh_ready_banner()
    _pump(app, 200)
    check(
        "无 Relay 且无 Telegram 仍阻塞",
        "未配置 Relay" in window._ready_banner._title.text(),
    )
    controller.config.telegram_enabled = True
    controller.config.relay_base_url = saved_relay
    controller.save_config()
    controller._on_status(_fake_status())  # noqa: SLF001 - 恢复配对状态
    window.refresh_ready_banner()
    _pump(app, 200)
    check(
        "恢复 Relay 后横幅恢复已准备好",
        "已准备好接收视频" in window._ready_banner._title.text(),
    )

    # ---- 配对成功态（设备页 PairingPanel）
    controller.pairingChanged.emit(_fake_claimed_pairing())
    _pump(app, 250)
    panel = window._pages["devices"]._pairing_panel
    check("配对成功后设备列表常驻", panel._devices_layout.count() >= 2)
    check("配对成功文案", "配对成功" in panel._state_badge.text())
    check("配对成功后设备区仍然可见", not panel._devices_container.isHidden())
    # 恢复到等待领取，截图同时展示二维码 + 设备
    controller.pairingChanged.emit(_fake_pairing())
    _pump(app, 200)
    check("配对等待态显示二维码", bool(panel._qr_label.pixmap()))

    window.show_page("history")
    _pump(app, 250)

    assets = REPO_ROOT / "assets"
    screenshots: list[tuple[Path, bool]] = []
    screenshots.append((assets / "desktop-ui.png", _grab_widget(window, assets / "desktop-ui.png")))
    print(f"[smoke] 截图: {assets / 'desktop-ui.png'}")

    for key in window.page_keys():
        try:
            window.show_page(key)
            _pump(app, 420)
            path = assets / f"desktop-ui-{key}.png"
            ok = _grab_widget(window, path)
            screenshots.append((path, ok))
            print(f"[smoke] 页面 {key:<14} 截图: {path.name} ({'ok' if ok else 'failed'})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"页面 {key} 渲染异常: {exc}")
            print(f"[error] 页面 {key} 渲染异常: {exc}")
    for path, ok in screenshots:
        check(f"截图 {path.name}", ok)

    # 额外产出「笔记库」命名（与页面 key 的 history 同图）
    try:
        import shutil

        shutil.copyfile(
            assets / "desktop-ui-history.png", assets / "desktop-ui-library.png"
        )
        check("截图 desktop-ui-library.png", True)
    except OSError as exc:  # noqa: BLE001
        check(f"截图 desktop-ui-library.png: {exc}", False)

    window.show_page("history")
    _pump(app, 120)

    # ---- 首次配置向导：Relay / ffmpeg / AI / 配对 / 完成 步骤
    wizard = None
    try:
        wizard = OnboardingWizard(controller)
        wizard.show()
        _pump(app, 120)
        wizard._go(wizard.RELAY)  # noqa: SLF001 - 冒烟脚本直接驱动步骤
        _pump(app, 220)
        check("向导新增「连接 Relay」步骤", wizard._step == wizard.RELAY)
        check(
            "向导 Relay 占位符是示例域名",
            "your-relay.example.com" in wizard._ob_relay_edit.placeholderText(),
        )
        wizard._ob_relay_edit.setText("https://relay.example.com")
        wizard._test_relay()
        _pump(app, 500)
        check("向导 Relay 测试连接成功", "连接成功" in wizard._ob_relay_msg._label.text())
        relay_path = assets / "desktop-ui-onboarding-relay.png"
        ok = _save(wizard, relay_path)
        screenshots.append((relay_path, ok))
        check("向导 Relay 步骤截图", ok)
        print(f"[ok] OnboardingWizard Relay 步骤渲染正常，截图: {relay_path}")

        wizard._go(wizard.FFMPEG)  # noqa: SLF001
        _pump(app, 500)
        check("向导当前为第 3 步（安装 ffmpeg）", wizard._step == wizard.FFMPEG)
        wizard_path = assets / "desktop-ui-onboarding.png"
        ok = _save(wizard, wizard_path)
        screenshots.append((wizard_path, ok))
        check("向导截图", ok)
        print(f"[ok] OnboardingWizard ffmpeg 步骤渲染正常，截图: {wizard_path}")

        # 未配置 Relay 时：配对步骤禁用生成按钮并提示
        saved_relay = str(controller.config.relay_base_url or "")
        controller.config.relay_base_url = ""
        wizard._go(wizard.PAIR)  # noqa: SLF001
        _pump(app, 300)
        check(
            "未配置 Relay 时向导配对按钮禁用",
            not wizard._pairing_panel._generate_button.isEnabled(),
        )
        check(
            "未配置 Relay 时向导有提示",
            "Relay" in wizard._ob_pair_msg._label.text(),
        )
        controller.config.relay_base_url = saved_relay
        wizard._go(wizard.RELAY)  # noqa: SLF001
        _pump(app, 150)
        wizard._ob_relay_edit.setText(saved_relay)
        wizard._save_relay_config()

        wizard._go(wizard.AI)  # noqa: SLF001
        _pump(app, 500)
        check("向导 AI 步骤加载供应商", wizard._ob_provider_combo.count() >= 1)
        ai_path = assets / "desktop-ui-onboarding-ai.png"
        ok = _save(wizard, ai_path)
        screenshots.append((ai_path, ok))
        print(f"[ok] OnboardingWizard AI 步骤渲染正常，截图: {ai_path}")

        # 配对步骤：模拟「首次未配对」场景，验证始终可见 / 可生成配对码 / 二维码
        controller.agent.clear_pairing()
        unpaired_status = _fake_status()
        unpaired_status["agent"]["paired"] = False
        unpaired_status["relay"]["devices"] = []
        controller._on_status(unpaired_status)  # noqa: SLF001
        _pump(app, 150)
        wizard._go(wizard.PAIR)  # noqa: SLF001
        _pump(app, 550)
        pair_panel = wizard._pairing_panel
        check("向导配对步骤可见", pair_panel.isVisible())
        check("向导配对步骤显示配对码", bool(pair_panel._pair_code_label.text().strip()))
        check("向导配对步骤显示二维码", bool(pair_panel._qr_label.pixmap()))
        pair_path = assets / "desktop-ui-onboarding-pair.png"
        ok = _save(wizard, pair_path)
        screenshots.append((pair_path, ok))
        check("向导配对步骤截图", ok)
        print(f"[ok] OnboardingWizard 配对步骤渲染正常，截图: {pair_path}")
        # 手机领取 → 成功态：设备列表仍常驻（状态刷新后）
        controller.pairingChanged.emit(_fake_claimed_pairing())
        _pump(app, 220)
        check("向导配对成功态文案", "配对成功" in pair_panel._state_badge.text())
        _seed_local_data(controller)  # 恢复模拟配对 token
        controller._on_status(_fake_status())  # noqa: SLF001
        _pump(app, 220)
        check("向导配对成功态设备列表常驻", pair_panel._devices_layout.count() >= 2)

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
        _pump(app, 300)
        check("首次配置自动弹出向导", window._onboarding is not None)
        auto_wizard = window._onboarding
        if auto_wizard is not None:
            try:
                auto_wizard.close()
            except Exception:  # noqa: BLE001
                pass
            _pump(app, 250)
        check("向导关闭后 onboarding_done 已落盘", bool(controller.config.onboarding_done))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"首次配置自动弹出异常: {exc}")
        print("[error] 首次配置自动弹出异常:", exc)

    # ---- 其它组件单测
    try:
        code = ui_utils.normalize_pair_code("k7m49qxb")
        check("配对码格式化", code == "K7M4-9QXB")
        pixmap = ui_utils.pairing_qr_pixmap(code, 136, "https://relay.example.com")
        check("二维码生成", not pixmap.isNull() and pixmap.width() > 100)
        check("未配置 Relay 时不生成二维码", ui_utils.pairing_qr_pixmap(code).isNull())
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


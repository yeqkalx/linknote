"""主窗口：左侧导航 + 右侧页面栈 + 关闭到托盘 + 首次配置向导入口。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import style, utils
from .pages import ADVANCED_KEYS, DEFAULT_PAGE, NAV_ITEMS, PRIMARY_NAV
from .widgets import NavButton, ReadyBanner, StatusDot, Toast

APP_TITLE = "linknote 链记"


class MainWindow(QMainWindow):
    """LinkNote 主窗口：1000×720，左侧 220px 导航，右侧 QStackedWidget。"""

    NAV_WIDTH = 220

    def __init__(self, controller, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.controller = controller
        app = QApplication.instance()
        if app is not None:
            style.apply_theme(app)
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(utils.app_icon())
        self.resize(1000, 720)
        self.setMinimumSize(900, 620)

        self._tray: Any = None
        self._quitting = False
        self._tray_hint_shown = False
        self._refresh_pending = False
        self._onboarding: Any = None
        self._last_status: dict = controller.last_status or {}
        self._last_tasks: dict = controller.last_tasks or {}
        self._nav_buttons: Dict[str, NavButton] = {}

        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = self._build_sidebar()
        root.addWidget(self._sidebar)

        content = QWidget()
        content.setObjectName("Root")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 14, 18, 0)
        content_layout.setSpacing(10)
        self._ready_banner = ReadyBanner()
        self._ready_banner.clicked.connect(self._on_ready_banner_clicked)
        content_layout.addWidget(self._ready_banner, 0)

        self._stack = QStackedWidget()
        self._stack.setObjectName("Pages")
        content_layout.addWidget(self._stack, 1)
        root.addWidget(content, 1)

        self._pages: Dict[str, Any] = {}
        self._build_pages()

        self._toast = Toast(central)
        self._connect_controller()
        self.show_page(DEFAULT_PAGE)
        if self._last_status:
            self._apply_status(self._last_status)
        self._refresh_ready_banner()

        # 首次启动：等主窗口显示后再弹配置向导
        QTimer.singleShot(450, self._maybe_show_onboarding)

    # ------------------------------------------------------------ 构建
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(self.NAV_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(utils.app_pixmap(38))
        icon.setFixedSize(38, 38)
        brand_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_column = QVBoxLayout()
        brand_column.setContentsMargins(0, 0, 0, 0)
        brand_column.setSpacing(0)
        brand_name = QLabel("linknote")
        name_font = QFont("Segoe UI")
        name_font.setPointSizeF(15.5)
        name_font.setWeight(QFont.Weight.Bold)
        name_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        brand_name.setFont(name_font)
        brand_column.addWidget(brand_name)
        brand_sub = QLabel("链记 · 桌面端")
        brand_sub.setObjectName("Help")
        brand_column.addWidget(brand_sub)
        brand_row.addLayout(brand_column, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(16)

        caption = QLabel("导航")
        caption.setObjectName("Help")
        layout.addWidget(caption)
        layout.addSpacing(2)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for index, (key, title, _page_class) in enumerate(PRIMARY_NAV):
            nav_button = NavButton(title)
            nav_button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            self._nav_group.addButton(nav_button, index)
            layout.addWidget(nav_button)
            self._nav_buttons[key] = nav_button

        layout.addStretch(1)

        status_card = QFrame()
        status_card.setObjectName("SidebarCard")
        status_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(8)
        self._side_dot = StatusDot(style.IDLE, 8)
        status_layout.addWidget(self._side_dot, 0, Qt.AlignmentFlag.AlignTop)
        status_column = QVBoxLayout()
        status_column.setContentsMargins(0, 0, 0, 0)
        status_column.setSpacing(1)
        self._side_engine_label = QLabel("引擎：—")
        self._side_engine_label.setStyleSheet(
            f"QLabel {{ color: {style.INK_SOFT}; font-size: 11px; font-weight: bold; }}"
        )
        self._side_agent_label = QLabel("Agent：—")
        self._side_agent_label.setStyleSheet(
            f"QLabel {{ color: {style.MUTED}; font-size: 11px; }}"
        )
        status_column.addWidget(self._side_engine_label)
        status_column.addWidget(self._side_agent_label)
        status_layout.addLayout(status_column, 1)
        layout.addWidget(status_card)
        layout.addSpacing(8)

        version = QLabel(f"v{utils.APP_VERSION} · Windows")
        version.setObjectName("Help")
        layout.addWidget(version)
        return sidebar

    def _build_pages(self) -> None:
        for key, _title, page_class in NAV_ITEMS:
            page = page_class(self.controller)
            page.navigateRequested.connect(self.show_page)
            self._pages[key] = page
            self._stack.addWidget(page)

    def _connect_controller(self) -> None:
        controller = self.controller
        controller.statusChanged.connect(self._on_status)
        controller.pairingChanged.connect(self._on_pairing_changed)
        controller.ffmpegProgress.connect(self._on_ffmpeg_progress)
        controller.notification.connect(self._on_notification)
        controller.logAppended.connect(self._on_log_appended)
        controller.error.connect(self._on_error)
        controller.engineChanged.connect(self._on_engine_changed)
        controller.agentChanged.connect(self._on_agent_changed)
        controller.tasksChanged.connect(self._on_tasks_changed)

    # ------------------------------------------------------------ 对外接口
    def set_tray(self, tray: Any) -> None:
        self._tray = tray

    def page_keys(self) -> list[str]:
        return list(self._pages.keys())

    def primary_page_keys(self) -> list[str]:
        return list(self._nav_buttons.keys())

    def current_page_key(self) -> str:
        page = self._stack.currentWidget()
        for key, candidate in self._pages.items():
            if candidate is page:
                return key
        return DEFAULT_PAGE

    def show_page(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        button = self._nav_buttons.get(key)
        if button is not None:
            if not button.isChecked():
                button.setChecked(True)
        else:
            # 二级页面：取消一级导航的选中态，页面内显示「返回设置」
            checked = self._nav_group.checkedButton()
            if checked is not None:
                self._nav_group.setExclusive(False)
                checked.setChecked(False)
                self._nav_group.setExclusive(True)
        for other_key, other in self._pages.items():
            setter = getattr(other, "set_back_visible", None)
            if callable(setter):
                setter(other_key in ADVANCED_KEYS)
        if self._stack.currentWidget() is not page:
            self._stack.setCurrentWidget(page)
        self._refresh_ready_banner()

    def refresh_ready_banner(self) -> None:
        self._refresh_ready_banner()

    def _on_ready_banner_clicked(self) -> None:
        target = str(getattr(self._ready_banner, "target_key", "") or "")
        if not target:
            return
        self.show_page(target)

    def show_and_activate(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def show_toast(self, text: str, level: str = "info") -> None:
        self._toast.show_message(text, level)

    def quit_application(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        try:
            if self._tray is not None:
                self._tray.hide()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.controller.shutdown()
        except Exception:  # noqa: BLE001
            pass
        QApplication.quit()

    # ------------------------------------------------------------ 信号处理
    def _on_status(self, status: dict) -> None:
        self._last_status = status or {}
        self._apply_status(self._last_status)
        for page in self._pages.values():
            try:
                page.on_status(self._last_status)
            except Exception:  # noqa: BLE001 - 单页异常不应影响全局状态
                pass
        self._refresh_ready_banner()

    def _on_tasks_changed(self, data: dict) -> None:
        self._last_tasks = data or {}
        self._refresh_ready_banner()

    # ------------------------------------------------------------ 就绪横幅
    def _refresh_ready_banner(self) -> None:
        status = self._last_status or {}
        tasks = self._last_tasks or {}
        config = self.controller.config
        agent = status.get("agent") or {}
        engine = status.get("engine") or {}
        ffmpeg = status.get("ffmpeg") or {}
        engine_state = str(engine.get("state") or "stopped")
        paired = bool(agent.get("paired"))

        issues: list[tuple[str, str, str]] = []
        relay_configured = bool(config.relay_configured())
        local_mode = bool(config.telegram_local_mode())
        if not relay_configured and not local_mode:
            issues.append(
                (
                    "未配置 Relay",
                    "settings",
                    "先按 docs/self-hosting.md 用你自己的 Cloudflare 部署 Relay，"
                    "再在设置页填写 https://你的域名；或启用 Telegram 使用本地模式（无手机/PWA）。",
                )
            )
        if local_mode:
            # Telegram 本地模式：不需要配对手机，但 Agent 仍需在后台运行
            if not agent.get("running"):
                issues.append(
                    (
                        "Agent 未运行",
                        "devices",
                        "Telegram 本地模式需要 Agent 在后台运行；进入设备页启动。",
                    )
                )
        elif not paired:
            issues.append(
                ("未配对手机", "devices", "生成配对码并用手机扫码；配对完成后就能接收视频。")
            )
        elif not agent.get("running"):
            issues.append(
                ("Agent 未运行", "devices", "进入设备页启动 Agent，恢复接收手机任务。")
            )
        if engine_state == "error":
            issues.append(
                ("BiliNote 引擎异常", "devices", "引擎启动失败，可重启引擎或查看日志。")
            )
        elif engine_state != "running":
            issues.append(
                ("BiliNote 引擎未运行", "devices", "启动引擎后才能下载与生成笔记。")
            )
        if ffmpeg.get("available") is False:
            issues.append(("ffmpeg 未安装", "engine", "下载 ffmpeg 或手动指定 bin 目录。"))
        if not str(config.provider_id or "").strip() or not str(config.model_name or "").strip():
            issues.append(
                ("未配置 AI 模型", "ai", "选择供应商、填好 API Key 并保存手机默认模型。")
            )

        if issues:
            label, target, detail = issues[0]
            extra = f" 等 {len(issues)} 项" if len(issues) > 1 else ""
            blocking = engine_state == "error" or (
                not local_mode and (not relay_configured or not paired)
            )
            level = "error" if blocking else "warn"
            self._ready_banner.set_state(
                level, f"需要处理：{label}{extra}", detail, "去处理 →", target
            )
            return

        if local_mode:
            current = tasks.get("current")
            if current:
                title = current.get("title") or current.get("url") or "Telegram 任务"
                detail = (
                    "Telegram 本地模式 · 正在处理："
                    f"{utils.clip_text(title, 60)}；手机配对/PWA 需要 Relay。"
                )
            else:
                detail = (
                    "通过 Telegram 机器人收发视频链接；不需要 Relay。"
                    "手机配对 / PWA 需要先部署 Relay。"
                )
            self._ready_banner.set_state(
                "ready", "Telegram 本地模式（无需 Relay）", detail, "查看设置 →", "settings"
            )
            return

        current = tasks.get("current")
        queue = tasks.get("queue") or {}
        queued = int(queue.get("queued") or 0)
        if current:
            title = current.get("title") or current.get("url") or "手机任务"
            progress = int(current.get("progress") or 0)
            detail = f"正在处理：{utils.clip_text(title, 60)}"
            if 0 < progress < 100:
                detail += f" · {progress}%"
            self._ready_banner.set_state(
                "ready", "已准备好接收视频", detail, "查看任务 →", "tasks"
            )
        elif queued:
            self._ready_banner.set_state(
                "ready",
                "已准备好接收视频",
                f"有 {queued} 个任务在队列中，稍后会自动处理。",
                "查看任务 →",
                "tasks",
            )
        else:
            self._ready_banner.set_state(
                "ready",
                "已准备好接收视频",
                "手机发来视频链接后，电脑会自动下载、转写并生成笔记。",
                "",
                "tasks",
            )

    def _apply_status(self, status: dict) -> None:
        overall = utils.overall_state(status)
        engine = status.get("engine") or {}
        agent = status.get("agent") or {}
        engine_state = engine.get("state") or "stopped"
        busy = engine_state == "starting"
        self._side_dot.set_state(utils.state_color(overall), busy=busy)
        engine_text = {
            "running": "运行中",
            "starting": "启动中",
            "error": "异常",
        }.get(engine_state, "已停止")
        self._side_engine_label.setText(f"引擎 · {engine_text}")
        agent_parts = ["在线" if agent.get("running") else "未启动"]
        agent_parts.append("已配对" if agent.get("paired") else "未配对")
        self._side_agent_label.setText("Agent · " + " · ".join(agent_parts))

    def _on_pairing_changed(self, state: dict) -> None:
        for page in self._pages.values():
            try:
                page.on_pairing_changed(state or {})
            except Exception:  # noqa: BLE001
                pass

    def _on_ffmpeg_progress(self, done: int, total: int) -> None:
        for page in self._pages.values():
            try:
                page.on_ffmpeg_progress(done, total)
            except Exception:  # noqa: BLE001
                pass

    def _on_notification(self, title: str, message: str, level: str) -> None:
        text = message
        if title and title != "LinkNote":
            text = f"{title}：{message}"
        self.show_toast(text, level or "info")
        for page in self._pages.values():
            try:
                page.on_notification(title, message, level or "info")
            except Exception:  # noqa: BLE001
                pass

    def _on_log_appended(self, line: str) -> None:
        for page in self._pages.values():
            try:
                page.on_log_appended(line)
            except Exception:  # noqa: BLE001
                pass

    def _on_error(self, message: str) -> None:
        self.show_toast(f"操作失败：{message}", "error")
        for page in self._pages.values():
            try:
                page.on_error(message)
            except Exception:  # noqa: BLE001
                pass

    def _on_engine_changed(self, _data: dict) -> None:
        self._schedule_refresh()

    def _on_agent_changed(self, _data: dict) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(400, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> None:
        self._refresh_pending = False
        try:
            self.controller.refresh_status_async()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 首次配置向导
    def _maybe_show_onboarding(self) -> None:
        if self._onboarding is not None:
            return
        if getattr(self.controller.config, "onboarding_done", True):
            return
        try:
            from .onboarding import OnboardingWizard
        except Exception:  # noqa: BLE001 - 向导异常不应阻塞主界面
            return
        wizard = OnboardingWizard(self.controller, self)
        wizard.finished.connect(self._on_onboarding_finished)
        wizard.setModal(True)
        self._onboarding = wizard
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()

    def _on_onboarding_finished(self, _result: int) -> None:
        wizard = self._onboarding
        self._onboarding = None
        completed = bool(getattr(wizard, "completed_fully", False))
        if wizard is not None:
            wizard.deleteLater()
        self.show_page(DEFAULT_PAGE)
        self.controller.refresh_status_async()
        if completed:
            self.show_toast("配置完成，欢迎使用 LinkNote", "success")
        else:
            self.show_toast(
                "已跳过首次配置，可随时在对应页面继续完成设置", "info"
            )

    def open_onboarding(self) -> None:
        """从设置页等处重新打开向导（已在首次流程中完成也会打开）。"""
        if self._onboarding is not None:
            return
        from .onboarding import OnboardingWizard

        wizard = OnboardingWizard(self.controller, self)
        wizard.finished.connect(self._on_onboarding_finished)
        wizard.setModal(True)
        self._onboarding = wizard
        wizard.show()

    # ------------------------------------------------------------ 窗口事件
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        toast = getattr(self, "_toast", None)
        if toast is not None:
            toast.reposition()

    def _can_hide_to_tray(self) -> bool:
        if not bool(getattr(self.controller.config, "close_to_tray", True)):
            return False
        tray = self._tray
        if tray is None:
            return False
        available = getattr(tray, "available", None)
        if callable(available):
            try:
                return bool(available())
            except Exception:  # noqa: BLE001
                return False
        return True

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return
        if self._can_hide_to_tray():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                tray = self._tray
                notify = getattr(tray, "show_notification", None)
                if callable(notify):
                    try:
                        notify(
                            "LinkNote 仍在后台运行",
                            "手机发来的任务会继续处理；右键托盘图标可以退出。",
                            "info",
                        )
                    except Exception:  # noqa: BLE001
                        pass
            return
        event.accept()
        QTimer.singleShot(0, self.quit_application)



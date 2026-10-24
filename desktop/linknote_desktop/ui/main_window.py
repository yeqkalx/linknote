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
from .pages import NAV_ITEMS
from .widgets import NavButton, StatusDot, Toast

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
        self._nav_buttons: Dict[str, NavButton] = {}

        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = self._build_sidebar()
        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setObjectName("Pages")
        root.addWidget(self._stack, 1)

        self._pages: Dict[str, Any] = {}
        self._build_pages()

        self._toast = Toast(central)
        self._connect_controller()
        self.show_page("status")
        if self._last_status:
            self._apply_status(self._last_status)

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
        for index, (key, title, _page_class) in enumerate(NAV_ITEMS):
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

    # ------------------------------------------------------------ 对外接口
    def set_tray(self, tray: Any) -> None:
        self._tray = tray

    def page_keys(self) -> list[str]:
        return list(self._pages.keys())

    def current_page_key(self) -> str:
        page = self._stack.currentWidget()
        for key, candidate in self._pages.items():
            if candidate is page:
                return key
        return "status"

    def show_page(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        button = self._nav_buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        if self._stack.currentWidget() is not page:
            self._stack.setCurrentWidget(page)

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
        self.show_page("status")
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



"""系统托盘：状态图标、右键菜单、通知气泡与退出逻辑。"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import utils


class TrayIcon(QSystemTrayIcon):
    """QSystemTrayIcon：根据 controller 状态变绿/黄/红，菜单覆盖常用操作。"""

    quit_requested = Signal()

    def __init__(self, controller, window: Any = None, parent: Any = None):
        super().__init__(utils.app_icon(), parent)
        self.controller = controller
        self.window = window
        self.setToolTip("LinkNote 链记")
        self._build_menu()
        self._wire_controller()
        # application.py 只创建 TrayIcon(controller, window)，这里主动注册回窗口，
        # 保证「关闭窗口 → 最小化到托盘」逻辑生效。
        setter = getattr(window, "set_tray", None)
        if callable(setter):
            try:
                setter(self)
            except Exception:  # noqa: BLE001
                pass
        self.update_status(controller.last_status or {})

    # ------------------------------------------------------------ 菜单
    def _build_menu(self) -> None:
        menu = QMenu()
        self._status_action: QAction = menu.addAction("正在启动…")
        self._status_action.setEnabled(False)
        menu.addSeparator()

        self._open_action = menu.addAction("打开主界面")
        self._open_action.triggered.connect(self._open_window)
        self._pair_action = menu.addAction("生成配对码")
        self._pair_action.triggered.connect(self._generate_pairing)
        self._agent_action = menu.addAction("暂停手机任务")
        self._agent_action.triggered.connect(self._toggle_agent)
        self._restart_action = menu.addAction("重启 BiliNote 引擎")
        self._restart_action.triggered.connect(self._restart_engine)
        menu.addSeparator()
        self._logs_action = menu.addAction("打开日志")
        self._logs_action.triggered.connect(self._open_logs)
        menu.addSeparator()
        self._quit_action = menu.addAction("退出 LinkNote")
        self._quit_action.triggered.connect(self._quit)

        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self._on_activated)

    def _wire_controller(self) -> None:
        self.controller.statusChanged.connect(self.update_status)
        self.controller.notification.connect(self._on_notification)

    # ------------------------------------------------------------ 公共
    def available(self) -> bool:
        try:
            return bool(QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:  # noqa: BLE001
            return False

    def show_notification(self, title: str, message: str, level: str = "info") -> None:
        icons = {
            "info": QSystemTrayIcon.MessageIcon.Information,
            "success": QSystemTrayIcon.MessageIcon.Information,
            "warning": QSystemTrayIcon.MessageIcon.Warning,
            "error": QSystemTrayIcon.MessageIcon.Critical,
        }
        try:
            self.showMessage(title, message, icons.get(level, QSystemTrayIcon.MessageIcon.Information), 4500)
        except Exception:  # noqa: BLE001 - 托盘不可用时静默降级
            pass

    def notify_started(self) -> None:
        self.show_notification(
            "LinkNote 已在后台运行",
            "从手机发送链接即可开始做笔记；双击托盘图标可打开主界面。",
            "info",
        )

    def update_status(self, status: Optional[dict]) -> None:
        status = status or {}
        state = utils.overall_state(status)
        try:
            self.setIcon(utils.status_icon(state))
        except Exception:  # noqa: BLE001
            pass
        status_text = utils.tray_status_text(status)
        self._status_action.setText(status_text)
        try:
            self.setToolTip(f"LinkNote 链记 · {status_text}")
        except Exception:  # noqa: BLE001
            pass

        agent = status.get("agent") or {}
        running = bool(agent.get("running"))
        paired = bool(agent.get("paired"))
        self._agent_action.setText("暂停手机任务" if running else "继续手机任务")
        self._agent_action.setEnabled(running or paired)
        self._pair_action.setText("添加手机" if paired else "生成配对码")

    # ------------------------------------------------------------ 菜单动作
    def _open_window(self) -> None:
        window = self.window
        show = getattr(window, "show_and_activate", None)
        if callable(show):
            show()

    def _generate_pairing(self) -> None:
        self._open_window()
        show_page = getattr(self.window, "show_page", None)
        if callable(show_page):
            show_page("devices")
        try:
            self.controller.create_pairing_async()
        except Exception as exc:  # noqa: BLE001
            self.show_notification("生成配对码失败", str(exc), "error")

    def _toggle_agent(self) -> None:
        agent = self.controller.agent
        if getattr(agent, "running", False):
            self.controller.stop_agent()
            self.show_notification("已暂停手机任务", "Agent 已停止，恢复后手机会继续发送任务。", "info")
            return
        if not getattr(self.controller, "agent", None) or not getattr(agent, "is_paired", False):
            self.show_notification("还没有配对手机", "请先在主界面生成配对码并完成配对。", "warning")
            self._open_window()
            show_page = getattr(self.window, "show_page", None)
            if callable(show_page):
                show_page("devices")
            return
        self.controller.start_agent()
        self.show_notification("已恢复手机任务", "Agent 正在运行，手机会继续发送任务。", "success")

    def _restart_engine(self) -> None:
        self.controller.restart_engine_async()
        self.show_notification("正在重启 BiliNote 引擎", "重启期间手机任务会排队等待。", "info")

    def _open_logs(self) -> None:
        self._open_window()
        show_page = getattr(self.window, "show_page", None)
        if callable(show_page):
            show_page("logs")

    def _quit(self) -> None:
        self.quit_requested.emit()
        window = self.window
        quit_app = getattr(window, "quit_application", None)
        if callable(quit_app):
            quit_app()
            return
        try:
            self.controller.shutdown()
        finally:
            QApplication.quit()

    # ------------------------------------------------------------ 事件
    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._open_window()

    def _on_notification(self, title: str, message: str, level: str) -> None:
        self.show_notification(title or "LinkNote", message, level or "info")

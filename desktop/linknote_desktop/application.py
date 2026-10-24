"""QApplication 入口：单实例、托盘、主窗口、Agent worker 模式。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import APP_NAME, APP_NAME_CN, APP_VERSION, DesktopConfig, resource_dir, user_data_dir
from .controller import DesktopController

SERVER_NAME = "LinkNote-Desktop-SingleInstance"


def _app_icon() -> QIcon:
    for name in ("icon.ico", "icon.png"):
        path = resource_dir() / name
        if path.exists():
            return QIcon(str(path))
    return QIcon()


def _install_single_instance(app: QApplication) -> tuple[QLocalServer | None, bool]:
    """返回 (server, is_first_instance)。"""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(400):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return None, False

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    if not server.listen(SERVER_NAME):
        return None, True
    return server, True


def run_agent_worker(argv: list[str]) -> int:
    """PyInstaller windowed exe 的 Agent 子进程模式。"""
    from linknote_agent.cli import main as agent_main

    return int(agent_main(argv))


def run_app(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    minimized = "--minimized" in argv
    argv = [a for a in argv if a != "--minimized"]

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName("linknote")
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(APP_VERSION)
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(_app_icon())

    server, first = _install_single_instance(app)
    if not first:
        return 0

    config = DesktopConfig.load()
    config.ensure_dirs()
    controller = DesktopController(config)
    controller.start()

    # 延迟导入：确保 QApplication 已创建，UI 模块里的 Qt 资源正常
    from .ui.main_window import MainWindow
    from .ui.tray import TrayIcon

    window = MainWindow(controller)
    tray = TrayIcon(controller, window)
    tray.show()
    app._linknote_window = window  # type: ignore[attr-defined]
    app._linknote_tray = tray  # type: ignore[attr-defined]
    app._linknote_controller = controller  # type: ignore[attr-defined]

    if server is not None:
        def _on_new_connection() -> None:
            conn = server.nextPendingConnection()
            if conn is None:
                return
            conn.readyRead.connect(lambda: conn.readAll())
            conn.disconnected.connect(conn.deleteLater)
            window.show_and_activate()

        server.newConnection.connect(_on_new_connection)

    if not minimized or not config.onboarding_done:
        window.show()
    else:
        QTimer.singleShot(300, lambda: tray.notify_started() if hasattr(tray, "notify_started") else None)

    def _shutdown() -> None:
        try:
            controller.shutdown()
        finally:
            app.quit()

    app.aboutToQuit.connect(controller.shutdown)

    def _on_exit() -> None:
        controller.shutdown()

    if hasattr(tray, "quit_requested"):
        tray.quit_requested.connect(_on_exit)

    return int(app.exec())


"""LinkNote Desktop UI 层。

对外入口：
    from linknote_desktop.ui import MainWindow, TrayIcon, OnboardingWizard
应用入口（application.py）通常只需要:

    window = MainWindow(controller)
    tray = TrayIcon(controller, window)
    window.set_tray(tray)
    tray.show()
    window.show()
"""
from __future__ import annotations

from .style import APP_QSS, apply_theme
from .main_window import MainWindow
from .onboarding import OnboardingWizard
from .tray import TrayIcon

__all__ = ["MainWindow", "TrayIcon", "OnboardingWizard", "APP_QSS", "apply_theme"]

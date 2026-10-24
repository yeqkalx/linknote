"""Windows 开机自启（HKCU Run）。"""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LinkNote"
APP_EXE_NAME = "LinkNote.exe"


def _run_value(exe_path: str) -> str:
    return f'"{exe_path}" --minimized'


def current_exe_path() -> str:
    if getattr(sys, "frozen", False):
        return str(sys.executable)
    # 开发环境：不写真实自启，返回一个可读的占位
    return str(sys.executable)


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool, exe_path: str | None = None) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                VALUE_NAME,
                0,
                winreg.REG_SZ,
                _run_value(exe_path or current_exe_path()),
            )
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass

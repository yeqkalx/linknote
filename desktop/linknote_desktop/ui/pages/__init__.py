"""页面集合：一级导航（4 项）与设置里的二级页面（4 项）。

一级导航：笔记库 / 任务 / 设备 / 设置 —— 面向用户的日常界面。
二级页面：AI 模型 / 语音转写 / 引擎与诊断 / 日志 —— 从设置页进入，页面右上角可返回设置。
"""
from __future__ import annotations

from .ai import AiPage
from .devices import DevicesPage
from .engine import EnginePage
from .history import HistoryPage
from .logs import LogsPage
from .settings import SettingsPage
from .tasks import TasksPage
from .transcription import TranscriptionPage

# 一级导航：(key, 导航文案, 页面类)
PRIMARY_NAV = [
    ("history", "笔记库", HistoryPage),
    ("tasks", "任务", TasksPage),
    ("devices", "设备", DevicesPage),
    ("settings", "设置", SettingsPage),
]

# 二级页面：不在侧栏出现，由设置页入口跳转（带「返回设置」）
ADVANCED_NAV = [
    ("ai", "AI 模型", AiPage),
    ("transcription", "语音转写", TranscriptionPage),
    ("engine", "引擎与诊断", EnginePage),
    ("logs", "日志", LogsPage),
]

# 所有页面（主窗口全部实例化，便于状态转发与跳转）
NAV_ITEMS = PRIMARY_NAV + ADVANCED_NAV

PRIMARY_KEYS = [item[0] for item in PRIMARY_NAV]
ADVANCED_KEYS = [item[0] for item in ADVANCED_NAV]
DEFAULT_PAGE = PRIMARY_KEYS[0]
PAGE_CLASSES = [item[2] for item in NAV_ITEMS]

__all__ = [
    "NAV_ITEMS",
    "PRIMARY_NAV",
    "ADVANCED_NAV",
    "PRIMARY_KEYS",
    "ADVANCED_KEYS",
    "DEFAULT_PAGE",
    "PAGE_CLASSES",
    "AiPage",
    "DevicesPage",
    "EnginePage",
    "HistoryPage",
    "LogsPage",
    "SettingsPage",
    "TasksPage",
    "TranscriptionPage",
]

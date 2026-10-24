"""页面集合：导航项与页面类定义。

导航顺序：状态 → 当前任务 → 历史笔记 → AI 模型 → 语音转写 → 引擎与 ffmpeg → 日志 → 设置
（先回答「手机发的任务到哪了」「生成过哪些笔记」，再进入配置类页面。）
"""
from __future__ import annotations

from .ai import AiPage
from .engine import EnginePage
from .history import HistoryPage
from .logs import LogsPage
from .settings import SettingsPage
from .status import StatusPage
from .tasks import TasksPage
from .transcription import TranscriptionPage

# (key, 导航文案, 页面类)
NAV_ITEMS = [
    ("status", "状态", StatusPage),
    ("tasks", "当前任务", TasksPage),
    ("history", "历史笔记", HistoryPage),
    ("ai", "AI 模型", AiPage),
    ("transcription", "语音转写", TranscriptionPage),
    ("engine", "引擎与 ffmpeg", EnginePage),
    ("logs", "日志", LogsPage),
    ("settings", "设置", SettingsPage),
]

PAGE_CLASSES = [item[2] for item in NAV_ITEMS]

__all__ = [
    "NAV_ITEMS",
    "PAGE_CLASSES",
    "AiPage",
    "EnginePage",
    "HistoryPage",
    "LogsPage",
    "SettingsPage",
    "StatusPage",
    "TasksPage",
    "TranscriptionPage",
]

"""页面集合：导航项与页面类定义。"""
from __future__ import annotations

from .ai import AiPage
from .engine import EnginePage
from .logs import LogsPage
from .settings import SettingsPage
from .status import StatusPage
from .transcription import TranscriptionPage

# (key, 导航文案, 页面类)
NAV_ITEMS = [
    ("status", "状态", StatusPage),
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
    "LogsPage",
    "SettingsPage",
    "StatusPage",
    "TranscriptionPage",
]

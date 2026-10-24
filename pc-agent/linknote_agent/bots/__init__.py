"""本机 Bot 入口（v0.4）：Telegram 长轮询 / QQ 官方 WebSocket 网关。

每个用户在自己的 ``agent.json`` 填自己的 bot 凭证；Bot 跑在用户自己的电脑上，
凭证只存本机、不上传 Relay。设计见 ``docs/bot-channels.md``。
"""

from __future__ import annotations

from .base import (
    BotAdapter,
    BotError,
    IncomingMessage,
    MessageCallback,
    ReplyWindowClosed,
)
from .device_relay import BotRelayClient, BotRelayError
from .state import BotState, PendingTask

__all__ = [
    "BotAdapter",
    "BotError",
    "BotRelayClient",
    "BotRelayError",
    "BotState",
    "IncomingMessage",
    "MessageCallback",
    "PendingTask",
    "ReplyWindowClosed",
]

"""Bot 入口的公共类型与适配器协议（v0.5 只保留 Telegram）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


@dataclass(frozen=True)
class IncomingMessage:
    """从 Bot 平台收到的一条用户消息（已做过滤/去重）。"""

    channel: str  # 目前只有 "telegram"
    chat_id: str  # Telegram chat_id
    chat_type: str  # "private" | "group"
    message_id: str  # Telegram message_id
    text: str
    sender_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def chat_key(self) -> str:
        return f"{self.channel}:{self.chat_type}:{self.chat_id}"


class BotError(Exception):
    """Bot 平台返回的可读错误（message 可直接给用户/日志）。"""

    def __init__(
        self,
        message: str,
        *,
        retriable: bool = False,
        retry_after: float = 0.0,
        code: str = "",
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.retriable = retriable
        self.retry_after = retry_after
        self.code = code
        self.status_code = status_code


MessageCallback = Callable[[IncomingMessage], Awaitable[None]]


class BotAdapter:
    """适配器接口（Telegram 实现；测试用 fake 实现）。"""

    name: str = "bot"

    async def start(self, on_message: MessageCallback) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    async def send_text(self, message: IncomingMessage, text: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_note(
        self,
        message: IncomingMessage,
        title: str,
        markdown: str,
        note_path: Optional[Path] = None,
    ) -> None:  # pragma: no cover - 接口
        raise NotImplementedError


__all__ = ["BotAdapter", "BotError", "IncomingMessage", "MessageCallback"]

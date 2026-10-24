"""Bot 入口的公共类型与适配器协议（Telegram / QQ 共用）。

设计见 ``docs/bot-channels.md``：Bot 只是本机 Agent 旁边的两个入口，不新增云端服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


@dataclass(frozen=True)
class IncomingMessage:
    """从 Bot 平台收到的一条用户消息（已做过滤/去重）。"""

    channel: str  # "telegram" | "qq"
    chat_id: str  # Telegram chat_id / QQ user_openid / QQ group_openid
    chat_type: str  # "private" | "group"
    message_id: str  # Telegram message_id / QQ 事件 id（被动回复的 msg_id）
    text: str
    reply_msg_id: str = ""  # QQ 被动回复用：= 事件 id（Telegram 留空）
    sender_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def chat_key(self) -> str:
        return f"{self.channel}:{self.chat_type}:{self.chat_id}"

    def reply(self, text: str) -> "IncomingMessage":
        """构造同一聊天的回复上下文（用于从 pending 状态恢复被动回复）。"""
        return IncomingMessage(
            channel=self.channel,
            chat_id=self.chat_id,
            chat_type=self.chat_type,
            message_id=self.message_id,
            text=text,
            reply_msg_id=self.reply_msg_id or self.message_id,
            sender_id=self.sender_id,
        )


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


class ReplyWindowClosed(BotError):
    """QQ 被动回复窗口已关闭/无权限：不应视作崩溃，转 pending 等 `/last` 或下条消息补发。"""

    def __init__(self, message: str = "QQ 被动回复窗口已关闭，稍后可用 /last 重新获取"):
        super().__init__(message, retriable=False)


MessageCallback = Callable[[IncomingMessage], Awaitable[None]]


class BotAdapter:
    """适配器接口（Telegram / QQ 实现；测试用 fake 实现）。"""

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


__all__ = [
    "BotAdapter",
    "BotError",
    "IncomingMessage",
    "MessageCallback",
    "ReplyWindowClosed",
]

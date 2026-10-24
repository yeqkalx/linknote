"""Bot 本地状态：``~/.linknote/bot-state.json``（原子写）。

保存：Telegram getUpdates offset、QQ session/seq、待投递/进行中任务（pending）、最近笔记、
本地限流窗口。进程重启后恢复轮询/补投递（docs/bot-channels.md §5）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .._jsonio import atomic_write_json, read_json
from ..config import default_home
from ..logging_setup import get_logger

logger = get_logger("bots.state")

STATE_VERSION = 1
RATE_LIMIT_TASKS = 10
RATE_LIMIT_WINDOW_SECONDS = 3600.0

STATUS_SUBMITTED = "submitted"
STATUS_WINDOW_CLOSED = "window_closed"
STATUS_DELIVERED = "delivered"

VALID_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_WINDOW_CLOSED, STATUS_DELIVERED})


def default_state_path() -> Path:
    return default_home() / "bot-state.json"


@dataclass
class PendingTask:
    task_id: str
    channel: str
    chat_id: str
    chat_type: str = "private"
    reply_msg_id: str = ""
    msg_seq: int = 0
    created_at: float = field(default_factory=time.time)
    status: str = STATUS_SUBMITTED
    title: str = ""

    @property
    def chat_key(self) -> str:
        return f"{self.channel}:{self.chat_type}:{self.chat_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "reply_msg_id": self.reply_msg_id,
            "msg_seq": self.msg_seq,
            "created_at": self.created_at,
            "status": self.status,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingTask":
        return cls(
            task_id=str(data.get("task_id") or ""),
            channel=str(data.get("channel") or ""),
            chat_id=str(data.get("chat_id") or ""),
            chat_type=str(data.get("chat_type") or "private"),
            reply_msg_id=str(data.get("reply_msg_id") or ""),
            msg_seq=int(data.get("msg_seq") or 0),
            created_at=float(data.get("created_at") or time.time()),
            status=str(data.get("status") or STATUS_SUBMITTED),
            title=str(data.get("title") or ""),
        )


class BotState:
    """``bot-state.json`` 的读写与辅助方法。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_state_path()
        self.telegram_offset = 0
        self.telegram_username = ""
        self.qq_session_id = ""
        self.qq_event_seq = 0
        self.qq_seq: dict[str, int] = {}
        self.pending: list[PendingTask] = []
        self.last_note: dict[str, str] = {}
        self.task_times: dict[str, list[float]] = {}

    # ---------------------------------------------------------------- 读写

    @classmethod
    def load(cls, path: Path | str | None = None) -> "BotState":
        state = cls(path)
        data = read_json(state.path, default=None)
        if not isinstance(data, dict):
            return state
        state.telegram_offset = int(data.get("telegram_offset") or 0)
        state.telegram_username = str(data.get("telegram_username") or "")
        state.qq_session_id = str(data.get("qq_session_id") or "")
        state.qq_event_seq = int(data.get("qq_event_seq") or 0)
        seq = data.get("qq_seq")
        if isinstance(seq, dict):
            state.qq_seq = {str(k): int(v) for k, v in seq.items() if str(k)}
        pending = data.get("pending")
        if isinstance(pending, list):
            for item in pending:
                if isinstance(item, dict) and item.get("task_id"):
                    pending_task = PendingTask.from_dict(item)
                    if pending_task.status in VALID_STATUSES:
                        state.pending.append(pending_task)
        last_note = data.get("last_note")
        if isinstance(last_note, dict):
            state.last_note = {str(k): str(v) for k, v in last_note.items()}
        times = data.get("task_times")
        if isinstance(times, dict):
            state.task_times = {
                str(k): [float(t) for t in v] for k, v in times.items() if isinstance(v, list)
            }
        state.prune_rate_limits()
        return state

    def save(self) -> Path:
        payload = {
            "version": STATE_VERSION,
            "telegram_offset": self.telegram_offset,
            "telegram_username": self.telegram_username,
            "qq_session_id": self.qq_session_id,
            "qq_event_seq": self.qq_event_seq,
            "qq_seq": self.qq_seq,
            "pending": [p.to_dict() for p in self.pending],
            "last_note": self.last_note,
            "task_times": self.task_times,
            "saved_at": time.time(),
        }
        return atomic_write_json(self.path, payload)

    # ---------------------------------------------------------------- 轮询 offset

    def update_offset(self, update_id: int) -> None:
        if update_id + 1 > self.telegram_offset:
            self.telegram_offset = update_id + 1
            self.save()

    # ---------------------------------------------------------------- QQ seq

    def next_qq_seq(self, chat_key: str) -> int:
        seq = self.qq_seq.get(chat_key, 0) + 1
        self.qq_seq[chat_key] = seq
        self.save()
        return seq

    def set_qq_session(self, session_id: str) -> None:
        self.qq_session_id = session_id or ""
        self.save()

    # ---------------------------------------------------------------- pending

    def add_pending(self, pending: PendingTask) -> None:
        self.pending = [p for p in self.pending if p.task_id != pending.task_id]
        self.pending.append(pending)
        self.save()

    def update_pending(self, task_id: str, **changes: Any) -> None:
        for pending in self.pending:
            if pending.task_id == task_id:
                for key, value in changes.items():
                    if hasattr(pending, key):
                        setattr(pending, key, value)
                self.save()
                return

    def remove_pending(self, task_id: str) -> None:
        before = len(self.pending)
        self.pending = [p for p in self.pending if p.task_id != task_id]
        if len(self.pending) != before:
            self.save()

    def pending_for_chat(self, chat_key: str, status: str | None = None) -> list[PendingTask]:
        return [
            p
            for p in self.pending
            if p.chat_key == chat_key and (status is None or p.status == status)
        ]

    def pendings(self, status: str | None = None) -> list[PendingTask]:
        return [p for p in self.pending if status is None or p.status == status]

    def find(self, task_id: str) -> Optional[PendingTask]:
        return next((p for p in self.pending if p.task_id == task_id), None)

    # ---------------------------------------------------------------- 最近笔记

    def set_last_note(self, chat_key: str, task_id: str) -> None:
        self.last_note[chat_key] = task_id
        self.save()

    def last_note_for(self, chat_key: str) -> str:
        return self.last_note.get(chat_key, "")

    # ---------------------------------------------------------------- 限流

    def prune_rate_limits(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        for key, times in list(self.task_times.items()):
            kept = [t for t in times if now - t < RATE_LIMIT_WINDOW_SECONDS]
            if kept:
                self.task_times[key] = kept
            else:
                self.task_times.pop(key, None)

    def allow_task(
        self,
        chat_key: str,
        *,
        now: Optional[float] = None,
        limit: int = RATE_LIMIT_TASKS,
        window: float = RATE_LIMIT_WINDOW_SECONDS,
    ) -> bool:
        """本地限流：同一聊天每小时最多 ``limit`` 个任务。返回是否允许并记录。"""
        now = time.time() if now is None else now
        times = [t for t in self.task_times.get(chat_key, []) if now - t < window]
        if len(times) >= limit:
            self.task_times[chat_key] = times
            self.save()
            return False
        times.append(now)
        self.task_times[chat_key] = times
        self.save()
        return True


__all__ = [
    "BotState",
    "PendingTask",
    "RATE_LIMIT_TASKS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "STATUS_DELIVERED",
    "STATUS_SUBMITTED",
    "STATUS_WINDOW_CLOSED",
    "default_state_path",
]

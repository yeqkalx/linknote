"""Bot 本地状态：``~/.linknote/bot-state.json``（原子写）。

保存：Telegram getUpdates offset、待投递/进行中任务（pending）、最近笔记、本地限流窗口。
进程重启后恢复轮询/补投递（docs/bot-channels.md §5）。
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

STATE_VERSION = 3
RATE_LIMIT_TASKS = 10
RATE_LIMIT_WINDOW_SECONDS = 3600.0

STATUS_SUBMITTED = "submitted"
STATUS_DELIVERED = "delivered"

VALID_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_DELIVERED})

#: v0.5+ Telegram 无 Relay 本地模式的任务状态
LOCAL_QUEUED = "queued"
LOCAL_RUNNING = "running"
LOCAL_SUCCEEDED = "succeeded"
LOCAL_FAILED = "failed"

LOCAL_VALID_STATUSES = frozenset({LOCAL_QUEUED, LOCAL_RUNNING, LOCAL_SUCCEEDED, LOCAL_FAILED})
LOCAL_RESTART_ERROR = "Agent 重启，任务未完成；请重发链接或 /retry <task_id>"


def default_state_path() -> Path:
    return default_home() / "bot-state.json"


@dataclass
class PendingTask:
    task_id: str
    channel: str
    chat_id: str
    chat_type: str = "private"
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
            created_at=float(data.get("created_at") or time.time()),
            status=str(data.get("status") or STATUS_SUBMITTED),
            title=str(data.get("title") or ""),
        )


@dataclass
class LocalTask:
    """Telegram 无 Relay 本地模式的任务记录（不涉及 Relay 任务 id）。"""

    task_id: str
    chat_key: str = ""
    chat_id: str = ""
    chat_type: str = "private"
    url: str = ""
    status: str = LOCAL_QUEUED
    title: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "chat_key": self.chat_key,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "url": self.url,
            "status": self.status,
            "title": self.title,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LocalTask":
        return cls(
            task_id=str(data.get("task_id") or ""),
            chat_key=str(data.get("chat_key") or ""),
            chat_id=str(data.get("chat_id") or ""),
            chat_type=str(data.get("chat_type") or "private"),
            url=str(data.get("url") or ""),
            status=str(data.get("status") or LOCAL_QUEUED),
            title=str(data.get("title") or ""),
            error=str(data.get("error") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class BotState:
    """``bot-state.json`` 的读写与辅助方法。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_state_path()
        self.telegram_offset = 0
        self.telegram_username = ""
        self.pending: list[PendingTask] = []
        self.local_tasks: list[LocalTask] = []
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
        local_tasks = data.get("local_tasks")
        restart_failed = False
        if isinstance(local_tasks, list):
            for item in local_tasks:
                if isinstance(item, dict) and item.get("task_id"):
                    local_task = LocalTask.from_dict(item)
                    if local_task.status in LOCAL_VALID_STATUSES:
                        state.local_tasks.append(local_task)
        # 简单重启处理：上次未完成的本地任务标记失败，/status 可见，/retry 可重跑
        for local_task in state.local_tasks:
            if local_task.status in (LOCAL_QUEUED, LOCAL_RUNNING):
                local_task.status = LOCAL_FAILED
                local_task.error = LOCAL_RESTART_ERROR
                local_task.updated_at = time.time()
                restart_failed = True
        times = data.get("task_times")
        if isinstance(times, dict):
            state.task_times = {
                str(k): [float(t) for t in v] for k, v in times.items() if isinstance(v, list)
            }
        state.prune_rate_limits()
        if restart_failed:
            state.save()
        return state

    def save(self) -> Path:
        payload = {
            "version": STATE_VERSION,
            "telegram_offset": self.telegram_offset,
            "telegram_username": self.telegram_username,
            "pending": [p.to_dict() for p in self.pending],
            "local_tasks": [t.to_dict() for t in self.local_tasks],
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

    # ---------------------------------------------------------------- 本地任务（无 Relay）

    def add_local_task(self, task: LocalTask) -> None:
        self.local_tasks = [t for t in self.local_tasks if t.task_id != task.task_id]
        self.local_tasks.append(task)
        # 只保留最近 50 条，避免 bot-state 无限增长
        self.local_tasks = self.local_tasks[-50:]
        self.save()

    def update_local_task(self, task_id: str, **changes: Any) -> None:
        for task in self.local_tasks:
            if task.task_id == task_id:
                for key, value in changes.items():
                    if hasattr(task, key):
                        setattr(task, key, value)
                task.updated_at = time.time()
                self.save()
                return

    def find_local_task(self, task_id_or_prefix: str) -> Optional[LocalTask]:
        needle = str(task_id_or_prefix or "").strip()
        if not needle:
            return None
        for task in reversed(self.local_tasks):
            if task.task_id == needle:
                return task
        for task in reversed(self.local_tasks):
            if task.task_id.startswith(needle):
                return task
        return None

    def local_tasks_recent(self, limit: int = 5) -> list[LocalTask]:
        return list(reversed(self.local_tasks[-limit:]))

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
    "LOCAL_FAILED",
    "LOCAL_QUEUED",
    "LOCAL_RESTART_ERROR",
    "LOCAL_RUNNING",
    "LOCAL_SUCCEEDED",
    "LocalTask",
    "PendingTask",
    "RATE_LIMIT_TASKS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "STATUS_DELIVERED",
    "STATUS_SUBMITTED",
    "default_state_path",
]

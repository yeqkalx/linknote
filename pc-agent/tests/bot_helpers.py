"""Bot 测试公共设施：fake 适配器 / fake Relay / fake 助手。

测试不触网：Telegram 用 httpx.MockTransport；Runner 用 fake BotRelayClient/适配器。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from linknote_agent.bots.base import BotAdapter, IncomingMessage
from linknote_agent.bots.device_relay import BotRelayError
from linknote_agent.bots.runner import BotRunner
from linknote_agent.bots.state import BotState
from linknote_agent.config import AgentConfig
from linknote_agent.outbox import Outbox


def make_message(
    *,
    channel: str = "telegram",
    chat_id: str = "chat-1",
    chat_type: str = "private",
    text: str = "https://www.bilibili.com/video/BV1xx411c7mD",
    message_id: str = "msg-1",
) -> IncomingMessage:
    return IncomingMessage(
        channel=channel,
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=message_id,
        text=text,
    )


async def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("等待条件超时")


class FakeAdapter(BotAdapter):
    def __init__(self, name: str = "telegram") -> None:
        self.name = name
        self.started = False
        self.stopped = False
        self.callback = None
        self.sent_text: list[dict[str, Any]] = []
        self.sent_notes: list[dict[str, Any]] = []
        self.fail_send = False

    async def start(self, on_message) -> None:
        self.started = True
        self.callback = on_message

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(self, message: IncomingMessage, text: str) -> None:
        if self.fail_send:
            raise BotError("fake send failure")
        self.sent_text.append({"message": message, "text": text})

    async def send_note(self, message, title, markdown, note_path=None) -> None:
        if self.fail_send:
            raise BotError("fake send failure")
        self.sent_notes.append(
            {"message": message, "title": title, "markdown": markdown, "path": note_path}
        )

    async def emit(self, message: IncomingMessage) -> None:
        assert self.callback is not None, "adapter 尚未 start"
        await self.callback(message)


class FakeBotRelay:
    """替身 BotRelayClient（device token 视角）。"""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        self.list_items: list[dict[str, Any]] = []
        self.retried: list[str] = []
        self.calls: list[str] = []

    def add_task(self, task_id: str = "task-0001", **fields: Any) -> dict[str, Any]:
        task = {"id": task_id, "status": "queued", "title": "", **fields}
        self.tasks[task_id] = task
        return task

    def set_status(self, task_id: str, status: str, **fields: Any) -> None:
        self.tasks.setdefault(task_id, {"id": task_id})
        self.tasks[task_id].update({"status": status, **fields})

    async def create_task(self, url: str, client_key: str | None = None) -> dict[str, Any]:
        self.calls.append("create_task")
        task = self.add_task(
            f"task-{len(self.created) + 1:04d}", url=url, status="queued", client_key=client_key
        )
        self.created.append({"url": url, "client_key": client_key, "task_id": task["id"]})
        return task

    async def get_task(self, task_id: str) -> dict[str, Any]:
        self.calls.append("get_task")
        task = self.tasks.get(task_id)
        if task is None:
            raise BotRelayError("任务不存在", status_code=404, code="task_not_found")
        return dict(task)

    async def list_tasks(self, limit: int = 5) -> list[dict[str, Any]]:
        return self.list_items[:limit]

    async def retry_task(self, task_id: str) -> dict[str, Any]:
        self.retried.append(task_id)
        new_id = f"task-retry-{len(self.retried):04d}"
        task = self.add_task(new_id, status="queued")
        return dict(task)

    async def aclose(self) -> None:
        return None


class FakeAgentRelay:
    """替身 Agent 认证 RelayClient：只实现 Bot 需要的 create_agent_device。"""

    def __init__(self) -> None:
        self.device_calls: list[dict[str, str]] = []
        self._n = 0
        self.fail: Optional[Exception] = None

    async def create_agent_device(self, name: str, platform: str) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        self._n += 1
        token = f"dev_bot_{self._n:04d}"
        self.device_calls.append({"name": name, "platform": platform, "token": token})
        return {"device": {"id": f"device-{self._n}"}, "token": token}


class BotTestEnv:
    def __init__(self, tmp_path: Path, *, adapters: dict[str, BotAdapter]):
        self.tmp_path = tmp_path
        self.cfg = AgentConfig()
        self.cfg.relay_base_url = "http://relay.test"
        self.cfg.agent_token = "agt_test_token"
        self.cfg.bot_task_poll_seconds = 0.01
        self.cfg.bot_task_timeout_seconds = 5.0
        self.cfg.bot_telegram_preview_chars = 120
        self.outbox = Outbox(tmp_path / "outbox", notes_dir=tmp_path / "notes")
        self.state = BotState(tmp_path / "bot-state.json")
        self.agent_relay = FakeAgentRelay()
        self.bot_relay = FakeBotRelay()
        self.adapters = adapters
        self.runner = BotRunner(
            self.cfg,
            self.agent_relay,  # type: ignore[arg-type]
            outbox=self.outbox,
            state=self.state,
            adapters=adapters,
            relay_factory=lambda token: self.bot_relay,
        )

    def new_state(self) -> BotState:
        return BotState.load(self.tmp_path / "bot-state.json")

    def seed_note(self, task_id: str, title: str = "测试标题", markdown: str = "# 笔记\n正文") -> Path:
        self.outbox.add_note(task_id, title, markdown)
        return self.outbox.note_path(task_id)


def build_env(tmp_path: Path) -> BotTestEnv:
    return BotTestEnv(tmp_path, adapters={"telegram": FakeAdapter("telegram")})


class FakeTelegram:
    """httpx.MockTransport 用的假 Telegram Bot API。"""

    def __init__(self) -> None:
        self.username = "my_test_bot"
        self.updates: list[list[dict[str, Any]]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.sent_documents: list[dict[str, Any]] = []
        self.me_error: Optional[tuple[int, dict[str, Any]]] = None
        self.poll_errors: list[tuple[int, dict[str, Any]]] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            if self.me_error is not None:
                status, payload = self.me_error
                return httpx.Response(status, json=payload)
            return httpx.Response(200, json={"ok": True, "result": {"username": self.username}})
        if method == "getUpdates":
            if self.poll_errors:
                status, payload = self.poll_errors.pop(0)
                return httpx.Response(status, json=payload)
            updates = self.updates.pop(0) if self.updates else []
            return httpx.Response(200, json={"ok": True, "result": updates})
        if method == "sendMessage":
            self.sent_messages.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if method == "sendDocument":
            self.sent_documents.append({"content": request.content, "headers": dict(request.headers)})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})
        return httpx.Response(404, json={"ok": False, "description": "unknown method"})


def telegram_update(
    *,
    update_id: int,
    text: str,
    chat_id: int = 100,
    chat_type: str = "private",
    message_id: int = 1,
    is_bot: bool = False,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": 7, "is_bot": is_bot},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }

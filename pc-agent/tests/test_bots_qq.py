"""QQ 官方 Bot WebSocket 网关适配器：token 缓存/Identify/心跳/事件/被动回复/窗口关闭。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bot_helpers import wait_until
from linknote_agent.bots.base import ReplyWindowClosed
from linknote_agent.bots.qq import GROUP_AND_C2C_INTENTS, QQBot
from linknote_agent.bots.state import BotState

APP_ID = "102000001"
APP_SECRET = "test-app-secret"


class FakeQQHttp:
    def __init__(self) -> None:
        self.token_calls = 0
        self.send_calls: list[dict] = []
        self.send_response: tuple[int, dict] = (200, {})
        self.gateway_calls = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/app/getAppAccessToken":
            self.token_calls += 1
            return httpx.Response(
                200, json={"access_token": f"at-{self.token_calls}", "expires_in": 7200}
            )
        if path == "/gateway":
            self.gateway_calls += 1
            return httpx.Response(200, json={"url": "wss://fake-gateway.example/ws"})
        if path.startswith("/v2/"):
            body = json.loads(request.content.decode("utf-8"))
            self.send_calls.append({"path": path, "body": body})
            status, payload = self.send_response
            return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"message": "not found"})


class FakeWS:
    def __init__(self, frames: list[dict]) -> None:
        self.inbox: asyncio.Queue = asyncio.Queue()
        for frame in frames:
            self.inbox.put_nowait(json.dumps(frame))
        self.sent: list[dict] = []
        self.closed = False

    async def recv(self):
        item = await self.inbox.get()
        if item is _CLOSED:
            raise ConnectionError("closed")
        return item

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True
        self.inbox.put_nowait(_CLOSED)


_CLOSED = object()


async def _start_bot(tmp_path, fake_http: FakeQQHttp, frames: list[dict]):
    state = BotState(tmp_path / "bot-state.json")
    ws = FakeWS(frames)

    async def fake_connect(url: str):
        return ws

    bot = QQBot(
        APP_ID,
        APP_SECRET,
        state=state,
        transport=fake_http.transport,
        connect=fake_connect,
        chunk_chars=20,
    )
    received = []

    async def on_message(message):
        received.append(message)

    await bot.start(on_message)
    return bot, state, ws, received


def _hello(heartbeat_ms: int = 30_000) -> dict:
    return {"op": 10, "d": {"heartbeat_interval": heartbeat_ms}}


def _ready() -> dict:
    return {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "session-123"}}


def _c2c_event(event_id: str = "event-1", content: str = "https://www.bilibili.com/video/BV1xx") -> dict:
    return {
        "op": 0,
        "s": 2,
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "id": event_id,
            "content": content,
            "author": {"user_openid": "user-openid-1"},
        },
    }


def _group_event(jump_url: str = "https://www.bilibili.com/video/BV1yy") -> dict:
    return {
        "op": 0,
        "s": 3,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": "group-event-1",
            "content": "",
            "group_openid": "group-openid-1",
            "author": {"member_openid": "member-1"},
            "ark_data": {"fields": {"jump_url": jump_url}},
        },
    }


async def test_access_token_cached(tmp_path):
    fake = FakeQQHttp()
    state = BotState(tmp_path / "bot-state.json")
    bot = QQBot(APP_ID, APP_SECRET, state=state, transport=fake.transport)
    try:
        assert await bot.access_token() == "at-1"
        assert await bot.access_token() == "at-1"
        assert fake.token_calls == 1
    finally:
        await bot.stop()


async def test_identify_ready_and_c2c_event(tmp_path):
    fake = FakeQQHttp()
    bot, state, ws, received = await _start_bot(
        tmp_path, fake, [_hello(60_000), _ready(), _c2c_event()]
    )
    try:
        await wait_until(lambda: len(received) == 1)
        identify = next(frame for frame in ws.sent if frame.get("op") == 2)
        data = identify["d"]
        assert data["token"] == "QQBot at-1"
        assert data["intents"] == GROUP_AND_C2C_INTENTS
        assert data["shard"] == [0, 1]
        await wait_until(lambda: state.qq_session_id == "session-123")
        assert received[0].channel == "qq"
        assert received[0].chat_type == "private"
        assert received[0].chat_id == "user-openid-1"
        assert received[0].reply_msg_id == "event-1"
        assert "bilibili.com" in received[0].text
    finally:
        await bot.stop()


async def test_heartbeat_op1_sent(tmp_path):
    fake = FakeQQHttp()
    bot, _, ws, _ = await _start_bot(tmp_path, fake, [_hello(60), _ready()])
    try:
        await wait_until(lambda: any(frame.get("op") == 1 for frame in ws.sent), timeout=3)
    finally:
        await bot.stop()


async def test_group_event_ark_jump_url(tmp_path):
    fake = FakeQQHttp()
    bot, _, _, received = await _start_bot(
        tmp_path, fake, [_hello(), _ready(), _group_event()]
    )
    try:
        await wait_until(lambda: len(received) == 1)
        message = received[0]
        assert message.chat_type == "group"
        assert message.chat_id == "group-openid-1"
        assert message.text == "https://www.bilibili.com/video/BV1yy"
    finally:
        await bot.stop()


async def test_duplicate_event_id_ignored(tmp_path):
    fake = FakeQQHttp()
    bot, _, _, received = await _start_bot(
        tmp_path, fake, [_hello(), _ready(), _c2c_event(), _c2c_event()]
    )
    try:
        await wait_until(lambda: len(received) >= 1)
        await asyncio.sleep(0.1)
        assert len(received) == 1
    finally:
        await bot.stop()


async def test_passive_reply_msg_id_and_seq(tmp_path):
    fake = FakeQQHttp()
    state = BotState(tmp_path / "bot-state.json")
    bot = QQBot(APP_ID, APP_SECRET, state=state, transport=fake.transport, chunk_chars=800)
    from bot_helpers import make_message

    message = make_message(
        channel="qq",
        chat_id="user-openid-1",
        chat_type="private",
        message_id="event-1",
        reply_msg_id="event-1",
        text="",
    )
    try:
        await bot.send_note(message, "标题", "正文")
        await bot.send_note(message, "标题", "正文2")
        assert [call["body"]["msg_seq"] for call in fake.send_calls] == [1, 2]
        assert all(call["body"]["msg_id"] == "event-1" for call in fake.send_calls)
        assert fake.send_calls[0]["path"] == "/v2/users/user-openid-1/messages"
    finally:
        await bot.stop()


async def test_long_note_capped_to_passive_limit(tmp_path):
    fake = FakeQQHttp()
    state = BotState(tmp_path / "bot-state.json")
    bot = QQBot(APP_ID, APP_SECRET, state=state, transport=fake.transport, chunk_chars=10)
    from bot_helpers import make_message

    message = make_message(channel="qq", chat_id="u1", message_id="e1", reply_msg_id="e1")
    try:
        await bot.send_note(message, "", "0123456789" * 20)  # 200 字符 → 10 片，C2C 最多 4
        assert len(fake.send_calls) == 4
        assert "完整内容在电脑/手机" in fake.send_calls[-1]["body"]["content"]
    finally:
        await bot.stop()


async def test_window_closed_raises_reply_window_closed(tmp_path):
    fake = FakeQQHttp()
    fake.send_response = (400, {"code": "40054005", "message": "msg_id 已过期"})
    state = BotState(tmp_path / "bot-state.json")
    bot = QQBot(APP_ID, APP_SECRET, state=state, transport=fake.transport)
    from bot_helpers import make_message

    message = make_message(channel="qq", chat_id="u1", message_id="e1", reply_msg_id="e1")
    try:
        with pytest.raises(ReplyWindowClosed):
            await bot.send_note(message, "", "正文")
    finally:
        await bot.stop()

"""Realtime protocol 单测：WS URL/退避/真实本地握手/消息分发/重连/心跳（v0.3 协议 ping）。

不依赖真实 Relay：本地用 `websockets.asyncio.server` 起一个假 Relay 验证握手与 header，
其余用注入的 fake connection / fake 时钟验证重连逻辑。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from linknote_agent.relay_ws import (
    EVENT_AUTH_ERROR,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_RECONNECT_SCHEDULED,
    EVENT_SERVER_PING,
    EVENT_SERVER_PROBE,
    RealtimeClient,
    WSError,
    build_ws_url,
    classify_connect_error,
    compute_backoff,
)

# ---------------------------------------------------------------- fake 基础设施


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        # 必须让出事件循环：否则 重连循环在 fake 时钟下会变成同步死循环，测试任务拿不到执行机会。
        await asyncio.sleep(0)

    def random(self) -> float:
        return 0.0  # 固定 jitter=0，退避时间确定


class FakeConnection:
    """模拟 websockets ClientConnection：recv 从队列取数据，close 后抛异常。"""

    def __init__(self, hello: dict[str, Any] | None = None) -> None:
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[str] = []
        self.closed = False
        if hello is not None:
            self.inbox.put_nowait(json.dumps(hello))

    async def recv(self) -> Any:
        if self.closed:
            raise ConnectionError("connection closed")
        item = await self.inbox.get()
        if item is _CLOSED:
            raise ConnectionError("connection closed")
        if isinstance(item, BaseException):
            raise item
        return item

    async def send(self, data: str) -> None:
        if self.closed:
            raise ConnectionError("connection closed")
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True
        try:
            self.inbox.put_nowait(_CLOSED)
        except Exception:  # pragma: no cover
            pass

    # 测试辅助
    def push(self, payload: Any) -> None:
        self.inbox.put_nowait(json.dumps(payload) if isinstance(payload, dict) else payload)


_CLOSED = object()


async def wait_for_event(
    client: RealtimeClient, msg_type: str, *, timeout: float = 3.0
) -> dict[str, Any]:
    """从 client.events 里等一条指定 type 的事件（跳过其它）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError(f"等待事件 {msg_type} 超时")
        message = await asyncio.wait_for(client.events.get(), timeout=remaining)
        if message.get("type") == msg_type:
            return message


async def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("等待条件超时")


# ---------------------------------------------------------------- URL / 退避


def test_build_ws_url():
    assert (
        build_ws_url("http://127.0.0.1:8787")
        == "ws://127.0.0.1:8787/api/v1/agent/ws"
    )
    assert (
        build_ws_url("https://relay.example.com/")
        == "wss://relay.example.com/api/v1/agent/ws"
    )
    assert (
        build_ws_url("https://relay.example.com", ws_url="ws://127.0.0.1:8798/api/v1/agent/ws")
        == "ws://127.0.0.1:8798/api/v1/agent/ws"
    )
    assert (
        build_ws_url("http://x", ws_url="http://127.0.0.1:8798/api/v1/agent/ws")
        == "ws://127.0.0.1:8798/api/v1/agent/ws"
    )
    with pytest.raises(WSError):
        build_ws_url("ftp://relay")
    with pytest.raises(WSError):
        build_ws_url("http://x", ws_url="ftp://x/ws")


def test_compute_backoff_schedule_and_jitter_cap():
    delays = [1.0, 2.0, 4.0, 8.0, 15.0, 30.0]
    no_jitter = [compute_backoff(i, delays, jitter_ratio=0.0, random_fn=lambda: 0.5) for i in range(7)]
    assert no_jitter == [1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 30.0]

    with_jitter = [
        compute_backoff(i, delays, jitter_ratio=0.2, random_fn=lambda: 0.5)
        for i in range(6)
    ]
    assert with_jitter[0] == pytest.approx(1.1)
    assert with_jitter[1] == pytest.approx(2.2)
    # 30s 封顶（不会因为 jitter 超过 30）
    assert with_jitter[-1] == 30.0
    assert all(d <= 30.0 for d in with_jitter)


def test_classify_connect_error_auth():
    class FakeResponse:
        status_code = 401

    class InvalidStatus(Exception):
        def __init__(self) -> None:
            self.response = FakeResponse()

    error = classify_connect_error(InvalidStatus())
    assert error.kind == "auth"
    assert error.status_code == 401
    assert "pair" in error.message

    network = classify_connect_error(ConnectionRefusedError("refused"))
    assert network.kind == "network"


def test_classify_close_codes():
    """契约 §11.1 服务端关闭码：4001 被顶掉 / 4002 心跳超时 / 其它通用断线。"""

    class _Close:
        def __init__(self, code: int, reason: str = "") -> None:
            self.code = code
            self.reason = reason

    class ConnectionClosedLike(Exception):
        def __init__(self, code: int, reason: str = "") -> None:
            self.rcvd = _Close(code, reason)
            self.sent = _Close(1000)
            self.code = code

    superseded = classify_connect_error(ConnectionClosedLike(4001, "replaced by newer agent"))
    assert superseded.kind == "superseded"
    assert "close 4001" in superseded.message
    assert "另一个 Agent 实例" in superseded.message

    heartbeat = classify_connect_error(ConnectionClosedLike(4002))
    assert heartbeat.kind == "network"
    assert "4002" in heartbeat.message

    generic = classify_connect_error(ConnectionClosedLike(1011, "internal error"))
    assert generic.kind == "network"
    assert "1011" in generic.message


async def test_superseded_close_event_and_reconnect():
    """服务端用 close 4001 顶掉旧 Agent 连接时：产生 _disconnected 事件、日志报重复实例、继续重连。"""
    hello = {"type": "hello", "agent_id": "a1", "server_time": "t"}
    conn = FakeConnection(hello=hello)
    conn2 = FakeConnection(hello=hello)
    connects = {"n": 0}

    async def fake_connect(url: str, headers: dict[str, str]):
        connects["n"] += 1
        return conn if connects["n"] == 1 else conn2

    class _Close:
        def __init__(self, code: int, reason: str = "") -> None:
            self.code = code
            self.reason = reason

    class Superseded(Exception):
        def __init__(self) -> None:
            self.rcvd = _Close(4001, "replaced by newer agent connection")
            self.sent = _Close(1000)
            self.code = 4001

    client = RealtimeClient(
        "ws://relay.test",
        "agt",
        connect=fake_connect,
        sleep=asyncio.sleep,
        clock=lambda: 0.0,
        random_fn=lambda: 0.0,
        ping_interval=10.0,
        dead_timeout=5.0,
        reconnect_delays=[0.05],
    )
    run_task = asyncio.create_task(client.run())
    await wait_for_event(client, EVENT_CONNECTED)
    conn.push(Superseded())
    disconnected = await wait_for_event(client, EVENT_DISCONNECTED)
    assert "close 4001" in disconnected["reason"]
    assert "另一个 Agent 实例" in disconnected["reason"]
    await wait_for_event(client, EVENT_RECONNECT_SCHEDULED)
    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)


# ---------------------------------------------------------------- 真实本地 WS 握手


async def test_real_websocket_handshake_with_local_fake_relay():
    """用本地 websockets server 验证真实握手：路径 + Authorization header + hello + 消息。"""
    websockets = pytest.importorskip("websockets")
    from websockets.asyncio.server import serve

    received: list[dict[str, Any]] = []
    handshake: dict[str, Any] = {}

    async def handler(ws):
        handshake["path"] = ws.request.path
        handshake["authorization"] = ws.request.headers.get("Authorization")
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "agent_id": "agent-123",
                    "server_time": "2026-10-24T08:00:00.000Z",
                }
            )
        )
        try:
            # v0.3：服务端在线探测用 JSON ping/probe，Agent 必须回 pong/probe_ack。
            await ws.send(json.dumps({"type": "ping", "ts": 111}))
            await ws.send(json.dumps({"type": "probe", "ts": 222}))
            async for raw in ws:
                message = json.loads(raw)
                received.append(message)
        except Exception:  # pragma: no cover - 客户端断开
            pass

    server = await serve(handler, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        client = RealtimeClient(
            f"ws://127.0.0.1:{port}",
            "agt_real_handshake_token",
            ping_interval=0.2,  # 协议层 ping（websockets 内部处理，不产生业务 JSON）
            ping_timeout=0.2,
            hello_timeout=2.0,
        )
        assert client.url == f"ws://127.0.0.1:{port}/api/v1/agent/ws"
        run_task = asyncio.create_task(client.run())
        hello = await wait_for_event(client, "hello")
        await wait_for_event(client, EVENT_CONNECTED)
        assert hello["agent_id"] == "agent-123"
        assert handshake["path"] == "/api/v1/agent/ws"
        assert handshake["authorization"] == "Bearer agt_real_handshake_token"

        assert await client.send({"type": "task_update", "task_id": "t1", "status": "running"})
        await wait_until(lambda: any(m.get("type") == "task_update" for m in received))
        # 对服务端 JSON ping/probe 的响应
        await wait_until(lambda: any(m.get("type") == "pong" for m in received))
        await wait_until(lambda: any(m.get("type") == "probe_ack" for m in received))
        # v0.3：Agent 不再发应用层 JSON ping（心跳走 WebSocket 协议 ping）
        assert not any(m.get("type") == "ping" for m in received)
        assert client.connected is True
    finally:
        await client.stop()
        await asyncio.wait_for(run_task, timeout=3)
        server.close()
        await server.wait_closed()


async def test_real_websocket_401_is_auth_error_and_keeps_retrying():
    """真实 WebSocket 握手被 401 拒绝：客户端产生 _auth_error 事件并继续重连。"""
    from http import HTTPStatus

    from websockets.asyncio.server import serve

    async def process_request(connection, request):
        return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")

    async def handler(ws):  # pragma: no cover - 不应被调用
        await ws.send("{}")

    server = await serve(handler, "127.0.0.1", 0, process_request=process_request)
    try:
        port = server.sockets[0].getsockname()[1]
        client = RealtimeClient(
            f"ws://127.0.0.1:{port}",
            "agt_revoked",
            reconnect_delays=[0.05],
            ping_interval=0.2,
            dead_timeout=2.0,
            hello_timeout=1.0,
        )
        run_task = asyncio.create_task(client.run())
        event = await wait_for_event(client, EVENT_AUTH_ERROR)
        assert "401" in event["message"]
        assert client.connected is False
        assert not run_task.done()  # 永久重连，不崩溃
    finally:
        await client.stop()
        await asyncio.wait_for(run_task, timeout=3)
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------- 重连 / 退避


async def test_reconnect_backoff_sequence():
    """连续连接失败时退避 1/2/4/8 秒（对应协议 1/2/4/8/15/30），成功后继续。"""
    clock = FakeClock()
    connect_times: list[float] = []
    connections: list[FakeConnection] = []
    hello = {"type": "hello", "agent_id": "a1", "server_time": "t"}

    async def fake_connect(url: str, headers: dict[str, str]):
        connect_times.append(clock.now)
        if len(connect_times) <= 4:  # 前 4 次失败
            raise ConnectionRefusedError("refused")
        conn = FakeConnection(hello=hello)
        connections.append(conn)
        return conn

    client = RealtimeClient(
        "ws://relay.test",
        "agt_token",
        connect=fake_connect,
        sleep=clock.sleep,
        clock=clock.clock,
        random_fn=clock.random,
        jitter_ratio=0.0,
        network_probe=None,
    )
    run_task = asyncio.create_task(client.run())
    await wait_for_event(client, EVENT_CONNECTED)
    assert len(connect_times) == 5
    # 第 2 次连接在第 1 秒、第 3 次 +2、第 4 次 +4、第 5 次 +8
    assert connect_times == pytest.approx([0.0, 1.0, 3.0, 7.0, 15.0])

    # 收到 hello 后能正常收消息
    conn = connections[0]
    conn.push({"type": "task", "task": {"id": "t1", "url": "u"}})
    message = await wait_for_event(client, "task")
    assert message["task"]["id"] == "t1"

    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)
    assert client.connected is False


async def test_network_probe_reconnects_immediately():
    """网络类错误时，TCP 探测到恢复就立即重连，不用等满退避。"""
    clock = FakeClock()
    attempts = {"n": 0}
    hello = {"type": "hello", "agent_id": "a1", "server_time": "t"}
    connected = asyncio.Event()

    async def fake_connect(url: str, headers: dict[str, str]):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionRefusedError("network down")
        connected.set()
        return FakeConnection(hello=hello)

    async def probe() -> bool:
        # 第一次探测（退避第 1 秒）后就返回网络已恢复
        return True

    client = RealtimeClient(
        "ws://relay.test",
        "agt_token",
        connect=fake_connect,
        sleep=clock.sleep,
        clock=clock.clock,
        random_fn=clock.random,
        jitter_ratio=0.0,
        network_probe=probe,
        reconnect_delays=[8.0],
    )
    run_task = asyncio.create_task(client.run())
    await connected.wait()
    assert clock.now == pytest.approx(1.0)  # 只等了 1 秒探测
    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)


async def test_auth_error_event_and_keeps_retrying():
    class FakeResponse:
        status_code = 401

    class InvalidStatus(Exception):
        def __init__(self) -> None:
            self.response = FakeResponse()

    async def fake_connect(url: str, headers: dict[str, str]):
        raise InvalidStatus()

    clock = FakeClock()
    client = RealtimeClient(
        "ws://relay.test",
        "agt_bad",
        connect=fake_connect,
        sleep=clock.sleep,
        clock=clock.clock,
        random_fn=clock.random,
        jitter_ratio=0.0,
        reconnect_delays=[1.0, 2.0],
    )
    run_task = asyncio.create_task(client.run())
    event = await wait_for_event(client, EVENT_AUTH_ERROR)
    assert "pair" in event["message"].lower() or "配对" in event["message"]
    await wait_for_event(client, EVENT_RECONNECT_SCHEDULED)
    assert not run_task.done()  # 永久重连，不崩溃
    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)


# ---------------------------------------------------------------- 心跳 / 收发


async def test_server_json_ping_probe_and_no_app_level_ping():
    """v0.3：Agent 不再发 25s 应用层 JSON ping；但服务端 JSON ping/probe 仍要回 pong/probe_ack。"""
    clock = FakeClock()
    hello = {"type": "hello", "agent_id": "a1", "server_time": "t"}
    conn = FakeConnection(hello=hello)
    sent_types: list[str] = []

    async def fake_connect(url: str, headers: dict[str, str]):
        return conn

    client = RealtimeClient(
        "ws://relay.test",
        "agt",
        connect=fake_connect,
        sleep=asyncio.sleep,
        clock=lambda: 0.0,
        random_fn=clock.random,
        ping_interval=0.05,  # 协议层 ping 参数（fake connection 下无实际心跳帧）
        ping_timeout=0.05,
        reconnect_delays=[0.05],
    )
    run_task = asyncio.create_task(client.run())
    await wait_for_event(client, EVENT_CONNECTED)
    await asyncio.sleep(0.15)
    sent_types = [json.loads(raw).get("type") for raw in conn.sent]
    assert "ping" not in sent_types, "不应再有应用层 JSON ping 循环"

    # 服务端发 JSON ping → 回 pong，并给 Runner 一个刷新 readiness 的事件
    conn.push({"type": "ping", "ts": 123})
    await wait_until(lambda: any(json.loads(raw).get("type") == "pong" for raw in conn.sent))
    ping_event = await wait_for_event(client, EVENT_SERVER_PING)
    assert ping_event["ts"] == 123

    # 服务端发 probe → 回 probe_ack
    conn.push({"type": "probe", "ts": 456})
    await wait_until(
        lambda: any(json.loads(raw).get("type") == "probe_ack" for raw in conn.sent)
    )
    probe_event = await wait_for_event(client, EVENT_SERVER_PROBE)
    assert probe_event["ts"] == 456

    # 服务端关闭 → 客户端产生 _disconnected 并重连
    await conn.close()
    disconnected = await wait_for_event(client, EVENT_DISCONNECTED)
    assert disconnected["reason"]
    await wait_for_event(client, EVENT_RECONNECT_SCHEDULED)
    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)


async def test_send_when_disconnected_returns_false():
    client = RealtimeClient("ws://relay.test", "agt")
    assert client.connected is False
    assert await client.send({"type": "ping"}) is False


async def test_default_connect_uses_protocol_ping_params(monkeypatch):
    """默认 connect 必须把 ping_interval=25 / ping_timeout=20 传给 websockets。"""
    import linknote_agent.relay_ws as relay_ws

    captured: dict = {}

    async def fake_connect_ws(url, headers, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeConnection(hello={"type": "hello", "agent_id": "a1", "server_time": "t"})

    monkeypatch.setattr(relay_ws, "connect_websocket", fake_connect_ws)
    client = RealtimeClient("ws://relay.test", "agt", ping_interval=7.0, ping_timeout=3.0)
    run_task = asyncio.create_task(client.run())
    await wait_for_event(client, EVENT_CONNECTED)
    assert captured["ping_interval"] == 7.0
    assert captured["ping_timeout"] == 3.0
    assert client.protocol_ping_interval == 7.0
    assert client.protocol_ping_timeout == 3.0
    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)


async def test_no_recv_idle_timeout_keeps_connection():
    """v0.3：不再按「多久没收到消息」判死连接（协议 ping/pong 负责），空闲连接保持。"""
    hello = {"type": "hello", "agent_id": "a1", "server_time": "t"}
    conn = FakeConnection(hello=hello)

    async def fake_connect(url: str, headers: dict[str, str]):
        return conn

    client = RealtimeClient(
        "ws://relay.test",
        "agt",
        connect=fake_connect,
        sleep=asyncio.sleep,
        clock=lambda: 0.0,
        random_fn=lambda: 0.0,
        ping_interval=10.0,
        ping_timeout=10.0,
        dead_timeout=0.05,  # 已废弃参数：不应再影响连接
        reconnect_delays=[0.05],
    )
    run_task = asyncio.create_task(client.run())
    await wait_for_event(client, EVENT_CONNECTED)
    await asyncio.sleep(0.2)
    assert client.connected is True, "空闲期间不应因 dead_timeout 断开"
    await client.stop()
    await asyncio.wait_for(run_task, timeout=3)

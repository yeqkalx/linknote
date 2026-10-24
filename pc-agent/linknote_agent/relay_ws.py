"""LinkNote Realtime Protocol v1 的 Agent WebSocket 客户端。

协议：``docs/realtime-protocol.md``（已冻结）。

- 连接：``ws(s)://<relay>/api/v1/agent/ws``，Header ``Authorization: Bearer <agent_token>``；
- 连上后等服务端 ``hello``，之后把消息放进 ``events`` 队列，由 Runner 消费；
- 应用层心跳：每 25s 发 ``{"type":"ping","ts":...}``，对端回 ``pong``；
  超过 ``dead_timeout``（默认 60s）没有任何消息就判定断线；
- 断线指数退避 ``1/2/4/8/15/30s`` + 抖动，永久重连；网络类错误时用轻量 TCP 探测
  在网络恢复后立即重连（不必等满退避）。

消息分发与重连逻辑都在本模块，便于用 fake connection / 本地 fake WS server 单测。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from .logging_setup import get_logger

logger = get_logger("ws")

WS_PATH = "/api/v1/agent/ws"

#: 协议 §3：两端每 25s 发 ping。
DEFAULT_PING_INTERVAL = 25.0
#: 协议 §3：Agent last_seen > 60s 服务端会关连接；客户端同样用它判定死连接。
DEFAULT_DEAD_TIMEOUT = 60.0
#: 连接后等 hello 的时间（DO 正常是立即发）。
DEFAULT_HELLO_TIMEOUT = 10.0
#: 协议 §5：1/2/4/8/15/30s + jitter，上限 30s。
DEFAULT_RECONNECT_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
DEFAULT_JITTER_RATIO = 0.2
#: 连接保持超过这个时长才算「稳定」，下一次断线从 1s 重新退避。
STABLE_CONNECTION_SECONDS = 10.0
MAX_BACKOFF_SECONDS = 30.0

#: 传输层/协议层事件（以 ``_`` 开头，与协议消息区分）
EVENT_CONNECTED = "_connected"
EVENT_DISCONNECTED = "_disconnected"
EVENT_AUTH_ERROR = "_auth_error"
EVENT_RECONNECT_SCHEDULED = "_reconnect_scheduled"

ConnectFunc = Callable[[str, dict[str, str]], Awaitable[Any]]
SleepFunc = Callable[[float], Awaitable[Any]]
RandomFunc = Callable[[], float]
ClockFunc = Callable[[], float]
ProbeFunc = Callable[[], Awaitable[bool]]


class WSError(Exception):
    """WebSocket 连接/协议错误。

    ``kind``:
    - ``network``：连不上 / 断网 / 读超时，值得快速重试；
    - ``auth``：握手 401/403，需要重新配对（仍会按退避永久重连）；
    - ``protocol``：握手成功但协议不对（没有 hello 等）。
    """

    def __init__(self, message: str, *, kind: str = "network", status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status_code = status_code


# ---------------------------------------------------------------- URL / 退避


def build_ws_url(relay_base_url: str, *, path: str = WS_PATH, ws_url: str = "") -> str:
    """由 REST relay 地址推导 WS 地址。

    - ``http://127.0.0.1:8787`` → ``ws://127.0.0.1:8787/api/v1/agent/ws``
    - ``https://relay.xykong.xyz`` → ``wss://relay.xykong.xyz/api/v1/agent/ws``
    - ``ws_url`` 非空时直接用它（本地联调/测试）。
    """
    if ws_url.strip():
        url = ws_url.strip()
        parts = urlsplit(url)
        if parts.scheme in ("http", "https"):
            url = urlunsplit(
                ("ws" if parts.scheme == "http" else "wss", parts.netloc, parts.path, parts.query, "")
            )
        elif parts.scheme not in ("ws", "wss"):
            raise WSError(f"WS 地址 scheme 非法（需要 ws/wss）：{ws_url}", kind="protocol")
        return url

    parts = urlsplit((relay_base_url or "").strip())
    if parts.scheme not in ("http", "https", "ws", "wss"):
        raise WSError(f"Relay 地址非法：{relay_base_url!r}", kind="protocol")
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[parts.scheme]
    prefix = parts.path.rstrip("/")
    return urlunsplit((scheme, parts.netloc, f"{prefix}{path}", "", ""))


def compute_backoff(
    attempt: int,
    delays: tuple[float, ...] | list[float] = DEFAULT_RECONNECT_DELAYS,
    *,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    random_fn: RandomFunc = random.random,
) -> float:
    """第 ``attempt`` 次重连的等待秒数（从 0 开始），带 0–jitter_ratio 抖动，封顶 30s。"""
    if not delays:
        return 1.0
    index = min(max(int(attempt), 0), len(delays) - 1)
    base = float(delays[index])
    jitter = base * max(0.0, float(jitter_ratio)) * max(0.0, min(1.0, float(random_fn())))
    return min(base + jitter, MAX_BACKOFF_SECONDS, float(delays[-1]))


def classify_connect_error(exc: BaseException) -> WSError:
    """把 websockets/网络异常归类成 :class:`WSError`。"""
    if isinstance(exc, WSError):
        return exc

    # 服务端主动关闭（契约 §11.1：4001 被新 Agent 顶掉 / 4002 心跳超时 / 4003 手机设备被撤销）
    close_code, close_reason = _close_code_and_reason(exc)
    if close_code is not None:
        reason_suffix = f"（{close_reason}）" if close_reason else ""
        if close_code == 4001:
            return WSError(
                "另一个 Agent 实例已连接，本连接被服务器顶掉（close 4001）"
                f"{reason_suffix}。可能同时启动了多个 Agent，建议只保留一个。",
                kind="superseded",
            )
        if close_code == 4002:
            return WSError(
                f"服务端心跳超时断开了连接（close 4002）{reason_suffix}，稍后自动重连。",
                kind="network",
            )
        if close_code in (1000, 1001):
            return WSError(
                f"Relay 主动关闭了连接（close {close_code}）{reason_suffix}，稍后自动重连。",
                kind="network",
            )
        return WSError(
            f"Relay WebSocket 连接断开（close {close_code}）{reason_suffix}",
            kind="network",
        )

    status_code: Optional[int] = None
    response = getattr(exc, "response", None)
    if response is not None:
        candidate = getattr(response, "status_code", None)
        if isinstance(candidate, int):
            status_code = candidate
    if status_code is None:
        candidate = getattr(exc, "status_code", None)
        if isinstance(candidate, int):
            status_code = candidate

    if status_code in (401, 403):
        return WSError(
            "配对已失效或无权连接 Relay WebSocket（HTTP %d）。"
            "请重新运行 pair 命令配对。" % status_code,
            kind="auth",
            status_code=status_code,
        )

    name = type(exc).__name__
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return WSError("连接 Relay WebSocket 超时", kind="network")
    if isinstance(exc, (OSError, ConnectionError)):
        return WSError(f"无法连接 Relay WebSocket：{exc}", kind="network")
    if name in ("InvalidStatus", "InvalidStatusCode") or status_code is not None:
        return WSError(
            f"Relay WebSocket 握手被拒绝（HTTP {status_code or '?'}）：{exc}",
            kind="protocol",
            status_code=status_code,
        )
    return WSError(f"Relay WebSocket 连接中断：{exc}", kind="network")


def _close_code_and_reason(exc: BaseException) -> tuple[Optional[int], str]:
    """从 websockets 的 ConnectionClosed 里取服务端关闭码/原因（兼容旧版属性）。"""
    for attr in ("rcvd", "sent"):
        close = getattr(exc, attr, None)
        if close is None:
            continue
        code = getattr(close, "code", None)
        if isinstance(code, int):
            return code, str(getattr(close, "reason", "") or "")
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code, ""
    return None, ""


async def default_network_probe(url: str) -> bool:
    """轻量 TCP 探测 Relay 主机是否可达（用于网络恢复后立即重连）。"""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        return False
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001 - 探测失败只表示"还没恢复"
        return False


# ---------------------------------------------------------------- 连接实现


async def connect_websocket(url: str, headers: dict[str, str], *, timeout: float = 10.0) -> Any:
    """真实连接（包装 websockets.asyncio.client.connect，便于测试注入）。"""
    from websockets.asyncio.client import connect as ws_connect

    return await ws_connect(
        url,
        additional_headers=headers,
        open_timeout=timeout,
        close_timeout=5.0,
        ping_interval=None,  # 用协议里的应用层 ping，不用 WS 控制帧 ping
        max_size=None,  # task_note 可能较大（≤1.5MiB）
    )


class RealtimeClient:
    """常驻 WebSocket 客户端：连接 / 重连 / 心跳 / 解析 / 事件队列。

    对外接口（Runner 只用这些，测试可注入 fake）：
    - ``run()``：永久运行（直到 ``stop()``），内部负责重连；
    - ``send(payload)``：发一条 JSON 消息，未连接返回 False（不抛异常）；
    - ``events``：``asyncio.Queue[dict]``，收到的协议消息 + ``_connected`` 等事件；
    - ``connected`` / ``agent_id`` / ``url``。
    """

    def __init__(
        self,
        relay_base_url: str,
        agent_token: str,
        *,
        ws_url: str = "",
        connect: Optional[ConnectFunc] = None,
        sleep: SleepFunc = asyncio.sleep,
        random_fn: RandomFunc = random.random,
        clock: ClockFunc = time.monotonic,
        network_probe: Optional[ProbeFunc] = None,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        dead_timeout: float = DEFAULT_DEAD_TIMEOUT,
        hello_timeout: float = DEFAULT_HELLO_TIMEOUT,
        reconnect_delays: tuple[float, ...] | list[float] = DEFAULT_RECONNECT_DELAYS,
        jitter_ratio: float = DEFAULT_JITTER_RATIO,
        user_agent: str = "LinkNote-Agent/0.1",
    ):
        self.url = build_ws_url(relay_base_url, ws_url=ws_url)
        self.agent_token = agent_token
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.connected = False
        self.agent_id = ""
        self.server_time = ""
        self.last_message_at = 0.0

        self._connect = connect or connect_websocket
        self._sleep = sleep
        self._random = random_fn
        self._clock = clock
        self._probe = network_probe
        self._ping_interval = max(0.1, float(ping_interval))
        self._dead_timeout = max(0.1, float(dead_timeout))
        self._hello_timeout = max(0.1, float(hello_timeout))
        self._reconnect_delays = tuple(float(d) for d in reconnect_delays) or DEFAULT_RECONNECT_DELAYS
        self._jitter_ratio = float(jitter_ratio)
        self._user_agent = user_agent

        self._ws: Any = None
        self._stop = asyncio.Event()
        self._attempt = 0
        self._connected_once = False
        self._connected_at = 0.0
        self._last_auth_log = -1e9
        self._auth_error_count = 0

    # ---------------------------------------------------------------- 计算

    def backoff_delay(self, attempt: int) -> float:
        return compute_backoff(
            attempt,
            self._reconnect_delays,
            jitter_ratio=self._jitter_ratio,
            random_fn=self._random,
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.agent_token}",
            "User-Agent": self._user_agent,
        }

    # ---------------------------------------------------------------- 主循环

    async def run(self) -> None:
        """永久连接循环（断线自动重连），直到 :meth:`stop` 被调用。"""
        logger.info("开始连接 Relay WebSocket：%s", self.url)
        while not self._stop.is_set():
            error: Optional[WSError] = None
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except WSError as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - 常驻进程不允许被单次异常带走
                error = classify_connect_error(exc)
            finally:
                self.connected = False
                await self._close_socket()

            if self._stop.is_set():
                break

            reason = error.message if error is not None else "连接已关闭"
            self.events.put_nowait({"type": EVENT_DISCONNECTED, "reason": reason})
            kind = "network"
            if error is not None:
                kind = error.kind
                if error.kind == "superseded":
                    logger.error(
                        "WebSocket 被服务器顶掉：%s 本实例会继续退避重连"
                        "（若这是重复启动的第二个 Agent，请关掉一个）。",
                        error.message,
                    )
                elif error.kind == "auth":
                    logger.warning("WebSocket 连接失败：%s", error.message)
                    await self._handle_auth_error(error)
                else:
                    logger.warning("WebSocket 连接失败：%s", error.message)
            else:
                logger.warning("WebSocket 连接已关闭，准备重连")

            # 连接稳定过一段时间 → 下一次从 1s 重新开始；快速反复掉线则继续升级退避。
            duration = (
                (self._clock() - self._connected_at) if self._connected_once else 0.0
            )
            if self._connected_once and duration >= STABLE_CONNECTION_SECONDS:
                self._attempt = 0
            self._connected_once = False

            delay = self.backoff_delay(self._attempt)
            self._attempt = min(self._attempt + 1, max(len(self._reconnect_delays) - 1, 0))
            self.events.put_nowait(
                {
                    "type": EVENT_RECONNECT_SCHEDULED,
                    "delay": round(delay, 3),
                    "attempt": self._attempt,
                }
            )
            logger.info("WebSocket %.1f 秒后重连（第 %d 次）", delay, self._attempt)
            await self._backoff_wait(delay, kind=kind)

        logger.info("WebSocket 客户端已停止")

    async def stop(self) -> None:
        self._stop.set()
        await self._close_socket()

    # ---------------------------------------------------------------- 单次连接

    async def _run_once(self) -> None:
        """建立一次连接并处理直到断开（异常/关闭时抛出或返回）。"""
        try:
            self._ws = await self._connect(self.url, self.headers)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(exc) from exc

        ws = self._ws
        self._connected_once = True
        try:
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=self._hello_timeout)
        except asyncio.TimeoutError as exc:
            raise WSError(
                f"连接成功但 {self._hello_timeout:.0f} 秒内没有收到 hello",
                kind="protocol",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(exc) from exc

        hello = self._decode(hello_raw)
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            raise WSError(
                f"WebSocket 首条消息不是 hello：{str(hello_raw)[:200]}", kind="protocol"
            )

        now = self._clock()
        self.connected = True
        self.agent_id = str(hello.get("agent_id") or "")
        self.server_time = str(hello.get("server_time") or "")
        self.last_message_at = now
        self._connected_at = now
        self.events.put_nowait(hello)
        self.events.put_nowait({"type": EVENT_CONNECTED, "agent_id": self.agent_id})
        logger.info(
            "WebSocket 已连接（agent_id=%s，URL=%s）", self.agent_id or "?", self.url
        )

        await self._recv_loop(ws)

    async def _recv_loop(self, ws: Any) -> None:
        ping_task = asyncio.create_task(self._ping_loop(ws), name="ws-ping")
        try:
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._dead_timeout)
                except asyncio.TimeoutError as exc:
                    raise WSError(
                        f"超过 {self._dead_timeout:.0f} 秒没有收到任何消息（心跳超时）",
                        kind="network",
                    ) from exc
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 正常/异常关闭都按断线处理
                    raise classify_connect_error(exc) from exc

                self.last_message_at = self._clock()
                await self._handle_incoming(raw)
        finally:
            ping_task.cancel()
            try:
                await ping_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _ping_loop(self, ws: Any) -> None:
        while not self._stop.is_set() and self.connected:
            await self._sleep(self._ping_interval)
            if self._stop.is_set() or not self.connected:
                return
            ok = await self._send_on(ws, {"type": "ping", "ts": int(self._clock() * 1000)})
            if not ok:
                logger.warning("WebSocket ping 发送失败，关闭连接触发重连")
                await self._close_socket()
                return

    # ---------------------------------------------------------------- 消息

    async def _handle_incoming(self, raw: Any) -> None:
        message = self._decode(raw)
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            logger.warning("忽略无法解析的 WebSocket 消息：%r", str(raw)[:200])
            return
        msg_type = message["type"]
        if msg_type == "ping":
            await self._send_on(self._ws, {"type": "pong", "ts": message.get("ts")})
            return
        if msg_type == "pong":
            return
        self.events.put_nowait(message)

    @staticmethod
    def _decode(raw: Any) -> Any:
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw

    async def send(self, payload: dict[str, Any]) -> bool:
        """发一条 JSON 消息；未连接/发送失败返回 False 并触发重连。"""
        if not self.connected or self._ws is None:
            return False
        ok = await self._send_on(self._ws, payload)
        if not ok:
            logger.warning("WebSocket 发送失败，关闭连接触发重连")
            await self._close_socket()
        return ok

    async def _send_on(self, ws: Any, payload: dict[str, Any]) -> bool:
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("WebSocket send 失败：%s", exc)
            return False

    # ---------------------------------------------------------------- 退避

    async def _backoff_wait(self, delay: float, *, kind: str) -> None:
        """等待重连；网络类错误期间每秒探测一次，网络恢复立即重连。"""
        deadline = self._clock() + max(0.0, delay)
        probe = self._probe
        while not self._stop.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            await self._sleep(min(1.0, remaining))
            if kind == "network" and probe is not None:
                try:
                    if await probe():
                        logger.info("探测到网络已恢复，立即重连 WebSocket")
                        return
                except Exception:  # noqa: BLE001
                    pass

    async def _close_socket(self) -> None:
        ws, self._ws = self._ws, None
        self.connected = False
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.close(), timeout=5.0)
        except Exception:  # noqa: BLE001 - 关闭失败无所谓
            pass

    async def _handle_auth_error(self, error: WSError) -> None:
        self._auth_error_count += 1
        now = self._clock()
        # 每次尝试都通知 Runner（用于 UI/日志），但日志本身去重，避免刷屏。
        self.events.put_nowait({"type": EVENT_AUTH_ERROR, "message": error.message})
        if now - self._last_auth_log >= 60.0:
            self._last_auth_log = now
            logger.error(
                "Relay 拒绝了 WebSocket 认证（%s）。请重新运行 pair 命令配对；"
                "本进程会继续永久重连。",
                error.message,
            )


__all__ = [
    "DEFAULT_DEAD_TIMEOUT",
    "DEFAULT_HELLO_TIMEOUT",
    "DEFAULT_PING_INTERVAL",
    "DEFAULT_RECONNECT_DELAYS",
    "EVENT_AUTH_ERROR",
    "EVENT_CONNECTED",
    "EVENT_DISCONNECTED",
    "EVENT_RECONNECT_SCHEDULED",
    "RealtimeClient",
    "WSError",
    "build_ws_url",
    "classify_connect_error",
    "compute_backoff",
    "connect_websocket",
    "default_network_probe",
]

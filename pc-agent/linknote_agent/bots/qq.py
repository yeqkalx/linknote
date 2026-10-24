"""QQ 官方机器人 WebSocket 网关适配器（v0.4）。

流程（docs/bot-channels.md）：
    getAppAccessToken（缓存 7200s，提前刷新）
    → GET /gateway
    → WSS 连接 → 收 op10 hello → op2 Identify(token="QQBot <at>", intents=1<<25, shard=[0,1])
    → 心跳 op1/op11、断线 Resume(op6) / Reconnect(op7) / Invalid Session(op9)
    → 解析 C2C_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE（content 或 ark_data.fields.jump_url）
    → 被动回复（msg_id=事件 id、msg_seq 递增；C2C 最多 4 次、群最多 5 次）
    → 窗口关闭/权限失败抛 :class:`ReplyWindowClosed`，由 Runner 转 pending 补发。

注意：真实 AppID/AppSecret 只从本机 agent.json / 环境变量读取；日志不打印明文。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, Optional

import httpx

from ..logging_setup import get_logger
from .base import BotAdapter, BotError, IncomingMessage, MessageCallback, ReplyWindowClosed
from .state import BotState

logger = get_logger("bots.qq")

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
DEFAULT_API_BASE = "https://api.sgroup.qq.com"
#: 群/单聊事件 intents（见 QQ 开放平台；若平台未授权 Identify 会失败）
GROUP_AND_C2C_INTENTS = 1 << 25
C2C_MAX_PASSIVE = 4
GROUP_MAX_PASSIVE = 5
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
EVENT_DEDUPE_SIZE = 500

_TITLE = "LinkNote"
_OS = "windows"
_BROWSER = "linknote-agent"
_DEVICE = "pc"


class QQBot(BotAdapter):
    name = "qq"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        state: Optional[BotState] = None,
        transport: httpx.AsyncBaseTransport | None = None,
        token_url: str = TOKEN_URL,
        api_base: str = DEFAULT_API_BASE,
        chunk_chars: int = 1500,
        connect=None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self._app_id = app_id or ""
        self._app_secret = app_secret or ""
        self.state = state or BotState()
        self.chunk_chars = max(20, int(chunk_chars))
        self._token_url = token_url
        self._api_base = api_base.rstrip("/")
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
            headers={"User-Agent": "LinkNote-Agent-Bot/0.4"},
        )
        self._connect = connect or self._default_connect
        self._sleep = sleep
        self._clock = clock

        self._access_token = ""
        self._token_expires_at = 0.0
        self._stop = asyncio.Event()
        self._ws_task: Optional[asyncio.Task] = None
        self._seen = deque(maxlen=EVENT_DEDUPE_SIZE)
        self._seen_set: set[str] = set()
        self._event_seq = int(self.state.qq_event_seq or 0)
        self.ready_at: Optional[float] = None

    # ---------------------------------------------------------------- token

    def _mask_secret(self, text: str) -> str:
        message = str(text)
        for secret in (self._app_secret, self._access_token):
            if secret:
                message = message.replace(secret, "***")
        return message

    async def access_token(self, *, force: bool = False) -> str:
        now = self._clock()
        if (
            not force
            and self._access_token
            and now < self._token_expires_at - 60.0
        ):
            return self._access_token
        try:
            resp = await self._http.post(
                self._token_url,
                json={"appId": self._app_id, "clientSecret": self._app_secret},
            )
        except httpx.HTTPError as exc:
            raise BotError(
                f"无法连接 QQ 开放平台获取 access_token（{type(exc).__name__}）",
                retriable=True,
            ) from exc
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BotError("QQ getAppAccessToken 返回非 JSON", retriable=resp.status_code >= 500) from exc
        payload = payload if isinstance(payload, dict) else {}
        token = str(payload.get("access_token") or "")
        if not token:
            raise BotError(
                self._mask_secret(
                    f"QQ getAppAccessToken 失败：{payload.get('message') or payload.get('code') or '未知错误'}"
                ),
                retriable=resp.status_code >= 500,
            )
        try:
            expires_in = float(payload.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires_in = 7200.0
        self._access_token = token
        self._token_expires_at = now + max(300.0, expires_in)
        logger.info("QQ access_token 已刷新（有效期约 %.0f 秒）", max(300.0, expires_in))
        return token

    # ---------------------------------------------------------------- HTTP API

    async def _api_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        retry_auth: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        token = await self.access_token()
        headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
        try:
            resp = await self._http.request(
                method, f"{self._api_base}{path}", headers=headers, json=json_body
            )
        except httpx.HTTPError as exc:
            raise BotError(
                f"无法连接 QQ API（{type(exc).__name__}）", retriable=True
            ) from exc
        if resp.status_code == 401 and retry_auth:
            await self.access_token(force=True)
            return await self._api_request(
                method, path, json_body=json_body, retry_auth=False
            )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        return resp.status_code, payload

    @staticmethod
    def _error_from(status: int, payload: dict[str, Any], fallback: str) -> BotError:
        message = str(payload.get("message") or payload.get("msg") or "") or fallback
        code = str(payload.get("code") or "")
        return BotError(
            message,
            retriable=status == 429 or status >= 500,
            code=code,
            status_code=status,
        )

    async def get_gateway(self) -> str:
        status, payload = await self._api_request("GET", "/gateway")
        if status >= 400:
            raise self._error_from(status, payload, f"QQ GET /gateway 失败（HTTP {status}）")
        url = str(payload.get("url") or "")
        if not url:
            raise BotError("QQ 网关没有返回 url", retriable=True)
        return url

    # ---------------------------------------------------------------- 连接

    async def _default_connect(self, url: str):
        from websockets.asyncio.client import connect as ws_connect

        return await ws_connect(url, ping_interval=None, max_size=None, open_timeout=20)

    async def start(self, on_message: MessageCallback) -> None:
        # 先拿一次 token，坏凭证/无权限在启动时就给出清晰错误
        await self.access_token()
        self._ws_task = asyncio.create_task(self._ws_loop(on_message), name="qq-gateway")
        logger.info("QQ Bot 适配器启动（WebSocket 网关）")

    async def _ws_loop(self, on_message: MessageCallback) -> None:
        attempt = 0
        while not self._stop.is_set():
            ws = None
            try:
                url = await self.get_gateway()
                ws = await self._connect(url)
                await self._session(ws, on_message)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except ReplyWindowClosed:
                raise
            except BotError as exc:
                logger.warning("QQ 网关连接失败：%s", exc.message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("QQ 网关异常：%s", self._mask_secret(str(exc)))
            finally:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self._stop.is_set():
                return
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            logger.info("QQ 网关 %.1fs 后重连", delay)
            await self._sleep(delay)

    async def _session(self, ws: Any, on_message: MessageCallback) -> None:
        raw = await ws.recv()
        hello = self._decode(raw)
        if not isinstance(hello, dict) or hello.get("op") != 10:
            raise BotError("QQ 网关首帧不是 op10 hello", retriable=True)
        data = hello.get("d") if isinstance(hello.get("d"), dict) else {}
        try:
            heartbeat_ms = float(data.get("heartbeat_interval") or 30000)
        except (TypeError, ValueError):
            heartbeat_ms = 30000.0
        heartbeat_seconds = max(0.05, heartbeat_ms / 1000.0)

        token = await self.access_token()
        if self.state.qq_session_id:
            await ws.send(
                json.dumps(
                    {
                        "op": 6,
                        "d": {
                            "token": f"QQBot {token}",
                            "session_id": self.state.qq_session_id,
                            "seq": self._event_seq,
                        },
                    },
                    ensure_ascii=False,
                )
            )
            logger.info("QQ 网关 Resume（session 已存在）")
        else:
            await ws.send(self._identify_frame(token))
            logger.info("QQ 网关 Identify 已发送")

        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(ws, heartbeat_seconds), name="qq-heartbeat"
        )
        try:
            while not self._stop.is_set():
                raw = await ws.recv()
                message = self._decode(raw)
                if not isinstance(message, dict):
                    continue
                op = message.get("op")
                data = message.get("d")
                if op == 11:  # heartbeat ACK
                    continue
                if op == 7:  # server request reconnect
                    logger.info("QQ 网关要求重连（op7）")
                    return
                if op == 9:  # invalid session
                    logger.warning("QQ 网关 session 失效（op9），将重新 Identify")
                    self.state.set_qq_session("")
                    return
                if op == 0 and isinstance(data, dict):
                    try:
                        seq = int(message.get("s"))
                        self._event_seq = seq
                        self.state.qq_event_seq = seq
                    except (TypeError, ValueError):
                        pass
                    await self._dispatch(message, on_message)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def _identify_frame(self, token: str) -> str:
        return json.dumps(
            {
                "op": 2,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": GROUP_AND_C2C_INTENTS,
                    "shard": [0, 1],
                    "properties": {"$os": _OS, "$browser": _BROWSER, "$device": _DEVICE},
                },
            },
            ensure_ascii=False,
        )

    async def _heartbeat_loop(self, ws: Any, interval: float) -> None:
        while not self._stop.is_set():
            await self._sleep(interval)
            try:
                await ws.send(
                    json.dumps(
                        {"op": 1, "d": self._event_seq if self._event_seq else None}
                    )
                )
            except Exception:  # noqa: BLE001 - 连接断了交给外层重连
                return

    # ---------------------------------------------------------------- 事件

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

    async def _dispatch(self, frame: dict[str, Any], on_message: MessageCallback) -> None:
        event_type = str(frame.get("t") or "")
        event = frame.get("d")
        if not isinstance(event, dict):
            return
        if event_type == "READY":
            session_id = str(event.get("session_id") or "")
            if session_id:
                self.state.set_qq_session(session_id)
            self.ready_at = self._clock()
            logger.info("QQ Bot 已就绪（READY）")
            return
        if event_type not in ("C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"):
            return
        message = self._parse_event(event_type, event)
        if message is None:
            return
        if message.message_id:
            if message.message_id in self._seen_set:
                logger.debug("忽略重复 QQ 事件 id=%s", message.message_id)
                return
            if len(self._seen) == self._seen.maxlen:
                old = self._seen.popleft()
                self._seen_set.discard(old)
            self._seen.append(message.message_id)
            self._seen_set.add(message.message_id)
        await on_message(message)

    def _parse_event(self, event_type: str, event: dict[str, Any]) -> Optional[IncomingMessage]:
        event_id = str(event.get("id") or "")
        content = str(event.get("content") or "").strip()
        ark = event.get("ark_data")
        fields = ark.get("fields") if isinstance(ark, dict) else None
        jump_url = str(fields.get("jump_url") or "") if isinstance(fields, dict) else ""
        text = content or jump_url
        if not event_id or not text:
            return None
        author = event.get("author") if isinstance(event.get("author"), dict) else {}
        if event_type == "C2C_MESSAGE_CREATE":
            chat_id = str(author.get("user_openid") or author.get("id") or "")
            chat_type = "private"
            sender_id = chat_id
        else:
            chat_id = str(event.get("group_openid") or "")
            chat_type = "group"
            sender_id = str(author.get("member_openid") or author.get("id") or "")
        if not chat_id:
            return None
        return IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=event_id,
            text=text,
            reply_msg_id=event_id,
            sender_id=sender_id,
            raw=event,
        )

    # ---------------------------------------------------------------- 发送

    def _max_passive(self, message: IncomingMessage) -> int:
        return GROUP_MAX_PASSIVE if message.chat_type == "group" else C2C_MAX_PASSIVE

    def _messages_path(self, message: IncomingMessage) -> str:
        if message.chat_type == "group":
            return f"/v2/groups/{message.chat_id}/messages"
        return f"/v2/users/{message.chat_id}/messages"

    async def _send_body(self, message: IncomingMessage, body: dict[str, Any]) -> None:
        status, payload = await self._api_request(
            "POST", self._messages_path(message), json_body=body
        )
        if status < 400:
            return
        code = str(payload.get("code") or "")
        error = self._error_from(status, payload, f"QQ 发送消息失败（HTTP {status}）")
        # 被动回复窗口关闭 / 无权限：不视为崩溃，交给 Runner 转 pending
        if status in (400, 403) or code in ("40054005", "11244", "11245"):
            raise ReplyWindowClosed(
                f"QQ 被动回复不可用（HTTP {status}）：{error.message[:120]}；稍后可用 /last 重新获取"
            )
        raise error

    async def _send_passive(self, message: IncomingMessage, text: str) -> None:
        seq = self.state.next_qq_seq(message.chat_key)
        body = {
            "content": text,
            "msg_type": 0,
            "msg_id": message.reply_msg_id or message.message_id,
            "msg_seq": seq,
        }
        await self._send_body(message, body)

    async def _send_active(self, message: IncomingMessage, text: str) -> None:
        await self._send_body(message, {"content": text, "msg_type": 0})

    async def send_text(self, message: IncomingMessage, text: str) -> None:
        try:
            await self._send_passive(message, text)
        except ReplyWindowClosed:
            # 窗口关闭时尝试主动消息（官方允许且用户已配置）；失败则交给 Runner 转 pending
            try:
                await self._send_active(message, text)
            except BotError:
                raise ReplyWindowClosed() from None

    def _chunk(self, text: str) -> list[str]:
        content = text or ""
        if len(content) <= self.chunk_chars:
            return [content] if content else []
        chunks: list[str] = []
        remaining = content
        while remaining:
            cut = self.chunk_chars
            chunk = remaining[:cut]
            # 尽量在换行处断开
            newline = chunk.rfind("\n")
            if 0 < newline < len(chunk) - 100:
                chunk = chunk[: newline + 1]
            chunks.append(chunk)
            remaining = remaining[len(chunk):]
        return chunks

    async def send_note(
        self,
        message: IncomingMessage,
        title: str,
        markdown: str,
        note_path=None,
    ) -> None:
        body = markdown or ""
        if title:
            body = f"📝 {title}\n\n{body}"
        chunks = self._chunk(body)
        limit = self._max_passive(message)
        if len(chunks) > limit:
            chunks = chunks[:limit]
            chunks[-1] = chunks[-1] + "\n\n…（笔记较长，完整内容在电脑/手机）"
        for chunk in chunks:
            await self._send_passive(message, chunk)

    # ---------------------------------------------------------------- 生命周期

    async def stop(self) -> None:
        self._stop.set()
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._ws_task = None
        await self._http.aclose()


__all__ = [
    "C2C_MAX_PASSIVE",
    "DEFAULT_API_BASE",
    "GROUP_AND_C2C_INTENTS",
    "GROUP_MAX_PASSIVE",
    "QQBot",
    "TOKEN_URL",
]

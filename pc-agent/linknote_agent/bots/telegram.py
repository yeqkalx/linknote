"""Telegram Bot 适配器：getUpdates 长轮询（无需公网/Webhook）。

- ``getMe`` 回填 username（供群聊 @ 过滤）；
- ``getUpdates`` 长轮询持久化 offset（重启不重复/不漏）；
- 私聊全处理；群聊默认关闭，开启时只处理 @bot 或命令；
- 完成投递：``sendMessage`` 预览 + ``sendDocument`` 上传本机 ``notes/*.md``；
- 429 用 ``retry_after`` 退避；401/409 给出可读错误；任何日志都不打印 token。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from ..logging_setup import get_logger
from .base import BotAdapter, BotError, IncomingMessage, MessageCallback
from .state import BotState

logger = get_logger("bots.telegram")

DEFAULT_API_BASE = "https://api.telegram.org"
POLL_TIMEOUT_SECONDS = 25
SEND_MIN_INTERVAL_SECONDS = 1.0
TELEGRAM_TEXT_LIMIT = 4096


class TelegramBot(BotAdapter):
    name = "telegram"

    def __init__(
        self,
        token: str,
        *,
        allow_groups: bool = False,
        preview_chars: int = 3500,
        state: Optional[BotState] = None,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = DEFAULT_API_BASE,
        poll_timeout: int = POLL_TIMEOUT_SECONDS,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self._token = token or ""
        self.allow_groups = allow_groups
        self.preview_chars = max(200, int(preview_chars))
        self.state = state or BotState()
        self._api_base = api_base.rstrip("/")
        self._poll_timeout = max(1, int(poll_timeout))
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=f"{self._api_base}/bot{self._token}",
            timeout=httpx.Timeout(self._poll_timeout + 10, connect=10.0),
            transport=transport,
            headers={"User-Agent": "LinkNote-Agent-Bot/0.4"},
        )
        self.username = ""
        self._stop = asyncio.Event()
        self._poll_task: Optional[asyncio.Task] = None
        self._last_sent_at: dict[str, float] = {}
        self._last_bot_error = ""

    # ---------------------------------------------------------------- 基础

    def _sanitize(self, text: str) -> str:
        """日志/异常里绝不出现明文 bot token。"""
        message = str(text)
        if self._token:
            message = message.replace(self._token, "<telegram-token>")
        return message

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.post(f"/{method}", **kwargs)
        except httpx.HTTPError as exc:
            raise BotError(
                self._sanitize(f"无法连接 Telegram（{type(exc).__name__}）"),
                retriable=True,
            ) from exc
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BotError(
                f"Telegram 返回非 JSON（HTTP {resp.status_code}）",
                retriable=resp.status_code >= 500,
            ) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            payload = payload if isinstance(payload, dict) else {}
            description = self._sanitize(str(payload.get("description") or ""))
            error_code = int(payload.get("error_code") or resp.status_code or 0)
            params = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
            retry_after = float(params.get("retry_after") or 0.0)
            if error_code == 401:
                raise BotError(
                    "Telegram Bot Token 无效或已撤销（401），请检查 agent.json 的 telegram_bot_token",
                    retriable=False,
                )
            if error_code == 409:
                raise BotError(
                    "Telegram getUpdates 冲突（409）：可能有另一个程序/实例在使用同一个 bot，"
                    "或该 bot 设置了 Webhook",
                    retriable=False,
                )
            raise BotError(
                description or f"Telegram API 错误（HTTP {resp.status_code}）",
                retriable=error_code == 429 or error_code >= 500,
                retry_after=retry_after,
            )
        return payload.get("result")

    # ---------------------------------------------------------------- 账号 / 收发

    async def get_me(self) -> dict[str, Any]:
        result = await self._call("getMe")
        me = result if isinstance(result, dict) else {}
        self.username = str(me.get("username") or "")
        return me

    async def _pace(self, chat_id: str) -> None:
        """每聊天约 1 条/秒，避免触发 Telegram 频控。"""
        last = self._last_sent_at.get(chat_id)
        now = self._clock()
        if last is not None:
            wait = SEND_MIN_INTERVAL_SECONDS - (now - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_sent_at[chat_id] = self._clock()

    async def send_message(self, chat_id: str, text: str, *, reply_to: str = "") -> None:
        body: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": (text or "")[:TELEGRAM_TEXT_LIMIT],
            "disable_web_page_preview": True,
        }
        if reply_to:
            body["reply_to_message_id"] = int(reply_to)
        await self._pace(str(chat_id))
        await self._call("sendMessage", json=body)

    async def send_document(
        self,
        chat_id: str,
        document: Path | str,
        *,
        caption: str = "",
        filename: str = "",
    ) -> None:
        path = Path(document)
        data = path.read_bytes()
        name = filename or path.name
        await self._pace(str(chat_id))
        await self._call(
            "sendDocument",
            data={"chat_id": str(chat_id), "caption": (caption or "")[:1000]},
            files={"document": (name, data, "text/markdown; charset=utf-8")},
        )

    # ---------------------------------------------------------------- 适配器接口

    async def start(self, on_message: MessageCallback) -> None:
        try:
            await self.get_me()
        except BotError as exc:
            logger.error("Telegram 初始化失败：%s", exc.message)
            raise
        logger.info(
            "Telegram Bot 已就绪%s（getUpdates 长轮询）",
            f"：@{self.username}" if self.username else "",
        )
        self._poll_task = asyncio.create_task(self._poll_loop(on_message), name="telegram-poll")

    async def _poll_loop(self, on_message: MessageCallback) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                updates = await self._call(
                    "getUpdates",
                    json={
                        "offset": self.state.telegram_offset,
                        "timeout": self._poll_timeout,
                        "limit": 100,
                        "allowed_updates": ["message"],
                    },
                    timeout=self._poll_timeout + 10,
                )
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except BotError as exc:
                self._last_bot_error = exc.message
                if not exc.retriable:
                    logger.error("Telegram 轮询停止：%s", exc.message)
                    return
                wait = exc.retry_after or backoff
                logger.warning("Telegram 轮询失败（%s），%.1fs 后重试", exc.message, wait)
                await self._sleep(wait)
                backoff = min(backoff * 2, 60.0)
                continue
            except Exception as exc:  # noqa: BLE001 - 不让偶发异常杀死轮询
                logger.warning("Telegram 轮询异常：%s", self._sanitize(str(exc)))
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            updates = updates if isinstance(updates, list) else []
            if not updates:
                # 真实环境 getUpdates 会阻塞到超时；mock/异常场景下留一点让出时间，避免热循环。
                await self._sleep(0.05)
                continue
            for update in updates:
                if not isinstance(update, dict):
                    continue
                try:
                    update_id = int(update.get("update_id"))
                except (TypeError, ValueError):
                    continue
                if update_id < self.state.telegram_offset:
                    continue
                message = self._parse_update(update)
                try:
                    if message is not None:
                        await on_message(message)
                finally:
                    # 处理完再推进 offset；同一条消息若因崩溃重复出现，Runner 用 client_key 幂等。
                    self.state.update_offset(update_id)

    # ---------------------------------------------------------------- 解析/过滤

    def _parse_update(self, update: dict[str, Any]) -> Optional[IncomingMessage]:
        raw = update.get("message")
        if not isinstance(raw, dict):
            return None
        sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
        if sender.get("is_bot"):
            return None
        chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}
        chat_type = str(chat.get("type") or "")
        if chat_type not in ("private", "group", "supergroup"):
            return None
        text = str(raw.get("text") or raw.get("caption") or "").strip()
        if not text:
            return None

        allow = chat_type == "private"
        if chat_type in ("group", "supergroup"):
            if not self.allow_groups:
                return None
            mention = f"@{self.username}".lower() if self.username else ""
            is_command = text.startswith("/")
            if mention:
                mentioned = mention in text.lower()
                if mentioned:
                    # 去掉 @bot 前缀后保留命令/链接
                    text = text.replace(mention, "").replace(self.username, "").strip()
                    if not text:
                        text = "/help"
                allow = mentioned or is_command
            else:
                allow = is_command
        if not allow:
            return None

        chat_id = str(chat.get("id") or "")
        message_id = str(raw.get("message_id") or "")
        if not chat_id:
            return None
        return IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            chat_type="group" if chat_type != "private" else "private",
            message_id=message_id,
            text=text,
            reply_msg_id="",
            sender_id=str(sender.get("id") or ""),
            raw=raw,
        )

    # ---------------------------------------------------------------- 发送接口

    async def send_text(self, message: IncomingMessage, text: str) -> None:
        await self.send_message(message.chat_id, text)

    async def send_note(
        self,
        message: IncomingMessage,
        title: str,
        markdown: str,
        note_path: Optional[Path] = None,
    ) -> None:
        preview = markdown or ""
        if len(preview) > self.preview_chars:
            preview = preview[: self.preview_chars].rstrip() + "\n\n…（完整笔记见附件）"
        head = f"📝 {title}\n\n" if title else ""
        await self.send_message(message.chat_id, head + preview)
        if note_path is not None:
            path = Path(note_path)
            if path.exists():
                try:
                    await self.send_document(message.chat_id, path, caption=title)
                except BotError as exc:
                    logger.warning("Telegram 上传笔记文件失败（已发送预览）：%s", exc.message)
            else:
                logger.warning("Telegram 笔记文件不存在，跳过附件：%s", path)

    # ---------------------------------------------------------------- 生命周期

    async def stop(self) -> None:
        self._stop.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._poll_task = None
        await self._client.aclose()


__all__ = ["DEFAULT_API_BASE", "TelegramBot"]

"""Bot Runner：把 Telegram / QQ 入口接进本机 Agent。

职责（docs/bot-channels.md §4–§5）：
- 按配置启动适配器；
- 确保 bot 专用 Relay device token（首次/失效时用 agent token 调
  ``POST /api/v1/agent/devices`` 签发并保存；不碰手机 token）；
- 收到链接 → 提取/校验平台 → 用 bot device token 建任务 → 回复「已提交」；
- 轮询任务状态，成功后从本机 ``notes/`` 读 Markdown → 适配器回发；
- pending 持久化到 ``bot-state.json``，重启/QQ 回复窗口关闭后靠 ``/last`` 或下条消息补发；
- 本地限流（10 任务/小时/聊天）与命令 ``/start /help /status /last /retry``。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..config import AgentConfig, mask_secret
from ..logging_setup import get_logger
from ..outbox import Outbox
from ..platforms import (
    SUPPORTED_HINT,
    UnsupportedUrlError,
    ensure_supported_url,
    extract_first_url,
)
from ..relay_client import RelayClient, RelayError
from .base import BotAdapter, BotError, IncomingMessage, ReplyWindowClosed
from .device_relay import BotRelayClient, BotRelayError
from .state import (
    STATUS_SUBMITTED,
    STATUS_WINDOW_CLOSED,
    BotState,
    PendingTask,
)

logger = get_logger("bots.runner")

HELP_TEXT = (
    "发一个视频链接给我，我用这台电脑上的 BiliNote 生成 AI 笔记后回给你。\n\n"
    f"支持的平台：{SUPPORTED_HINT}\n\n"
    "命令：\n"
    "/status  最近任务\n"
    "/last    重发最近一篇笔记\n"
    "/retry <task_id>  重试失败任务\n"
    "/help    显示本帮助"
)

NOTE_READ_RETRIES = 4
NOTE_READ_INTERVAL_SECONDS = 0.5


def _safe_task_id(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id or ""))[:120] or "unknown"


class BotRunner:
    def __init__(
        self,
        config: AgentConfig,
        relay: RelayClient,
        *,
        outbox: Optional[Outbox] = None,
        state: Optional[BotState] = None,
        adapters: Optional[dict[str, BotAdapter]] = None,
        relay_factory: Optional[Callable[[str], BotRelayClient]] = None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self.config = config
        self.relay = relay
        self.outbox = outbox or Outbox(
            config.resolved_outbox_dir(), notes_dir=config.resolved_notes_dir()
        )
        self.state = state or BotState.load()
        self._adapters: dict[str, BotAdapter] = dict(adapters or {})
        self._injected_adapters = adapters is not None
        self._relay_factory = relay_factory or (
            lambda token: BotRelayClient(config.relay_base_url, token)
        )
        self._sleep = sleep
        self._clock = clock
        self._bot_relays: dict[str, BotRelayClient] = {}
        self._tasks: set[asyncio.Task] = set()
        self._stop: Optional[asyncio.Event] = None
        self._delivery_task: Optional[asyncio.Task] = None
        self._started = False

    # ================================================================ 生命周期

    @property
    def started(self) -> bool:
        return self._started

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        self._stop = stop
        await self.start()
        if not self._started:
            return
        try:
            await stop.wait()
        except asyncio.CancelledError:
            raise
        finally:
            await self._shutdown()

    async def start(self) -> None:
        """启动适配器 + 投递循环（幂等）。CLI 用 run()，测试可直接 start()。"""
        if self._started:
            return
        stop = self._stop or asyncio.Event()
        self._stop = stop
        self.state.prune_rate_limits()

        adapters = (
            dict(self._adapters)
            if self._injected_adapters
            else self._build_adapters()
        )
        if not adapters:
            logger.info("没有启用的 Bot 入口（telegram_enabled / qq_enabled 均为 false）")
            return

        usable: dict[str, BotAdapter] = {}
        for channel, adapter in adapters.items():
            try:
                token = await self.ensure_device_token(channel)
                self._bot_relays[channel] = self._relay_factory(token)
                await adapter.start(self._on_message)
                usable[channel] = adapter
                logger.info("Bot 入口已启动：%s", channel)
            except Exception as exc:  # noqa: BLE001 - 单个入口失败不影响其它入口
                logger.error(
                    "Bot 入口 %s 启动失败（已跳过）：%s",
                    channel,
                    mask_secret(getattr(exc, "message", str(exc))),
                )

        if not usable:
            logger.error("没有可用的 Bot 入口")
            return
        self._adapters = usable
        self._started = True
        self._delivery_task = asyncio.create_task(
            self._delivery_loop(stop), name="bot-delivery"
        )

    async def _shutdown(self) -> None:
        if self._delivery_task is not None:
            self._delivery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._delivery_task
            self._delivery_task = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        for adapter in self._adapters.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await adapter.stop()
        for client in self._bot_relays.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client.aclose()
        self._bot_relays.clear()
        self._started = False
        with contextlib.suppress(Exception):
            self.state.save()
        logger.info("Bot Runner 已停止")

    async def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        await self._shutdown()

    def _build_adapters(self) -> dict[str, BotAdapter]:
        """按配置创建真实适配器；缺少凭证时给出可读错误。"""
        from .qq import QQBot
        from .telegram import TelegramBot

        adapters: dict[str, BotAdapter] = {}
        cfg = self.config
        if cfg.telegram_enabled:
            if not cfg.telegram_bot_token:
                logger.error("telegram_enabled=true 但缺少 telegram_bot_token")
            else:
                adapters["telegram"] = TelegramBot(
                    cfg.telegram_bot_token,
                    allow_groups=cfg.telegram_allow_groups,
                    preview_chars=cfg.bot_telegram_preview_chars,
                    state=self.state,
                )
        if cfg.qq_enabled:
            if not cfg.qq_app_id or not cfg.qq_app_secret:
                logger.error("qq_enabled=true 但缺少 qq_app_id / qq_app_secret")
            else:
                adapters["qq"] = QQBot(
                    cfg.qq_app_id,
                    cfg.qq_app_secret,
                    state=self.state,
                    chunk_chars=cfg.bot_qq_chunk_chars,
                )
        return adapters

    # ================================================================ Device token

    async def ensure_device_token(self, channel: str) -> str:
        attr = "telegram_device_token" if channel == "telegram" else "qq_device_token"
        token = str(getattr(self.config, attr, "") or "")
        if token:
            return token
        return await self._mint_device_token(channel)

    async def _mint_device_token(self, channel: str) -> str:
        name = "QQ Bot"
        platform = "qq"
        if channel == "telegram":
            username = self.config.telegram_bot_username or ""
            name = f"Telegram @{username}" if username else "Telegram Bot"
            platform = "telegram"
        data = await self.relay.create_agent_device(name=name[:40], platform=platform)
        token = str(data.get("token") or "")
        device = data.get("device") if isinstance(data.get("device"), dict) else {}
        attr = "telegram_device_token" if channel == "telegram" else "qq_device_token"
        setattr(self.config, attr, token)
        with contextlib.suppress(Exception):
            self.config.save()
        logger.info(
            "已为本机 %s Bot 签发 Relay device token（device_id=%s token=%s）",
            channel,
            device.get("id") or "?",
            mask_secret(token),
        )
        return token

    async def _call_bot_relay(
        self,
        channel: str,
        op: Callable[[BotRelayClient], Awaitable[Any]],
    ) -> Any:
        """用 bot device token 调 Relay；401 时自动重新签发一次。"""
        client = self._bot_relays.get(channel)
        if client is None:
            raise BotError(f"{channel} Bot 入口未就绪")
        try:
            return await op(client)
        except BotRelayError as exc:
            if not exc.is_auth_error:
                raise
            token = await self._mint_device_token(channel)
            client = self._relay_factory(token)
            self._bot_relays[channel] = client
            return await op(client)

    # ================================================================ 消息入口

    async def _on_message(self, message: IncomingMessage) -> None:
        task = asyncio.create_task(
            self._handle_message(message), name=f"bot-msg-{message.channel}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _adapter_for(self, channel: str) -> Optional[BotAdapter]:
        return self._adapters.get(channel)

    async def _reply(self, message: IncomingMessage, text: str) -> None:
        adapter = self._adapters.get(message.channel)
        if adapter is None:
            return
        try:
            await adapter.send_text(message, text)
        except ReplyWindowClosed:
            logger.info("回复窗口已关闭（%s），等待下条消息/命令补发", message.channel)

    async def _handle_message(self, message: IncomingMessage) -> None:
        try:
            await self._flush_window_closed(message)
            text = (message.text or "").strip()
            if not text:
                return
            lowered = text.lower()
            if lowered.startswith("/start") or lowered.startswith("/help"):
                await self._reply(message, HELP_TEXT)
                return
            if lowered.startswith("/status"):
                await self._handle_status(message)
                return
            if lowered.startswith("/last"):
                await self._handle_last(message)
                return
            if lowered.startswith("/retry"):
                await self._handle_retry(message, text[len("/retry"):].strip())
                return

            url = extract_first_url(text)
            if not url:
                await self._reply(message, "没有识别到视频链接。\n\n" + HELP_TEXT)
                return
            try:
                _, real_url = ensure_supported_url(url)
            except UnsupportedUrlError as exc:
                await self._reply(message, str(exc))
                return
            await self._submit_task(message, real_url)
        except asyncio.CancelledError:
            raise
        except ReplyWindowClosed:
            logger.info("消息回复窗口已关闭（%s），不视为错误", message.channel)
        except Exception:  # noqa: BLE001 - 单条消息异常不能带走 Bot Runner
            logger.exception("处理 Bot 消息异常（%s）", message.channel)

    # ================================================================ 建任务

    def _client_key(self, message: IncomingMessage) -> str:
        """按平台消息 id 生成幂等 client_key（重启重复投递不会建两个任务）。"""
        raw = f"{message.channel}-{message.message_id}"
        key = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:64]
        if len(key) < 8:
            key = uuid.uuid4().hex[:16]
        return key

    async def _submit_task(self, message: IncomingMessage, url: str) -> None:
        chat_key = message.chat_key
        if not self.state.allow_task(chat_key, now=self._clock()):
            await self._reply(
                message, "⏳ 频率限制：同一聊天每小时最多 10 个任务，请稍后再试。"
            )
            return
        client_key = self._client_key(message)
        try:
            task = await self._call_bot_relay(
                message.channel,
                lambda client: client.create_task(url, client_key=client_key),
            )
        except (BotRelayError, RelayError) as exc:
            code = getattr(exc, "status_code", None)
            if code == 401:
                await self._reply(message, "❌ Bot 设备凭据失效且自动重签失败，请检查 Agent 配对。")
            else:
                await self._reply(message, f"❌ 提交任务失败：{exc.message}")
            return

        task_id = str(task.get("id") or "")
        short_id = task_id[:8]
        self.state.add_pending(
            PendingTask(
                task_id=task_id,
                channel=message.channel,
                chat_id=message.chat_id,
                chat_type=message.chat_type,
                reply_msg_id=message.reply_msg_id or message.message_id,
                created_at=self._clock(),
            )
        )
        logger.info("Bot 建任务 channel=%s task_id=%s url=%s", message.channel, task_id, url)
        await self._reply(message, f"已提交 #{short_id}，处理中…（完成后把笔记发给你）")

    # ================================================================ 投递循环

    async def _delivery_loop(self, stop: asyncio.Event) -> None:
        interval = max(0.05, float(self.config.bot_task_poll_seconds))
        while not stop.is_set():
            for pending in self.state.pendings(STATUS_SUBMITTED):
                if stop.is_set():
                    return
                try:
                    await self._poll_pending(pending)
                except asyncio.CancelledError:
                    raise
                except ReplyWindowClosed:
                    logger.debug("投递窗口关闭，留在 pending")
                except Exception:  # noqa: BLE001
                    logger.exception("轮询 Bot 任务 %s 异常", pending.task_id)
            await self._wait_stop(stop, interval)

    async def _poll_pending(self, pending: PendingTask) -> None:
        adapter = self._adapters.get(pending.channel)
        if adapter is None:
            return
        elapsed = self._clock() - pending.created_at
        if elapsed > float(self.config.bot_task_timeout_seconds):
            self.state.remove_pending(pending.task_id)
            await self._reply(self._context(pending), "⏰ 任务处理超时，已放弃等待。请稍后重试。")
            return

        try:
            task = await self._call_bot_relay(
                pending.channel, lambda client: client.get_task(pending.task_id)
            )
        except BotRelayError as exc:
            if exc.status_code == 404:
                self.state.remove_pending(pending.task_id)
                await self._reply(self._context(pending), "❌ 任务在 Relay 上已不存在。")
            else:
                logger.warning("查询 Bot 任务状态失败 task=%s：%s", pending.task_id, exc.message)
            return

        status = str(task.get("status") or "")
        if status == "failed":
            reason = str(task.get("error_message") or "生成失败")
            self.state.remove_pending(pending.task_id)
            await self._reply(self._context(pending), f"❌ 生成失败：{reason}")
            return
        if status != "succeeded":
            return

        found = await self._read_note(pending.task_id)
        if found is None:
            logger.warning("任务 %s 已完成但本机笔记尚未落盘，稍后重试", pending.task_id)
            return
        title, markdown = found
        note_path = self.outbox.note_path(pending.task_id)
        context = self._context(pending, text="")
        try:
            await adapter.send_note(context, title, markdown, note_path)
        except ReplyWindowClosed:
            self.state.update_pending(pending.task_id, status=STATUS_WINDOW_CLOSED)
            logger.info(
                "QQ 回复窗口关闭，笔记转 pending（task=%s，可 /last 补发）", pending.task_id
            )
            return
        except BotError as exc:
            logger.warning("发送笔记失败（保留 pending 稍后重试）：%s", exc.message)
            return
        self.state.set_last_note(pending.chat_key, pending.task_id)
        self.state.remove_pending(pending.task_id)
        logger.info(
            "Bot 笔记已投递 channel=%s task=%s title=%s", pending.channel, pending.task_id, title
        )

    async def _read_note(self, task_id: str) -> Optional[tuple[str, str]]:
        for attempt in range(NOTE_READ_RETRIES):
            found = self.outbox.note_for(task_id)
            if found is not None:
                return found
            if attempt < NOTE_READ_RETRIES - 1:
                await self._sleep(NOTE_READ_INTERVAL_SECONDS)
        return None

    def _context(self, pending: PendingTask, text: str = "") -> IncomingMessage:
        return IncomingMessage(
            channel=pending.channel,
            chat_id=pending.chat_id,
            chat_type=pending.chat_type,
            message_id=pending.reply_msg_id or pending.task_id,
            text=text,
            reply_msg_id=pending.reply_msg_id or pending.task_id,
        )

    # ================================================================ 命令

    async def _handle_status(self, message: IncomingMessage) -> None:
        try:
            tasks = await self._call_bot_relay(
                message.channel, lambda client: client.list_tasks(limit=5)
            )
        except (BotRelayError, RelayError) as exc:
            await self._reply(message, f"❌ 查询任务失败：{exc.message}")
            return
        if not tasks:
            await self._reply(message, "还没有任务。发一个视频链接试试。")
            return
        lines = ["最近任务："]
        for task in tasks:
            task_id = str(task.get("id") or "")
            status = str(task.get("status") or "?")
            title = str(task.get("title") or "")
            lines.append(f"#{task_id[:8]} [{status}] {title}".rstrip())
        await self._reply(message, "\n".join(lines))

    async def _handle_last(self, message: IncomingMessage) -> None:
        task_id = self.state.last_note_for(message.chat_key)
        if not task_id:
            pendings = self.state.pending_for_chat(message.chat_key)
            if pendings:
                task_id = pendings[-1].task_id
        if not task_id:
            await self._reply(message, "还没有可重发的笔记。")
            return
        found = await self._read_note(task_id)
        if found is None:
            await self._reply(message, "❌ 本机还没有这篇笔记（可能已被清理）。")
            return
        title, markdown = found
        adapter = self._adapters.get(message.channel)
        if adapter is None:
            return
        try:
            await adapter.send_note(message, title, markdown, self.outbox.note_path(task_id))
        except ReplyWindowClosed:
            await self._reply(message, "QQ 回复窗口已关闭，请过一会儿再发 /last。")
            return
        except BotError as exc:
            await self._reply(message, f"❌ 发送失败：{exc.message}")
            return
        self.state.set_last_note(message.chat_key, task_id)

    async def _handle_retry(self, message: IncomingMessage, arg: str) -> None:
        if not arg:
            await self._reply(message, "用法：/retry <task_id>（可只写前 8 位）")
            return
        if not self.state.allow_task(message.chat_key, now=self._clock()):
            await self._reply(message, "⏳ 频率限制：同一聊天每小时最多 10 个任务，请稍后再试。")
            return
        task_id = arg.split()[0]
        try:
            old = await self._call_bot_relay(
                message.channel, lambda client: client.get_task(task_id)
            )
        except (BotRelayError, RelayError) as exc:
            await self._reply(message, f"❌ 找不到任务：{exc.message}")
            return
        if str(old.get("status") or "") not in ("failed", "succeeded"):
            await self._reply(message, "任务还在处理中，等它结束再重试。")
            return
        try:
            new_task = await self._call_bot_relay(
                message.channel, lambda client: client.retry_task(task_id)
            )
        except (BotRelayError, RelayError) as exc:
            await self._reply(message, f"❌ 重试失败：{exc.message}")
            return
        new_id = str(new_task.get("id") or "")
        self.state.add_pending(
            PendingTask(
                task_id=new_id,
                channel=message.channel,
                chat_id=message.chat_id,
                chat_type=message.chat_type,
                reply_msg_id=message.reply_msg_id or message.message_id,
                created_at=self._clock(),
            )
        )
        await self._reply(message, f"已重新提交 #{new_id[:8]}，处理中…")

    # ================================================================ QQ 窗口补发

    async def _flush_window_closed(self, message: IncomingMessage) -> None:
        """QQ 窗口关闭后，用户下一条消息会带来新的被动回复窗口：先把 pending 笔记补发。"""
        pending_list = self.state.pending_for_chat(message.chat_key, STATUS_WINDOW_CLOSED)
        if not pending_list:
            return
        adapter = self._adapters.get(message.channel)
        if adapter is None:
            return
        for pending in list(pending_list):
            found = await self._read_note(pending.task_id)
            if found is None:
                continue
            title, markdown = found
            try:
                await adapter.send_note(
                    message, title, markdown, self.outbox.note_path(pending.task_id)
                )
            except ReplyWindowClosed:
                return
            except BotError as exc:
                logger.warning("补发 pending 笔记失败：%s", exc.message)
                return
            self.state.set_last_note(pending.chat_key, pending.task_id)
            self.state.remove_pending(pending.task_id)
            logger.info("已补发窗口关闭期间积压的笔记 task=%s", pending.task_id)

    # ================================================================ 工具

    async def _wait_stop(self, stop: asyncio.Event, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


__all__ = ["BotRunner", "HELP_TEXT"]

"""WebSocket 推送版单任务执行器（默认 ``run`` 模式）。

按 ``docs/realtime-protocol.md``：
- 不再轮询任务；服务端推 ``task`` 才处理（同一时刻只跑一个任务）；
- 任务过程用 WS 发 ``task_update``（running/progress/completed/failed，带可选 stage/message）；
- 完成时把正文保存在本地 ``notes/`` + ``outbox/``，然后发 ``task_note``（**不写 D1**）；
- 收 ``note_request`` 从本地重发 ``task_note``；收 ``ack_note`` 清理 outbox（保留 notes 文件）；
- WS 断开期间用 REST ``GET /api/v1/agent/tasks/next`` 兜底领取任务，WS 恢复后停用；
- 重连成功后（收到 hello）重发当前任务状态与所有未 ack 笔记。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .backend import BackendSupervisor
from .bilinote_client import BiliNoteClient
from .config import AgentConfig
from .logging_setup import get_logger
from .outbox import KIND_FAIL, Outbox
from .relay_client import MARKDOWN_MAX_BYTES, RelayClient, RelayError
from .relay_ws import (
    EVENT_AUTH_ERROR,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_RECONNECT_SCHEDULED,
    RealtimeClient,
)
from .stage_map import FAIL_MESSAGE_MAX_CHARS, translate_failure, truncate_message
from .task_pipeline import ProgressEvent, TaskOutcome, TaskPipeline, extract_title_and_markdown

logger = get_logger("ws_runner")

TITLE_MAX_CHARS = 200
PROGRESS_MESSAGE_MAX_CHARS = 200

TASK_QUEUE_MAX = 256
#: hello 后按 Relay 队列长度用 REST 补领 pending 的上限（契约 §11.3：hello 每次最多推 50 条）
MAX_REST_DRAIN = 100


class RealtimeAgentRunner:
    """WS 推送模式 Runner。

    ``ws_client`` 可注入（测试用 fake）；真实环境用 :class:`RealtimeClient`。
    """

    def __init__(
        self,
        config: AgentConfig,
        relay: RelayClient,
        bilinote: BiliNoteClient,
        *,
        backend: Optional[BackendSupervisor] = None,
        outbox: Optional[Outbox] = None,
        ws_client: Optional[Any] = None,
        sleep=asyncio.sleep,
        clock=None,
    ):
        self.config = config
        self.relay = relay
        self.bilinote = bilinote
        self.backend = backend or BackendSupervisor(config, bilinote=bilinote)
        self.outbox = outbox or Outbox(
            config.resolved_outbox_dir(), notes_dir=config.resolved_notes_dir()
        )
        self.ws = ws_client or RealtimeClient(
            config.relay_base_url,
            config.agent_token,
            ws_url=config.ws_url,
            ping_interval=config.ws_ping_interval_seconds,
            dead_timeout=config.ws_dead_timeout_seconds,
            hello_timeout=config.ws_hello_timeout_seconds,
            reconnect_delays=config.ws_reconnect_delays,
            jitter_ratio=config.ws_jitter_ratio,
            sleep=sleep,
            clock=clock or time.monotonic,
        )
        self._sleep = sleep
        self._clock = clock or time.monotonic

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=TASK_QUEUE_MAX)
        self._known_task_ids: set[str] = set()
        self._active_task_id: Optional[str] = None
        self._active_progress = 0
        self._active_stage = ""
        self._active_title = ""
        self._unsent_failures: dict[str, tuple[str, str]] = {}
        self._busy = False
        self._hello_seen = False
        self._stop: Optional[asyncio.Event] = None
        self._auth_error_logged = False

    # ================================================================ 状态

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def connected(self) -> bool:
        return bool(getattr(self.ws, "connected", False))

    # ================================================================ 主循环

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        self._stop = stop

        migrated = self.outbox.migrate_legacy_complete_entries()
        self._migrate_legacy_failures()
        self.outbox.prune_notes(float(self.config.note_retention_days) * 86400.0)

        logger.info(
            "Agent 启动（WebSocket 推送模式）：ws_url=%s relay=%s backend=%s 模型=%s/%s",
            getattr(self.ws, "url", "?"),
            self.config.relay_base_url,
            self.config.backend_base_url,
            self.config.provider_id,
            self.config.model_name,
        )
        if migrated:
            logger.warning("旧 REST outbox 已迁移 %d 条笔记到 WS 待 ack 队列", migrated)

        tasks: list[asyncio.Task] = [
            asyncio.create_task(self._ws_run(), name="ws-client"),
            asyncio.create_task(self._worker_loop(stop), name="task-worker"),
            asyncio.create_task(self._event_loop(stop), name="ws-events"),
            asyncio.create_task(self._fallback_loop(stop), name="rest-fallback"),
        ]
        if self.config.rest_heartbeat_enabled:
            tasks.append(asyncio.create_task(self._rest_heartbeat_loop(stop), name="rest-heartbeat"))
        try:
            await stop.wait()
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await self._close_ws()
            await self.backend.close()
            logger.info("Agent 已停止")

    async def _ws_run(self) -> None:
        try:
            await self.ws.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 客户端自身已兜底重连，这里只防御
            logger.exception("WebSocket 客户端异常退出")

    async def _worker_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                await self.process_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单任务异常不能带走进程
                logger.exception("处理推送任务时出现未预期异常")

    async def _event_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                message = await asyncio.wait_for(self.ws.events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle_ws_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("处理 WebSocket 消息异常：%r", message)

    # ================================================================ 消息分发

    async def handle_ws_message(self, message: dict[str, Any]) -> None:
        """处理一条 WS 消息/事件（公开方法便于单测直接驱动）。"""
        msg_type = str(message.get("type") or "")

        if msg_type == EVENT_CONNECTED:
            logger.info("实时连接已建立（agent_id=%s）", message.get("agent_id") or "?")
            return

        if msg_type == EVENT_DISCONNECTED:
            self._hello_seen = False
            logger.warning(
                "实时连接断开：%s；REST 兜底接管任务领取，WebSocket 自动重连",
                message.get("reason") or "未知原因",
            )
            return

        if msg_type == EVENT_AUTH_ERROR:
            logger.error(
                "Relay 拒绝了 WebSocket 认证：%s。请重新运行 pair 命令配对（进程会继续重连）。",
                message.get("message") or "",
            )
            return

        if msg_type == EVENT_RECONNECT_SCHEDULED:
            logger.debug("WebSocket 计划重连：%s", message)
            return

        if msg_type == "hello":
            self._hello_seen = True
            logger.info(
                "收到 WebSocket hello（agent_id=%s server_time=%s）",
                message.get("agent_id") or "?",
                message.get("server_time") or "?",
            )
            await self.on_ws_ready()
            return

        if msg_type == "task":
            task = message.get("task")
            if isinstance(task, dict):
                await self._enqueue_task(task)
            else:
                logger.warning("收到 task 消息但缺少 task 对象：%r", message)
            return

        if msg_type == "note_request":
            task_id = str(message.get("task_id") or "")
            if task_id:
                await self.resend_note(task_id)
            return

        if msg_type == "ack_note":
            task_id = str(message.get("task_id") or "")
            if task_id and not self.outbox.ack_note(task_id):
                logger.debug("收到 ack_note 但 outbox 已无对应记录 task_id=%s", task_id)
            return

        if msg_type == "phone_online":
            logger.info("手机%s", "已上线" if message.get("online") else "离线")
            return

        if msg_type == "error":
            logger.warning(
                "Relay 返回 WS 错误：code=%s message=%s",
                message.get("code") or "?",
                message.get("message") or "",
            )
            return

        logger.debug("忽略未知 WebSocket 消息类型：%r", msg_type)

    # ================================================================ 连接就绪

    async def on_ws_ready(self) -> None:
        """收到 hello（首次连接/重连）后：重发当前状态 + 所有未 ack 笔记 + 未送达失败。"""
        if self._active_task_id is not None:
            await self._send_running_update(
                self._active_task_id,
                self._active_progress,
                self._active_stage,
                "连接已恢复，任务继续处理中",
                title=self._active_title,
            )

        resent = await self.resend_pending_notes()
        if resent:
            logger.info("重连后已重发 %d 条未 ack 笔记", resent)

        for task_id, (error, error_code) in list(self._unsent_failures.items()):
            if await self._send_failed_update(task_id, error, error_code):
                self._unsent_failures.pop(task_id, None)

        # 契约 §11.3：hello 后 Relay 每次最多补发 50 条 queued/running，其余用 REST 兜底补领。
        await self.pull_pending_via_rest()

    async def pull_pending_via_rest(self) -> int:
        """hello 后用 REST 补领经过 50 条推送上限后剩余的排队任务（去重后入队）。

        只在「收到 hello」和「WS 断开兜底」时调用，不做空闲轮询；WS 推送与 REST 领取
        用 ``_known_task_ids`` 去重，同一任务不会被处理两次。
        """
        if not self.config.rest_fallback:
            return 0
        try:
            me = await self.relay.me()
        except RelayError as exc:
            logger.warning("hello 后查询待处理队列失败（不影响实时通道）：%s", exc.message)
            return 0
        queue = me.get("queue") if isinstance(me.get("queue"), dict) else {}
        try:
            queued = int(queue.get("queued") or 0)
        except (TypeError, ValueError):
            queued = 0
        if queued <= 0:
            return 0
        pulled = 0
        for _ in range(min(queued, MAX_REST_DRAIN)):
            try:
                task = await self.relay.next_task()
            except RelayError as exc:
                logger.warning("hello 后 REST 补领任务失败：%s", exc.message)
                break
            if not task:
                break
            await self._enqueue_task(task)
            pulled += 1
        if pulled:
            logger.info(
                "hello 后 Relay 队列仍有 queued≈%d，已用 REST 兜底补领 %d 个任务",
                queued,
                pulled,
            )
        return pulled

    async def resend_pending_notes(self) -> int:
        """把所有未 ack 笔记的 ``task_note`` 重发一遍（Relay 幂等）。"""
        sent = 0
        for entry in self.outbox.pending_notes():
            title = str(entry.payload.get("title") or "未命名笔记")
            markdown = str(entry.payload.get("markdown") or "")
            if await self._send_task_note(entry.task_id, title, markdown):
                sent += 1
        return sent

    async def resend_note(self, task_id: str) -> bool:
        """收到 ``note_request``：从 outbox/notes 找到正文并重发 ``task_note``。"""
        found = self.outbox.note_for(task_id)
        if found is None:
            logger.warning(
                "手机请求笔记，但本地没有 task_id=%s 的正文（可能已超过保留期），无法重发",
                task_id,
            )
            return False
        title, markdown = found
        ok = await self._send_task_note(task_id, title, markdown)
        if ok:
            logger.info("已按 note_request 重发笔记 task_id=%s（%d 字）", task_id, len(markdown))
        return ok

    # ================================================================ 任务队列

    async def _enqueue_task(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            logger.warning("收到缺少 id 的任务推送，已忽略：%r", task)
            return
        if task_id == self._active_task_id or task_id in self._known_task_ids:
            logger.debug("忽略重复任务推送 task_id=%s", task_id)
            return
        self._known_task_ids.add(task_id)
        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull:  # pragma: no cover - 正常不会到 256
            logger.error("任务队列已满，丢弃任务推送 task_id=%s", task_id)
            return
        logger.info(
            "收到任务推送 task_id=%s url=%s platform=%s",
            task_id,
            task.get("url"),
            task.get("platform"),
        )

    # ================================================================ 执行任务

    async def process_task(self, task: dict[str, Any]) -> None:
        """执行一个被推送的任务（公开方法便于单测直接调用）。"""
        task_id = str(task.get("id") or "").strip()
        raw_url = str(task.get("url") or "").strip()
        if not task_id:
            logger.error("任务缺少 id，已忽略：%r", task)
            return

        # 上次进程崩溃/断线前已经生成过笔记：直接重发，不重复调用 BiliNote。
        if self.outbox.has_note(task_id):
            found = self.outbox.note_for(task_id)
            if found is not None:
                title, markdown = found
                logger.info("任务 %s 本地已有笔记，跳过重复生成，重发 task_note", task_id)
                await self._send_completed_update(task_id, title)
                await self._send_task_note(task_id, title, markdown)
                return

        self._busy = True
        self._active_task_id = task_id
        self._active_progress = 0
        self._active_stage = "parsing"
        self._active_title = ""
        logger.info("开始处理任务 task_id=%s url=%s", task_id, raw_url)
        try:
            pipeline = TaskPipeline(
                self.config,
                self.bilinote,
                backend=self.backend,
                sleep=self._sleep,
                clock=self._clock,
            )

            async def on_progress(event: ProgressEvent) -> bool:
                self._active_progress = event.progress
                self._active_stage = event.stage
                return await self._send_running_update(
                    task_id, event.progress, event.stage, event.message
                )

            async def on_lease(relay_task_id: str) -> Any:
                return await self.relay.task_heartbeat(relay_task_id)

            outcome = await pipeline.execute(
                task_id,
                raw_url,
                on_progress=on_progress,
                on_lease=on_lease,
                platform_hint=str(task.get("platform") or ""),
            )
            if outcome.kind == "completed":
                await self._deliver_completed(task_id, outcome)
            elif outcome.kind == "failed" and outcome.failure is not None:
                await self._deliver_failed(
                    task_id, outcome.failure.code, outcome.failure.message
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底把失败告诉手机
            logger.exception("处理任务 %s 时出现未预期异常", task_id)
            failure = translate_failure(f"{type(exc).__name__}: {exc}")
            await self._deliver_failed(task_id, failure.code, failure.message)
        finally:
            self._busy = False
            self._active_task_id = None
            self._active_progress = 0
            self._active_stage = ""

    async def _deliver_completed(self, task_id: str, outcome: TaskOutcome) -> None:
        title, markdown = extract_title_and_markdown(
            outcome.result or {}, max_title=TITLE_MAX_CHARS
        )
        if not markdown:
            await self._deliver_failed(
                task_id,
                "empty_note",
                "BiliNote 任务显示成功，但没有返回 Markdown 内容。"
                "请到 BiliNote 界面查看任务详情后重试。",
            )
            return

        size = len(markdown.encode("utf-8"))
        if size > MARKDOWN_MAX_BYTES:
            # 仅本地留档，不进入待重发队列（重发也会被 Relay 拒绝）。
            saved = self.outbox.save_note_locally(task_id, markdown)
            await self._deliver_failed(
                task_id,
                "note_too_large",
                f"笔记超过 1.5 MiB 限制（{size} 字节），无法通过 Relay 回传。"
                + (f"正文已保存在电脑本地：{saved}" if saved else "本地保存也失败了。"),
            )
            return

        # 先落盘再发送：即使紧跟着断网/崩溃，笔记也不会丢，重连后会重发。
        self.outbox.add_note(
            task_id,
            title,
            markdown,
            duration_seconds=outcome.duration_seconds,
            meta={"platform": outcome.platform, "url": outcome.url},
        )
        self._active_title = title
        sent_update = await self._send_completed_update(task_id, title)
        sent_note = await self._send_task_note(task_id, title, markdown)
        logger.info(
            "任务完成 task_id=%s title=%s markdown=%d 字 duration=%s 秒"
            "（本地已保存，等待手机 ack；completed_update=%s task_note=%s）",
            task_id,
            title,
            len(markdown),
            outcome.duration_seconds,
            sent_update,
            sent_note,
        )

    async def _deliver_failed(self, task_id: str, code: str, message: str) -> None:
        text = truncate_message(message or "生成失败", FAIL_MESSAGE_MAX_CHARS)
        ok = await self._send_failed_update(task_id, text, code)
        if not ok:
            # WS 断线：记下来，重连 hello 后补发。
            self._unsent_failures[task_id] = (text, code)
        logger.error(
            "任务失败 task_id=%s code=%s message=%s（WS 已送达=%s）", task_id, code, text, ok
        )

    # ================================================================ 发送

    async def _send_running_update(
        self,
        task_id: str,
        progress: int,
        stage: str,
        message: str,
        *,
        title: str = "",
    ) -> bool:
        payload: dict[str, Any] = {
            "type": "task_update",
            "task_id": task_id,
            "status": "running",
            "progress": max(0, min(100, int(progress))),
        }
        if stage:
            payload["stage"] = stage
        if message:
            payload["message"] = truncate_message(message, PROGRESS_MESSAGE_MAX_CHARS)
        if title:
            payload["title"] = truncate_message(title, TITLE_MAX_CHARS)
        return await self.ws.send(payload)

    async def _send_completed_update(self, task_id: str, title: str) -> bool:
        return await self.ws.send(
            {
                "type": "task_update",
                "task_id": task_id,
                "status": "completed",
                "progress": 100,
                "title": truncate_message(title or "未命名笔记", TITLE_MAX_CHARS),
            }
        )

    async def _send_failed_update(
        self, task_id: str, error: str, error_code: str = ""
    ) -> bool:
        payload: dict[str, Any] = {
            "type": "task_update",
            "task_id": task_id,
            "status": "failed",
            "error": truncate_message(error or "生成失败", FAIL_MESSAGE_MAX_CHARS),
        }
        if error_code:
            # 契约 §11.2 增量字段：error_code（≤64），Relay 会写入 D1 并推给手机。
            payload["error_code"] = truncate_message(error_code, 64)
        return await self.ws.send(payload)

    async def _send_task_note(self, task_id: str, title: str, markdown: str) -> bool:
        ok = await self.ws.send(
            {
                "type": "task_note",
                "task_id": task_id,
                "title": truncate_message(title or "未命名笔记", TITLE_MAX_CHARS),
                "markdown": markdown,
            }
        )
        if not ok:
            logger.warning("task_note 发送失败（连接断开），笔记仍在本地，重连后重发 task_id=%s", task_id)
        return ok

    # ================================================================ REST 兜底 / 心跳

    async def rest_fallback_poll_once(self) -> Optional[dict[str, Any]]:
        """WS 断开时用 REST 领一个任务；WS 已连接时不动作（避免与推送重复处理）。"""
        if self.connected:
            return None
        task = await self.relay.next_task()
        if task:
            await self._enqueue_task(task)
        return task

    async def _fallback_loop(self, stop: asyncio.Event) -> None:
        if not self.config.rest_fallback:
            logger.info("REST 兜底已禁用（rest_fallback=false）")
            return
        while not stop.is_set():
            if not self.connected:
                try:
                    await self.rest_fallback_poll_once()
                except RelayError as exc:
                    if exc.is_auth_error:
                        if not self._auth_error_logged:
                            logger.error("REST 兜底领取任务失败（配对失效）：%s", exc.message)
                            self._auth_error_logged = True
                    elif exc.retriable:
                        logger.warning("REST 兜底领取任务失败，稍后重试：%s", exc.message)
                    else:
                        logger.warning("REST 兜底领取任务失败：%s", exc.message)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("REST 兜底出现未预期异常")
                await self._wait_stop(
                    stop, max(0.1, float(self.config.fallback_poll_interval_seconds))
                )
            else:
                await self._wait_stop(stop, 0.5)

    async def _rest_heartbeat_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.relay.heartbeat(self.config.version, busy=self.busy)
                self._auth_error_logged = False
            except RelayError as exc:
                if exc.is_auth_error:
                    logger.error("REST 心跳失败（配对失效）：%s", exc.message)
                elif exc.retriable:
                    logger.warning("REST 心跳失败（Relay 暂时不可达）：%s", exc.message)
                else:
                    logger.warning("REST 心跳失败：%s", exc.message)
            except Exception:  # noqa: BLE001
                logger.exception("REST 心跳出现未预期异常")
            if stop.is_set():
                return
            await self._wait_stop(
                stop, max(0.05, float(self.config.heartbeat_interval_seconds))
            )

    # ================================================================ 收尾

    def _migrate_legacy_failures(self) -> None:
        """旧 REST 模式的 fail outbox 记录：转成待补发的 WS failed 更新。"""
        for entry in list(self.outbox.entries()):
            if entry.kind != KIND_FAIL:
                continue
            task_id = entry.task_id
            message = str(entry.payload.get("message") or "生成失败")
            code = str(entry.payload.get("code") or "bilinote_error")
            self._unsent_failures.setdefault(task_id, (message, code))
            self.outbox.remove(entry)
            logger.warning("旧 REST fail 记录已转为 WS 待补发 failed task_id=%s", task_id)

    async def _wait_stop(self, stop: asyncio.Event, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def _close_ws(self) -> None:
        try:
            await self.ws.stop()
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        await self._close_ws()
        await self.backend.close()


__all__ = ["RealtimeAgentRunner"]

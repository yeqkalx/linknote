"""单任务串行执行器：领任务 → 确保 BiliNote 后端 → 提交 → 轮询进度 → complete/fail。

关键行为（对齐 docs/api-contract.md）：
- 每 3–5 秒 ``GET /api/v1/agent/tasks/next``；同一时刻只跑一个任务；
- 每 30 秒 ``POST /agent/heartbeat``；长任务每 5 分钟 ``POST /agent/tasks/{id}/heartbeat``
  续租（进度回传本身也会顺延租约，双保险）；
- 状态变化立刻回传 progress，同状态最多每 10 秒一次；
- 成功后取 ``result.markdown`` + ``result.audio_meta.title`` 回传 complete；
- 失败把常见错误翻成中文回传 fail；
- Relay 暂时不可达：结果写本地 outbox，下个循环/下次启动补传。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .backend import BackendError, BackendSupervisor
from .bilinote_client import BiliNoteClient, BiliNoteError
from .config import AgentConfig
from .logging_setup import get_logger
from .outbox import Outbox
from .platforms import UnsupportedUrlError, ensure_supported_url, resolve_short_link
from .relay_client import RelayClient, RelayError
from .stage_map import (
    FAIL_MESSAGE_MAX_CHARS,
    format_elapsed,
    map_status,
    normalize_status,
    translate_failure,
    truncate_message,
)

logger = get_logger("runner")

TITLE_MAX_CHARS = 200


class TaskLostError(Exception):
    """Relay 表示这个任务已经不再归本 Agent 处理（404/409，例如租约过期被回收）。"""


class AgentRunner:
    def __init__(
        self,
        config: AgentConfig,
        relay: RelayClient,
        bilinote: BiliNoteClient,
        *,
        backend: Optional[BackendSupervisor] = None,
        outbox: Optional[Outbox] = None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self.config = config
        self.relay = relay
        self.bilinote = bilinote
        self.backend = backend or BackendSupervisor(config, bilinote=bilinote)
        self.outbox = outbox or Outbox(
            config.resolved_outbox_dir(), notes_dir=config.resolved_notes_dir()
        )
        self._sleep = sleep
        self._clock = clock
        self._busy = False
        self._stop: Optional[asyncio.Event] = None
        self._auth_error_logged = False

    # ================================================================ 主循环

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """常驻轮询。``stop_event`` 被 set 后退出（测试/CLI 优雅关闭用）。"""
        stop = stop_event or asyncio.Event()
        self._stop = stop

        logger.info(
            "Agent 开始轮询任务：relay=%s backend=%s 模型=%s/%s",
            self.config.relay_base_url,
            self.config.backend_base_url,
            self.config.provider_id,
            self.config.model_name,
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(stop), name="agent-heartbeat")
        try:
            while not stop.is_set():
                try:
                    processed = await self.poll_once()
                except RelayError as exc:
                    await self._handle_relay_error(exc, stop)
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 常驻进程不允许被单次异常带走
                    logger.exception("轮询循环出现未预期异常，10 秒后继续")
                    await self._wait_stop(stop, 10.0)
                    continue

                if processed:
                    continue  # 有任务处理完立刻继续领（契约：有任务时立即继续）
                await self._wait_stop(stop, max(0.1, float(self.config.poll_interval_seconds)))
        finally:
            stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            logger.info("Agent 轮询循环已停止")

    async def poll_once(self) -> bool:
        """补传 outbox → 领一个任务并执行；没有任务返回 False。"""
        await self._flush_outbox()
        task = await self.relay.next_task()
        if not task:
            return False
        await self.execute_task(task)
        return True

    async def _handle_relay_error(self, exc: RelayError, stop: asyncio.Event) -> None:
        if exc.is_auth_error:
            if not self._auth_error_logged:
                logger.error(
                    "配对已失效（%s）。请重新运行 pair 命令配对；本进程会继续重试。",
                    exc.message,
                )
                self._auth_error_logged = True
            else:
                logger.debug("Relay 认证仍然失败：%s", exc.message)
            await self._wait_stop(stop, 60.0)
        elif exc.retriable:
            logger.warning("Relay 暂时不可达，稍后重试：%s", exc.message)
            await self._wait_stop(stop, 5.0)
        else:
            logger.error("Relay 返回错误：%s", exc.message)
            await self._wait_stop(stop, 5.0)

    # ================================================================ 心跳

    async def _heartbeat_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.relay.heartbeat(self.config.version, busy=self._busy)
                self._auth_error_logged = False
            except RelayError as exc:
                if exc.is_auth_error:
                    logger.error("心跳失败：%s", exc.message)
                elif exc.retriable:
                    logger.warning("心跳失败（Relay 暂时不可达）：%s", exc.message)
                else:
                    logger.warning("心跳失败：%s", exc.message)
            except Exception:  # noqa: BLE001
                logger.exception("心跳出现未预期异常")
            if stop.is_set():
                return
            await self._wait_stop(stop, max(0.05, float(self.config.heartbeat_interval_seconds)))

    async def _lease_heartbeat_loop(self, task_id: str) -> None:
        interval = max(0.05, float(self.config.lease_heartbeat_interval_seconds))
        while True:
            await asyncio.sleep(interval)
            try:
                data = await self.relay.task_heartbeat(task_id)
                logger.debug("租约续期成功 task_id=%s %s", task_id, data)
            except RelayError as exc:
                # 续租失败不打断生成：进度回传也会顺延租约；真过期了 progress 会 404/409。
                logger.warning("租约续期失败 task_id=%s：%s", task_id, exc.message)
            except Exception:  # noqa: BLE001
                logger.exception("租约续期出现未预期异常")

    # ================================================================ outbox

    async def _flush_outbox(self) -> None:
        entries = self.outbox.pending()
        if not entries:
            return
        logger.info("发现 %d 条待补传记录，尝试补传…", len(entries))
        try:
            stats = await self.outbox.flush(self.relay, entries=entries)
        except Exception:  # noqa: BLE001 - flush 内部已兜底，这里防御
            logger.exception("补传 outbox 出现异常")
            return
        if stats.delivered:
            logger.info("outbox 补传完成：成功 %d 条", stats.delivered)
        if stats.notes_saved:
            logger.warning("有 %d 条笔记无法回传，已保存到本地：%s", stats.notes_saved, self.outbox.notes_dir)

    # ================================================================ 单任务

    async def execute_task(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("id") or "").strip()
        raw_url = str(task.get("url") or "").strip()
        if not task_id:
            logger.error("Relay 返回的任务缺少 id，已忽略：%r", task)
            return

        self._busy = True
        started = self._clock()
        logger.info("领到任务 task_id=%s url=%s platform=%s", task_id, raw_url, task.get("platform"))
        try:
            try:
                platform, url = ensure_supported_url(raw_url)
            except UnsupportedUrlError as exc:
                await self._fail(task_id, "unsupported_platform", str(exc))
                return

            relay_platform = str(task.get("platform") or "").strip()
            if relay_platform and relay_platform != platform:
                logger.warning(
                    "Relay 标记平台 %s 与链接实际平台 %s 不一致，按实际平台处理",
                    relay_platform,
                    platform,
                )

            await self._progress(
                task_id, "parsing", 1, "已领取任务，正在准备处理"
            )
            timeout_s = int(float(self.config.backend_start_timeout or 90))
            await self._progress(
                task_id,
                "parsing",
                2,
                f"正在检查 BiliNote 后端，必要时自动启动（最多 {timeout_s} 秒）",
            )
            try:
                await self.backend.ensure_running()
            except BackendError as exc:
                await self._fail(
                    task_id, "backend_unavailable", f"BiliNote 后端不可用：{exc.message}"
                )
                return

            real_url = await resolve_short_link(url)
            if real_url != url:
                logger.info("短链展开：%s -> %s", url, real_url)

            try:
                bilinote_task_id = await self.bilinote.generate_note(
                    real_url,
                    platform,
                    provider_id=self.config.provider_id,
                    model_name=self.config.model_name,
                    quality=self.config.quality,
                    style=self.config.style,
                    screenshot=self.config.screenshot,
                    link=self.config.insert_video_links,
                )
            except BiliNoteError as exc:
                logger.error("提交 BiliNote 任务失败 task_id=%s：%s", task_id, exc.message)
                failure = translate_failure(exc.message)
                await self._fail(task_id, failure.code, failure.message)
                return

            logger.info(
                "已提交 BiliNote 任务 relay_task=%s bilinote_task=%s platform=%s",
                task_id,
                bilinote_task_id,
                platform,
            )
            await self._await_bilinote(task_id, bilinote_task_id, platform, url, started)
        except asyncio.CancelledError:
            raise
        except TaskLostError as exc:
            logger.warning("任务 %s 已不再归本 Agent 处理：%s", task_id, exc)
        except Exception as exc:  # noqa: BLE001 - 兜底回传，不能让用户一直看到「进行中」
            logger.exception("处理任务 %s 时出现未预期异常", task_id)
            failure = translate_failure(f"{type(exc).__name__}: {exc}")
            await self._fail(task_id, failure.code, failure.message)
        finally:
            self._busy = False

    async def _await_bilinote(
        self,
        relay_task_id: str,
        bilinote_task_id: str,
        platform: str,
        url: str,
        started: float,
    ) -> None:
        lease_task = asyncio.create_task(
            self._lease_heartbeat_loop(relay_task_id), name=f"lease-{relay_task_id}"
        )
        try:
            last_status = ""
            last_progress_at = 0.0
            status_errors = 0
            while True:
                await asyncio.sleep(max(0.01, float(self.config.task_status_poll_interval_seconds)))
                elapsed = self._clock() - started
                if elapsed > float(self.config.task_timeout_seconds):
                    await self._fail(
                        relay_task_id,
                        "task_timeout",
                        f"处理超时（已等待 {format_elapsed(elapsed)}），已放弃等待。"
                        "视频可能过长或网络较慢，请稍后重试。",
                    )
                    return

                try:
                    status_payload = await self.bilinote.get_task_status(bilinote_task_id)
                except BiliNoteError as exc:
                    status_errors += 1
                    logger.warning(
                        "查询 BiliNote 任务状态失败（第 %d 次）：%s", status_errors, exc.message
                    )
                    if status_errors >= max(1, int(self.config.max_status_errors)):
                        await self._fail(
                            relay_task_id,
                            "backend_unavailable",
                            "查询 BiliNote 任务状态连续失败，后端可能已经退出。"
                            f"请检查 BiliNote 后端与日志。（最后错误：{exc.message}）",
                        )
                        return
                    continue
                status_errors = 0

                status = normalize_status(status_payload.get("status"))
                if status == "FAILED":
                    reason = str(
                        status_payload.get("message")
                        or status_payload.get("result")
                        or "BiliNote 返回任务失败"
                    )
                    logger.error("BiliNote 任务失败 bilinote_task=%s：%s", bilinote_task_id, reason)
                    failure = translate_failure(reason)
                    await self._fail(relay_task_id, failure.code, failure.message)
                    return

                if status == "SUCCESS":
                    result = status_payload.get("result")
                    if not isinstance(result, dict):
                        result = {}
                    await self._complete(relay_task_id, result, platform, url)
                    return

                update = map_status(status, elapsed)
                if update is None:
                    logger.debug("BiliNote 返回未映射状态 %r，继续等待", status)
                    continue

                now = self._clock()
                if status != last_status or (
                    now - last_progress_at
                ) >= float(self.config.progress_min_interval_seconds):
                    sent = await self._progress(
                        relay_task_id, update.stage, update.progress, update.message
                    )
                    if sent:
                        last_status = status
                        last_progress_at = now
        finally:
            lease_task.cancel()
            try:
                await lease_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ================================================================ 回传

    async def _progress(
        self,
        task_id: str,
        stage: str,
        progress: int,
        message: str,
    ) -> bool:
        """回传进度。返回是否成功；404/409 说明任务已不属于本 Agent（抛 TaskLostError）。"""
        try:
            await self.relay.progress(task_id, stage, progress, message)
            return True
        except RelayError as exc:
            if exc.status_code in (404, 409) and not exc.is_auth_error:
                raise TaskLostError(exc.message) from exc
            if exc.is_auth_error:
                if not self._auth_error_logged:
                    logger.error("进度回传失败（配对失效）：%s", exc.message)
                    self._auth_error_logged = True
                return False
            if exc.retriable:
                logger.warning("进度回传失败（稍后会重试）：%s", exc.message)
                return False
            logger.warning("进度回传失败：%s", exc.message)
            return False

    async def _complete(
        self,
        task_id: str,
        result: dict[str, Any],
        platform: str,
        url: str,
    ) -> None:
        markdown = str(result.get("markdown") or "").strip()
        audio_meta = result.get("audio_meta")
        if not isinstance(audio_meta, dict):
            audio_meta = {}
        title = truncate_message(
            str(audio_meta.get("title") or "").strip() or "未命名笔记", TITLE_MAX_CHARS
        )
        duration_seconds: Optional[int] = None
        raw_duration = audio_meta.get("duration")
        try:
            if raw_duration is not None and float(raw_duration) > 0:
                duration_seconds = int(float(raw_duration))
        except (TypeError, ValueError):
            duration_seconds = None
        meta = {"platform": platform, "url": url}

        if not markdown:
            await self._fail(
                task_id,
                "empty_note",
                "BiliNote 任务显示成功，但没有返回 Markdown 内容。"
                "请到 BiliNote 界面查看任务详情后重试。",
            )
            return

        try:
            await self.relay.complete(
                task_id,
                title=title,
                markdown=markdown,
                duration_seconds=duration_seconds,
                meta=meta,
            )
        except RelayError as exc:
            if exc.retriable and not exc.is_auth_error:
                self.outbox.add_complete(
                    task_id,
                    title=title,
                    markdown=markdown,
                    duration_seconds=duration_seconds,
                    meta=meta,
                )
                logger.warning(
                    "任务 %s 已生成笔记但 Relay 暂时不可达（%s），已写入本地 outbox，稍后补传",
                    task_id,
                    exc.message,
                )
                return
            saved = self.outbox.save_note_locally(task_id, markdown)
            if exc.code == "markdown_too_large":
                # Relay 的 1.5 MB 是硬限制，本地已留档；明确把任务置失败，让手机端看到原因。
                await self._fail(
                    task_id,
                    "note_too_large",
                    "笔记超过 Relay 的 1.5 MB 限制，无法回传。"
                    + (
                        f"已保存到电脑本地：{saved}，可在电脑上打开。"
                        if saved
                        else "本地保存也失败了，请到 BiliNote 界面查看。"
                    ),
                )
                return
            logger.error(
                "任务 %s 的笔记无法回传（%s）。%s",
                task_id,
                exc.message,
                f"已保存到本地：{saved}" if saved else "本地保存也失败了",
            )
            return
        logger.info(
            "任务完成并已回传 task_id=%s title=%s markdown=%d 字 duration=%s 秒",
            task_id,
            title,
            len(markdown),
            duration_seconds,
        )

    async def _fail(self, task_id: str, code: str, message: str) -> None:
        text = truncate_message(message or "生成失败", FAIL_MESSAGE_MAX_CHARS)
        try:
            await self.relay.fail(task_id, code, text)
        except RelayError as exc:
            if exc.retriable and not exc.is_auth_error:
                self.outbox.add_fail(task_id, code, text)
                logger.warning(
                    "任务 %s 的失败状态无法回传（%s），已写入本地 outbox，稍后补传",
                    task_id,
                    exc.message,
                )
            else:
                logger.error("任务 %s 的失败状态无法回传：%s（原始失败：%s）", task_id, exc.message, text)
            return
        logger.error("任务失败已回传 task_id=%s code=%s message=%s", task_id, code, text)

    # ================================================================ 工具

    async def _wait_stop(self, stop: asyncio.Event, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def close(self) -> None:
        """释放后端日志句柄（不杀用户自己的 BiliNote 进程）。"""
        await self.backend.close()


__all__ = ["AgentRunner", "TaskLostError"]

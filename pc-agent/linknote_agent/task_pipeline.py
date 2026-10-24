"""BiliNote 任务执行流水线（REST 轮询版 AgentRunner 与 WS 推送版 RealtimeAgentRunner 共用）。

把「确保 BiliNote 后端 → 提交 /generate_note → 轮询 /task_status → 产出结果/失败」
这段与 Relay 传输方式无关的逻辑收敛到一处：

- ``on_progress``：状态变化时回调（调用方决定走 REST ``/progress`` 还是 WS ``task_update``）；
- ``on_lease``：长任务租约续期回调（REST ``/agent/tasks/{id}/heartbeat``）；
- 返回 :class:`TaskOutcome`，由调用方决定 ``complete`` / ``fail`` / ``task_note`` 的落点。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .backend import BackendError, BackendSupervisor
from .bilinote_client import BiliNoteClient, BiliNoteError
from .config import AgentConfig
from .logging_setup import get_logger
from .platforms import UnsupportedUrlError, ensure_supported_url, resolve_short_link
from .stage_map import (
    FailureInfo,
    format_elapsed,
    map_status,
    normalize_status,
    translate_failure,
)

logger = get_logger("pipeline")


@dataclass(frozen=True)
class ProgressEvent:
    """一次待回传的进度（调用方负责翻译成 REST 或 WS 字段）。"""

    stage: str
    progress: int
    message: str = ""


@dataclass
class TaskOutcome:
    """任务执行结果。

    kind:
    - ``completed``：BiliNote 成功，``result`` 为 ``{markdown, audio_meta}``；
    - ``failed``：失败，``failure`` 带 code + 中文 message。
    """

    kind: str
    result: Optional[dict[str, Any]] = None
    failure: Optional[FailureInfo] = None
    platform: str = ""
    url: str = ""
    duration_seconds: Optional[int] = None


ProgressCallback = Callable[[ProgressEvent], Awaitable[Any]]
LeaseCallback = Callable[[str], Awaitable[Any]]


class TaskPipeline:
    def __init__(
        self,
        config: AgentConfig,
        bilinote: BiliNoteClient,
        *,
        backend: Optional[BackendSupervisor] = None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self.config = config
        self.bilinote = bilinote
        self.backend = backend or BackendSupervisor(config, bilinote=bilinote)
        self._sleep = sleep
        self._clock = clock

    # ---------------------------------------------------------------- 主流程

    async def execute(
        self,
        task_id: str,
        raw_url: str,
        *,
        on_progress: ProgressCallback,
        on_lease: Optional[LeaseCallback] = None,
        platform_hint: str = "",
    ) -> TaskOutcome:
        """执行一个任务直到成功/失败（不负责把结果回传 Relay）。"""
        try:
            platform, url = ensure_supported_url(raw_url)
        except UnsupportedUrlError as exc:
            return TaskOutcome(
                kind="failed", failure=FailureInfo("unsupported_platform", str(exc))
            )

        if platform_hint and platform_hint != platform:
            logger.warning(
                "Relay 标记平台 %s 与链接实际平台 %s 不一致，按实际平台处理",
                platform_hint,
                platform,
            )

        await on_progress(ProgressEvent("parsing", 1, "已领取任务，正在准备处理"))
        timeout_s = int(float(self.config.backend_start_timeout or 90))
        await on_progress(
            ProgressEvent(
                "parsing",
                2,
                f"正在检查 BiliNote 后端，必要时自动启动（最多 {timeout_s} 秒）",
            )
        )
        try:
            await self.backend.ensure_running()
        except BackendError as exc:
            return TaskOutcome(
                kind="failed",
                failure=FailureInfo("backend_unavailable", f"BiliNote 后端不可用：{exc.message}"),
                platform=platform,
                url=url,
            )

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
            return TaskOutcome(kind="failed", failure=failure, platform=platform, url=url)

        logger.info(
            "已提交 BiliNote 任务 relay_task=%s bilinote_task=%s platform=%s",
            task_id,
            bilinote_task_id,
            platform,
        )
        return await self._await_bilinote(
            task_id,
            bilinote_task_id,
            platform=platform,
            url=url,
            on_progress=on_progress,
            on_lease=on_lease,
        )
    # ---------------------------------------------------------------- 轮询

    async def _await_bilinote(
        self,
        relay_task_id: str,
        bilinote_task_id: str,
        *,
        platform: str,
        url: str,
        on_progress: ProgressCallback,
        on_lease: Optional[LeaseCallback],
    ) -> TaskOutcome:
        started = self._clock()
        lease_task: Optional[asyncio.Task] = None
        if on_lease is not None:
            lease_task = asyncio.create_task(
                self._lease_loop(relay_task_id, on_lease), name=f"lease-{relay_task_id}"
            )
        try:
            last_status = ""
            last_progress_at = 0.0
            status_errors = 0
            while True:
                await self._sleep(
                    max(0.01, float(self.config.task_status_poll_interval_seconds))
                )
                elapsed = self._clock() - started
                if elapsed > float(self.config.task_timeout_seconds):
                    return TaskOutcome(
                        kind="failed",
                        failure=FailureInfo(
                            "task_timeout",
                            f"处理超时（已等待 {format_elapsed(elapsed)}），已放弃等待。"
                            "视频可能过长或网络较慢，请稍后重试。",
                        ),
                        platform=platform,
                        url=url,
                    )

                try:
                    status_payload = await self.bilinote.get_task_status(bilinote_task_id)
                except BiliNoteError as exc:
                    status_errors += 1
                    logger.warning(
                        "查询 BiliNote 任务状态失败（第 %d 次）：%s", status_errors, exc.message
                    )
                    if status_errors >= max(1, int(self.config.max_status_errors)):
                        return TaskOutcome(
                            kind="failed",
                            failure=FailureInfo(
                                "backend_unavailable",
                                "查询 BiliNote 任务状态连续失败，后端可能已经退出。"
                                f"请检查 BiliNote 后端与日志。（最后错误：{exc.message}）",
                            ),
                            platform=platform,
                            url=url,
                        )
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
                    return TaskOutcome(
                        kind="failed",
                        failure=translate_failure(reason),
                        platform=platform,
                        url=url,
                    )

                if status == "SUCCESS":
                    result = status_payload.get("result")
                    if not isinstance(result, dict):
                        result = {}
                    duration_seconds = _extract_duration(result)
                    return TaskOutcome(
                        kind="completed",
                        result=result,
                        platform=platform,
                        url=url,
                        duration_seconds=duration_seconds,
                    )

                update = map_status(status, elapsed)
                if update is None:
                    logger.debug("BiliNote 返回未映射状态 %r，继续等待", status)
                    continue

                now = self._clock()
                if status != last_status or (
                    now - last_progress_at
                ) >= float(self.config.progress_min_interval_seconds):
                    sent = await on_progress(
                        ProgressEvent(update.stage, update.progress, update.message)
                    )
                    # 回调返回 False 表示本次没送达（例如 WS 断开），下次继续尝试。
                    if sent is not False:
                        last_status = status
                        last_progress_at = now
        finally:
            if lease_task is not None:
                lease_task.cancel()
                try:
                    await lease_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    # ---------------------------------------------------------------- 租约

    async def _lease_loop(self, task_id: str, on_lease: LeaseCallback) -> None:
        interval = max(0.05, float(self.config.lease_heartbeat_interval_seconds))
        while True:
            await self._sleep(interval)
            try:
                await on_lease(task_id)
                logger.debug("租约续期成功 task_id=%s", task_id)
            except Exception as exc:  # noqa: BLE001 - 续租失败不打断生成
                logger.warning("租约续期失败 task_id=%s：%s", task_id, exc)


def _extract_duration(result: dict[str, Any]) -> Optional[int]:
    audio_meta = result.get("audio_meta")
    if not isinstance(audio_meta, dict):
        return None
    raw_duration = audio_meta.get("duration")
    try:
        if raw_duration is not None and float(raw_duration) > 0:
            return int(float(raw_duration))
    except (TypeError, ValueError):
        return None
    return None


def extract_title_and_markdown(
    result: dict[str, Any], *, max_title: int = 200
) -> tuple[str, str]:
    """从 BiliNote 的 NoteResult 里取 ``(title, markdown)``。"""
    from .stage_map import truncate_message

    markdown = str(result.get("markdown") or "").strip()
    audio_meta = result.get("audio_meta")
    if not isinstance(audio_meta, dict):
        audio_meta = {}
    title = truncate_message(
        str(audio_meta.get("title") or "").strip() or "未命名笔记", max_title
    )
    return title, markdown


__all__ = [
    "ProgressEvent",
    "TaskOutcome",
    "TaskPipeline",
    "extract_title_and_markdown",
]

"""v0.3 readiness：回答「电脑在线 ≠ 可以生成」。

Agent 收到 Relay ``hello`` 后立即发一次 ``agent_ready``，之后 readiness 变化时重发：

```json
{"type":"agent_ready","ready":false,"issues":[
  {"code":"model_not_configured","label":"未配置 AI 模型"},
  {"code":"engine_unreachable","label":"BiliNote 引擎不可用"}
]}
```

判定（``docs/v0.3-protocol-delta.md`` §3）：
- ``model_not_configured``：``provider_id`` 或 ``model_name`` 为空；
- ``engine_unreachable``：BiliNote ``/api/health`` 失败/超时（≤2s，老版本回退 provider 探针）；
- 预留（后续扩展）：``ffmpeg_missing``、``agent_paused``。

BiliNote 健康探测结果缓存 30s（``readiness_health_cache_seconds``），避免每次心跳/probe 都探测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .bilinote_client import BiliNoteClient
from .config import AgentConfig
from .logging_setup import get_logger

logger = get_logger("readiness")

ISSUE_MODEL_NOT_CONFIGURED = "model_not_configured"
ISSUE_ENGINE_UNREACHABLE = "engine_unreachable"
#: 预留，暂未实现判定
ISSUE_FFMPEG_MISSING = "ffmpeg_missing"
ISSUE_AGENT_PAUSED = "agent_paused"

ISSUE_LABELS = {
    ISSUE_MODEL_NOT_CONFIGURED: "未配置 AI 模型",
    ISSUE_ENGINE_UNREACHABLE: "BiliNote 引擎不可用",
    ISSUE_FFMPEG_MISSING: "ffmpeg 未安装或不可用",
    ISSUE_AGENT_PAUSED: "Agent 已暂停",
}

HealthProbe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "label": self.label}


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    issues: tuple[ReadinessIssue, ...] = ()

    def to_payload(self) -> dict:
        return {
            "ready": self.ready,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    @property
    def signature(self) -> tuple:
        """用于判断 readiness 是否变化的可比较签名。"""
        return (self.ready, tuple((issue.code, issue.label) for issue in self.issues))


class ReadinessChecker:
    """组合「模型配置」与「BiliNote 引擎健康」给出 readiness。

    ``health_probe`` 可注入（测试/自定义探测）；默认调
    :meth:`BiliNoteClient.engine_health`。``clock`` 可注入便于测试缓存 TTL。
    """

    def __init__(
        self,
        config: AgentConfig,
        bilinote: Optional[BiliNoteClient] = None,
        *,
        health_ttl: Optional[float] = None,
        health_timeout: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        health_probe: Optional[HealthProbe] = None,
    ):
        self.config = config
        self.bilinote = bilinote
        self.health_ttl = (
            float(config.readiness_health_cache_seconds)
            if health_ttl is None
            else float(health_ttl)
        )
        self.health_timeout = (
            float(config.readiness_health_timeout_seconds)
            if health_timeout is None
            else float(health_timeout)
        )
        self._clock = clock
        self._health_probe = health_probe
        self._engine_ok: Optional[bool] = None
        self._engine_checked_at = 0.0

    # ---------------------------------------------------------------- 判定

    def model_issues(self) -> list[ReadinessIssue]:
        issues: list[ReadinessIssue] = []
        if not str(self.config.provider_id or "").strip() or not str(
            self.config.model_name or ""
        ).strip():
            issues.append(
                ReadinessIssue(ISSUE_MODEL_NOT_CONFIGURED, ISSUE_LABELS[ISSUE_MODEL_NOT_CONFIGURED])
            )
        return issues

    async def engine_ok(self, *, force: bool = False) -> bool:
        now = self._clock()
        if (
            not force
            and self._engine_ok is not None
            and (now - self._engine_checked_at) < self.health_ttl
        ):
            return self._engine_ok
        ok = await self._probe_engine()
        self._engine_ok = ok
        self._engine_checked_at = now
        return ok

    async def _probe_engine(self) -> bool:
        if self._health_probe is not None:
            try:
                return bool(await self._health_probe())
            except Exception as exc:  # noqa: BLE001 - 探测失败=不可用
                logger.debug("注入的引擎健康探测失败：%s", exc)
                return False
        if self.bilinote is None:
            return False
        return await self.bilinote.engine_health(timeout=self.health_timeout)

    async def check(self, *, force: bool = False) -> ReadinessReport:
        issues = self.model_issues()
        if not await self.engine_ok(force=force):
            issues.append(
                ReadinessIssue(ISSUE_ENGINE_UNREACHABLE, ISSUE_LABELS[ISSUE_ENGINE_UNREACHABLE])
            )
        return ReadinessReport(ready=not issues, issues=tuple(issues))

    def invalidate_engine_cache(self) -> None:
        """任务失败/后端异常后调用，下一次 check 强制重新探测引擎。"""
        self._engine_ok = None
        self._engine_checked_at = 0.0


__all__ = [
    "ISSUE_AGENT_PAUSED",
    "ISSUE_ENGINE_UNREACHABLE",
    "ISSUE_FFMPEG_MISSING",
    "ISSUE_LABELS",
    "ISSUE_MODEL_NOT_CONFIGURED",
    "ReadinessChecker",
    "ReadinessIssue",
    "ReadinessReport",
]

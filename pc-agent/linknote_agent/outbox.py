"""本地 outbox：Relay 暂时不可达时，先把手里的结果落盘，后续循环再补传。

可靠性目标（architecture.md §3.2）：
- 任务已生成 Markdown 但 ``POST /complete`` 失败（断网、Relay 5xx）→ 落盘；
- Agent 重启后从 ``~/.linknote/outbox/`` 重新读取并补传；
- 补传成功/被 Relay 明确拒绝（404/409 等）后删除；被拒绝时把 Markdown 另存到
  ``~/.linknote/notes/<task_id>.md``，至少让用户能手动找回笔记。

每条记录一个 JSON 文件，文件名 ``{kind}-{task_id}.json``，写入用原子写。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ._jsonio import atomic_write_json, read_json
from .config import default_notes_dir, default_outbox_dir
from .logging_setup import get_logger
from .relay_client import RelayClient, RelayError

logger = get_logger("outbox")

KIND_COMPLETE = "complete"
KIND_FAIL = "fail"
VALID_KINDS = frozenset({KIND_COMPLETE, KIND_FAIL})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class OutboxEntry:
    task_id: str
    kind: str
    payload: dict[str, Any]
    attempts: int = 0
    created_at: str = field(default_factory=_now_iso)
    last_attempt_at: str = ""
    last_error: str = ""
    path: Path | None = field(default=None, compare=False, repr=False)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("path", None)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any], path: Path) -> OutboxEntry:
        return cls(
            task_id=str(data.get("task_id") or ""),
            kind=str(data.get("kind") or ""),
            payload=dict(data.get("payload") or {}),
            attempts=int(data.get("attempts") or 0),
            created_at=str(data.get("created_at") or ""),
            last_attempt_at=str(data.get("last_attempt_at") or ""),
            last_error=str(data.get("last_error") or ""),
            path=path,
        )


@dataclass
class FlushStats:
    delivered: int = 0
    deferred: int = 0
    dropped: int = 0
    notes_saved: int = 0

    @property
    def total(self) -> int:
        return self.delivered + self.deferred + self.dropped


class Outbox:
    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        notes_dir: Path | str | None = None,
    ):
        self.directory = Path(directory) if directory else default_outbox_dir()
        self.notes_dir = Path(notes_dir) if notes_dir else default_notes_dir()

    # ---------------------------------------------------------------- 读写

    def add_complete(
        self,
        task_id: str,
        title: str,
        markdown: str,
        *,
        duration_seconds: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> OutboxEntry:
        payload: dict[str, Any] = {"title": title, "markdown": markdown}
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
        if meta:
            payload["meta"] = meta
        return self._add(KIND_COMPLETE, task_id, payload)

    def add_fail(self, task_id: str, code: str, message: str) -> OutboxEntry:
        return self._add(KIND_FAIL, task_id, {"code": code, "message": message})

    def _add(self, kind: str, task_id: str, payload: dict[str, Any]) -> OutboxEntry:
        if kind not in VALID_KINDS:
            raise ValueError(f"非法 outbox 类型：{kind}")
        if not task_id:
            raise ValueError("outbox 记录缺少 task_id")
        entry = OutboxEntry(task_id=task_id, kind=kind, payload=payload)
        path = self.directory / f"{kind}-{_safe_name(task_id)}.json"
        entry.path = path
        atomic_write_json(path, entry.to_json())
        logger.warning(
            "Relay 暂时不可达，任务 %s 的 %s 结果已写入本地 outbox：%s",
            task_id,
            "笔记" if kind == KIND_COMPLETE else "失败状态",
            path,
        )
        return entry

    def entries(self) -> list[OutboxEntry]:
        if not self.directory.exists():
            return []
        result: list[OutboxEntry] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = read_json(path, default=None)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("读取 outbox 记录失败，已跳过 %s：%s", path, exc)
                continue
            if not isinstance(data, dict):
                logger.warning("outbox 记录格式异常，已跳过：%s", path)
                continue
            entry = OutboxEntry.from_json(data, path)
            if entry.kind not in VALID_KINDS or not entry.task_id:
                logger.warning("outbox 记录字段缺失，已跳过：%s", path)
                continue
            result.append(entry)
        result.sort(key=lambda e: (e.created_at, e.task_id))
        return result

    def pending(self) -> list[OutboxEntry]:
        return self.entries()

    def remove(self, entry: OutboxEntry) -> None:
        path = entry.path
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - 极少见
            logger.warning("删除 outbox 记录失败 %s：%s", path, exc)

    def record_attempt(self, entry: OutboxEntry, error: str) -> None:
        entry.attempts += 1
        entry.last_attempt_at = _now_iso()
        entry.last_error = (error or "")[:500]
        if entry.path is not None:
            try:
                atomic_write_json(entry.path, entry.to_json())
            except OSError as exc:  # pragma: no cover
                logger.warning("更新 outbox 记录失败 %s：%s", entry.path, exc)

    # ---------------------------------------------------------------- 补传

    async def flush(
        self,
        relay: RelayClient,
        *,
        entries: Iterable[OutboxEntry] | None = None,
    ) -> FlushStats:
        """尝试补传所有（或指定的）outbox 记录。"""
        stats = FlushStats()
        for entry in list(entries if entries is not None else self.entries()):
            try:
                if entry.kind == KIND_COMPLETE:
                    await relay.complete(
                        entry.task_id,
                        title=str(entry.payload.get("title") or "未命名笔记"),
                        markdown=str(entry.payload.get("markdown") or ""),
                        duration_seconds=entry.payload.get("duration_seconds"),
                        meta=entry.payload.get("meta"),
                    )
                else:
                    await relay.fail(
                        entry.task_id,
                        code=str(entry.payload.get("code") or "bilinote_error"),
                        message=str(entry.payload.get("message") or "生成失败"),
                    )
            except RelayError as exc:
                if exc.retriable and not exc.is_auth_error:
                    self.record_attempt(entry, exc.message)
                    stats.deferred += 1
                    logger.warning(
                        "补传任务 %s 失败（第 %d 次），稍后重试：%s",
                        entry.task_id,
                        entry.attempts,
                        exc.message,
                    )
                    continue
                # 明确不可重试：任务已被 Relay 回收/关闭，或 token 失效。
                if entry.kind == KIND_COMPLETE:
                    saved = self._save_note_locally(entry)
                    if saved:
                        stats.notes_saved += 1
                        logger.error(
                            "任务 %s 的笔记无法回传（%s），已保存到本地：%s",
                            entry.task_id,
                            exc.message,
                            saved,
                        )
                self.remove(entry)
                stats.dropped += 1
                logger.warning("放弃补传任务 %s 的 %s：%s", entry.task_id, entry.kind, exc.message)
            except Exception as exc:  # noqa: BLE001 - 保底不丢记录
                self.record_attempt(entry, f"{type(exc).__name__}: {exc}")
                stats.deferred += 1
                logger.exception("补传任务 %s 时出现未预期异常，稍后重试", entry.task_id)
            else:
                self.remove(entry)
                stats.delivered += 1
                logger.info("outbox 补传成功 task_id=%s kind=%s", entry.task_id, entry.kind)
        return stats

    def save_note_locally(self, task_id: str, markdown: str) -> Path | None:
        """把无法回传的笔记另存到 ``~/.linknote/notes/<task_id>.md``，返回路径。"""
        if not markdown:
            return None
        try:
            self.notes_dir.mkdir(parents=True, exist_ok=True)
            path = self.notes_dir / f"{_safe_name(task_id)}.md"
            path.write_text(markdown, encoding="utf-8")
            return path
        except OSError as exc:  # pragma: no cover
            logger.error("本地保存笔记失败：%s", exc)
            return None

    def _save_note_locally(self, entry: OutboxEntry) -> Path | None:
        return self.save_note_locally(entry.task_id, str(entry.payload.get("markdown") or ""))


def _safe_name(value: str) -> str:
    keep = []
    for ch in str(value):
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep)[:120] or "unknown"


__all__ = [
    "FlushStats",
    "KIND_COMPLETE",
    "KIND_FAIL",
    "Outbox",
    "OutboxEntry",
]

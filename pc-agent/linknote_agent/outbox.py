"""本地 outbox：笔记正文的本地可靠性存储 + 待补传/待 ack 队列。

两种工作模式共用：

- REST 轮询模式（旧兼容）：``complete`` / ``fail`` 记录，Relay 不可达时落盘，
  后续循环补传（:meth:`Outbox.flush`）。
- WebSocket 推送模式（默认）：``note`` 记录。笔记正文**只在本机**，通过 WS 发
  ``task_note``；收到手机 ``ack_note`` 后删除 outbox 记录（保留
  ``~/.linknote/notes/<task_id>.md``），或超过 ``note_retention_days`` 后清理。

每条记录一个 JSON 文件，文件名 ``{kind}-{task_id}.json``，写入用原子写。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ._jsonio import atomic_write_json, read_json
from .config import default_notes_dir, default_outbox_dir
from .logging_setup import get_logger
from .relay_client import RelayClient, RelayError

logger = get_logger("outbox")

KIND_COMPLETE = "complete"
KIND_FAIL = "fail"
KIND_NOTE = "note"  # WS 模式：正文待手机 ack
VALID_KINDS = frozenset({KIND_COMPLETE, KIND_FAIL, KIND_NOTE})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso_epoch(value: str) -> Optional[float]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


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

    # ---------------------------------------------------------------- WS 笔记

    def add_note(
        self,
        task_id: str,
        title: str,
        markdown: str,
        *,
        duration_seconds: int | None = None,
        meta: dict[str, Any] | None = None,
        save_file: bool = True,
    ) -> OutboxEntry:
        """保存一篇已生成的笔记：``notes/<task_id>.md`` + outbox 待 ack 记录。

        WS 模式下笔记正文只在本机流转（发 ``task_note`` 给 Relay 转发手机），
        本记录用于：断线/重连后重发、重启后重发、手机 ``ack_note`` 后清理。
        """
        if not str(markdown or "").strip():
            raise ValueError("笔记正文为空，不能写入本地 outbox")
        if save_file:
            self.save_note_locally(task_id, markdown)
        payload: dict[str, Any] = {"title": title or "未命名笔记", "markdown": markdown}
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
        if meta:
            payload["meta"] = meta
        entry = self._add(KIND_NOTE, task_id, payload)
        logger.info("笔记已保存到本地并等待手机 ack task_id=%s（%d 字）", task_id, len(markdown))
        return entry

    def pending_notes(self) -> list[OutboxEntry]:
        """所有等待手机 ack / 待重发的笔记记录。"""
        return [entry for entry in self.entries() if entry.kind == KIND_NOTE]

    def has_note(self, task_id: str) -> bool:
        return any(entry.task_id == task_id for entry in self.pending_notes()) or (
            self.notes_dir / f"{_safe_name(task_id)}.md"
        ).exists()

    def note_for(self, task_id: str) -> Optional[tuple[str, str]]:
        """取 ``(title, markdown)``：优先 outbox 记录，其次本地 notes 文件。"""
        for entry in self.pending_notes():
            if entry.task_id == task_id:
                markdown = str(entry.payload.get("markdown") or "")
                if markdown:
                    return str(entry.payload.get("title") or "未命名笔记"), markdown
        path = self.notes_dir / f"{_safe_name(task_id)}.md"
        if path.exists():
            try:
                return "未命名笔记", path.read_text(encoding="utf-8")
            except OSError as exc:  # pragma: no cover
                logger.warning("读取本地笔记失败 %s：%s", path, exc)
        return None

    def ack_note(self, task_id: str) -> bool:
        """手机已收到笔记：清理 outbox 记录，**保留** ``notes/`` 里的 Markdown 文件。"""
        removed = 0
        for entry in self.pending_notes():
            if entry.task_id == task_id:
                self.remove(entry)
                removed += 1
        if removed:
            logger.info("已收到手机 ack，清理 outbox 记录 task_id=%s（本地 notes 保留）", task_id)
        return removed > 0

    def prune_notes(self, max_age_seconds: float, *, now: Optional[float] = None) -> int:
        """清理超过保留期的笔记 outbox 记录（本地 notes 文件保留，不会丢正文）。"""
        if max_age_seconds <= 0:
            return 0
        deadline = (time.time() if now is None else now) - max_age_seconds
        removed = 0
        for entry in self.pending_notes():
            created = _parse_iso_epoch(entry.created_at)
            # created_at 解析失败时保守保留，避免误删刚生成的笔记。
            if created is None or created >= deadline:
                continue
            self.remove(entry)
            removed += 1
        if removed:
            logger.info(
                "已清理 %d 条超过保留期的笔记 outbox 记录（正文仍在 %s）",
                removed,
                self.notes_dir,
            )
        return removed

    def migrate_legacy_complete_entries(self) -> int:
        """把旧 REST 模式的 ``complete`` outbox 记录迁移成 WS ``note`` 记录。

        老版本用 ``POST /complete`` 把 Markdown 写入 D1；新 Relay 不再存正文，
        升级后这些记录改为等待 WS ``task_note`` 重发（正文保留本地）。
        """
        migrated = 0
        for entry in list(self.entries()):
            if entry.kind != KIND_COMPLETE:
                continue
            markdown = str(entry.payload.get("markdown") or "")
            if not markdown:
                self.remove(entry)
                continue
            self.add_note(
                entry.task_id,
                str(entry.payload.get("title") or "未命名笔记"),
                markdown,
                duration_seconds=entry.payload.get("duration_seconds"),
                meta=entry.payload.get("meta"),
            )
            self.remove(entry)
            migrated += 1
        if migrated:
            logger.warning("已把 %d 条旧 outbox 记录迁移为 WS 待 ack 笔记", migrated)
        return migrated

    def _add(self, kind: str, task_id: str, payload: dict[str, Any]) -> OutboxEntry:
        if kind not in VALID_KINDS:
            raise ValueError(f"非法 outbox 类型：{kind}")
        if not task_id:
            raise ValueError("outbox 记录缺少 task_id")
        entry = OutboxEntry(task_id=task_id, kind=kind, payload=payload)
        path = self.directory / f"{kind}-{_safe_name(task_id)}.json"
        entry.path = path
        atomic_write_json(path, entry.to_json())
        if kind == KIND_NOTE:
            logger.debug("笔记已写入本地 outbox（待 ack）：%s", path)
        else:
            logger.warning(
                "Relay 暂时不可达，任务 %s 的%s已写入本地 outbox：%s",
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
            if entry.kind == KIND_NOTE:
                # WS 模式的笔记正文只走 task_note，绝不通过 REST /complete 写进 D1。
                continue
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
    "KIND_NOTE",
    "Outbox",
    "OutboxEntry",
]

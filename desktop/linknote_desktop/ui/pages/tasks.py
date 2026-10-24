"""当前任务页：Agent 当前任务 / 队列 / 最近完成失败。

数据来自 controller 的后台快照（Agent 日志 + Relay /agent/me + 引擎 /task_status），
不新增任何后端接口。页面可见时每 3 秒自动刷新，也可手动刷新。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import style, utils
from ..widgets import (
    Card,
    EmptyState,
    InlineMessage,
    StatTile,
    StatusBadge,
    StatusDot,
    button,
    clear_layout,
    progress_bar,
)
from .base import BasePage

REFRESH_INTERVAL_MS = 3000

STAGE_LABELS = {
    "pending": "排队中",
    "parsing": "正在解析链接",
    "downloading": "正在下载音视频",
    "transcribing": "正在语音转写",
    "summarizing": "AI 正在总结",
    "formatting": "正在整理格式",
    "saving": "正在保存",
    "done": "已完成",
    "failed": "失败",
}

RECENT_LABELS = {
    "completed": ("已完成", "ok"),
    "failed": ("失败", "error"),
    "pending": ("待回传", "warn"),
    "lost": ("已中断", "neutral"),
    "running": ("进行中", "warn"),
}


class TasksPage(BasePage):
    PAGE_KEY = "tasks"
    PAGE_TITLE = "当前任务"
    PAGE_SUBTITLE = "手机发来的任务会在这里实时显示进度；历史结果会保留最近几条。"

    def build(self) -> None:
        self._snapshot: dict[str, Any] = dict(self.controller.last_tasks or {})
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)

        self._build_toolbar()
        self._build_stats()
        self._build_current_card()
        self._build_recent_card()
        self.controller.tasksChanged.connect(self._on_tasks_changed)
        if self._snapshot:
            self._render(self._snapshot)

    # ------------------------------------------------------------ 构建
    def _build_toolbar(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._refreshed_label = QLabel("正在读取任务状态…")
        self._refreshed_label.setObjectName("Help")
        layout.addWidget(self._refreshed_label, 1)
        self._refresh_button = button("刷新", "", self.refresh)
        layout.addWidget(self._refresh_button, 0)
        self.add(row)
        self._error_msg = InlineMessage()
        self.add(self._error_msg)

    def _build_stats(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._queue_tile = StatTile("排队中", "—", "等待 Agent 处理")
        self._running_tile = StatTile("运行中", "—", "Agent 正在执行")
        self._agent_tile = StatTile("电脑 Agent", "—", "")
        self._relay_tile = StatTile("Relay", "—", "")
        for tile in (self._queue_tile, self._running_tile, self._agent_tile, self._relay_tile):
            layout.addWidget(tile, 1)
        self.add(row)

    def _build_current_card(self) -> None:
        card = Card()
        self._current_card = card
        card.add_header(
            "正在执行",
            "进度来自 BiliNote 引擎；手机端也会同步看到同样的阶段。",
        )
        self._current_empty = EmptyState(
            "当前没有正在执行的任务",
            "手机发送视频链接后，这里会显示解析、下载、转写与生成笔记的实时进度。",
        )
        card.body.addWidget(self._current_empty)

        self._current_body = QWidget()
        body_layout = QVBoxLayout(self._current_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self._current_title = QLabel("")
        self._current_title.setWordWrap(True)
        self._current_title.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 14px; font-weight: bold; }}"
        )
        body_layout.addWidget(self._current_title)
        self._current_meta = QLabel("")
        self._current_meta.setObjectName("Muted")
        self._current_meta.setWordWrap(True)
        body_layout.addWidget(self._current_meta)
        self._current_stage = QLabel("")
        self._current_stage.setStyleSheet(
            f"QLabel {{ color: {style.CORAL_INK}; font-size: 12.5px; font-weight: bold; }}"
        )
        body_layout.addWidget(self._current_stage)
        self._current_progress = progress_bar()
        body_layout.addWidget(self._current_progress)
        self._current_hint = QLabel("")
        self._current_hint.setObjectName("Help")
        self._current_hint.setWordWrap(True)
        body_layout.addWidget(self._current_hint)
        card.body.addWidget(self._current_body)
        self._current_body.hide()
        self.add(card)

    def _build_recent_card(self) -> None:
        card = Card()
        self._recent_card = card
        card.add_header(
            "最近完成 / 失败",
            "来自 Agent 日志的最近任务记录（最多 8 条）。",
        )
        self._recent_container = QWidget()
        self._recent_layout = QVBoxLayout(self._recent_container)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(6)
        card.body.addWidget(self._recent_container)
        self._recent_empty = QLabel(
            "还没有历史任务。手机成功发送一条视频链接后，这里会显示处理结果。"
        )
        self._recent_empty.setObjectName("Help")
        self._recent_empty.setWordWrap(True)
        card.body.addWidget(self._recent_empty)
        self.add(card)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        self.refresh()
        self._timer.start()

    def on_hide(self) -> None:
        self._timer.stop()

    def on_status(self, status: dict) -> None:
        super().on_status(status)
        self._apply_status(status or {})

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        self._refresh_button.setEnabled(False)
        self.controller.refresh_tasks_async()

    def _on_tasks_changed(self, data: dict) -> None:
        self._snapshot = data or {}
        self._refresh_button.setEnabled(True)
        self._render(self._snapshot)

    # ------------------------------------------------------------ 渲染
    def _apply_status(self, status: dict) -> None:
        engine = status.get("engine") or {}
        agent = status.get("agent") or {}
        relay = status.get("relay") or {}
        agent_ok = bool(agent.get("running"))
        self._agent_tile.set_value(
            "运行中" if agent_ok else ("已停止" if agent.get("paired") else "未配对"),
            hint=f"PID {agent.get('pid')}" if agent.get("pid") else "",
            color=style.OK if agent_ok else style.MUTED,
        )
        relay_ok = utils.relay_online(status)
        queue = relay.get("queue") or {}
        queue_text = "在线" if relay_ok else ("离线" if agent.get("paired") else "未连接")
        self._relay_tile.set_value(
            queue_text,
            hint=f"队列 {int(queue.get('queued') or 0)}" if isinstance(queue, dict) else "",
            color=style.OK if relay_ok else style.MUTED,
        )
        engine_text = {
            "running": "引擎运行中",
            "starting": "引擎启动中",
            "error": "引擎异常",
        }.get(str(engine.get("state") or ""), "引擎未启动")
        if str(engine.get("state") or "") != "running":
            self._current_hint.setText(f"{engine_text}；任务会在引擎就绪后继续。")

    def _render(self, snapshot: dict) -> None:
        error = str(snapshot.get("error") or "")
        if error:
            self._error_msg.set_message(error, "error")
        else:
            self._error_msg.clear()

        queue = snapshot.get("queue") or {}
        agent = snapshot.get("agent") or {}
        queued = int(queue.get("queued") or 0)
        running = int(queue.get("running") or 0)
        outbox_pending = int(snapshot.get("outbox_pending") or 0)
        self._queue_tile.set_value(
            str(queued),
            hint=(f"另有 {outbox_pending} 条结果待回传" if outbox_pending else "等待 Agent 处理"),
        )
        self._running_tile.set_value(str(running), hint="Agent 正在执行")
        agent_running = bool(agent.get("running"))
        self._agent_tile.set_value(
            "运行中" if agent_running else ("已停止" if agent.get("paired") else "未配对"),
            hint=f"PID {agent.get('pid')}" if agent.get("pid") else "",
            color=style.OK if agent_running else style.MUTED,
        )
        relay_ok = bool(snapshot.get("relay_online"))
        self._relay_tile.set_value(
            "在线" if relay_ok else "离线",
            hint=f"{int(snapshot.get('devices') or 0)} 台手机" if relay_ok else "检查网络/配对",
            color=style.OK if relay_ok else style.MUTED,
        )
        generated = str(snapshot.get("generated_label") or "")
        if generated:
            self._refreshed_label.setText(
                f"更新于 {generated} · 每 {REFRESH_INTERVAL_MS // 1000} 秒自动刷新"
            )
        self._render_current(snapshot.get("current") or {})
        self._render_recent(snapshot.get("recent") or [])

    def _render_current(self, current: dict) -> None:
        has_current = bool(current)
        self._current_empty.setVisible(not has_current)
        self._current_body.setVisible(has_current)
        if not has_current:
            self._current_hint.clear()
            return
        title = str(current.get("title") or "").strip()
        url = str(current.get("url") or "")
        self._current_title.setText(title or url or "手机任务")
        platform = utils.format_platform(current.get("platform"))
        task_id = str(current.get("task_id") or "")
        short_id = task_id[:8] if task_id else "—"
        elapsed = float(current.get("elapsed_seconds") or 0)
        elapsed_text = utils.format_duration(elapsed) if elapsed else "刚刚开始"
        self._current_meta.setText(f"{platform} · 已用时 {elapsed_text} · 任务 {short_id}")
        stage = str(current.get("stage") or "")
        progress = int(current.get("progress") or 0)
        stage_text = STAGE_LABELS.get(stage, "正在处理")
        if current.get("backend_status") in ("PENDING", "PARSING") and progress <= 2:
            stage_text = "已领取任务，等待引擎开始处理"
        self._current_stage.setText(stage_text)
        if 0 < progress < 100:
            self._current_progress.setRange(0, 100)
            self._current_progress.setValue(progress)
            self._current_progress.show()
        elif progress >= 100:
            self._current_progress.setRange(0, 100)
            self._current_progress.setValue(100)
            self._current_progress.show()
        else:
            self._current_progress.setRange(0, 0)
            self._current_progress.show()
        message = str(current.get("message") or "").strip()
        hint_parts = []
        if message:
            hint_parts.append(message)
        hint_parts.append("可在手机端查看同一条任务的进度。")
        self._current_hint.setText(" · ".join(hint_parts))

    def _render_recent(self, recent: list[dict]) -> None:
        clear_layout(self._recent_layout)
        self._recent_empty.setVisible(not recent)
        for item in recent:
            self._recent_layout.addWidget(_RecentRow(item))


class _RecentRow(QFrame):
    """最近任务一行：状态点 + 标题 + 状态徽章 + 时间/说明。"""

    def __init__(self, item: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)

        status = str(item.get("status") or "")
        label, level = RECENT_LABELS.get(status, ("未知", "neutral"))
        color = {
            "ok": style.OK,
            "error": style.DANGER,
            "warn": style.WARN,
            "neutral": style.IDLE,
        }.get(level, style.IDLE)
        dot = StatusDot(color, 8)
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "")
        task_id = str(item.get("task_id") or "")
        title_label = QLabel(title or "视频笔记任务")
        title_label.setWordWrap(True)
        title_label.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}"
        )
        column.addWidget(title_label)
        meta_parts = []
        platform_text = utils.format_platform(item.get("platform")) if item.get("platform") else ""
        if platform_text:
            meta_parts.append(platform_text)
        if item.get("time_label"):
            meta_parts.append(str(item.get("time_label")))
        if task_id:
            meta_parts.append(f"任务 {task_id[:8]}")
        chars = int(item.get("markdown_chars") or 0)
        if chars:
            meta_parts.append(f"{chars} 字")
        if status == "pending":
            meta_parts.append("Relay 恢复后自动回传")
        message = str(item.get("message") or "").strip()
        if message and status in ("failed", "lost", "pending"):
            meta_parts.append(utils.clip_text(message, 90))
        meta = QLabel(" · ".join(part for part in meta_parts if part))
        meta.setObjectName("Help")
        meta.setWordWrap(True)
        column.addWidget(meta)
        layout.addLayout(column, 1)

        badge = StatusBadge(label, level)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

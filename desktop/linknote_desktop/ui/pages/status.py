"""状态页（首页）：四张状态卡 + 配对手机 + 最近事件。"""
from __future__ import annotations

from collections import deque
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import style, utils
from ..pairing_panel import PairingPanel
from ..widgets import (
    Card,
    CodeView,
    InfoLine,
    InlineMessage,
    StatusBadge,
    StatusDot,
    button,
    progress_bar,
)
from .base import BasePage


class _StatusCard(Card):
    """状态页用的卡片：标题 + 徽章 + 若干信息行 + 底部按钮。"""

    def __init__(self, title: str):
        super().__init__(shadow=False, spacing=8)
        self.setMinimumHeight(172)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.dot = StatusDot(style.IDLE, 9)
        header.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        header.addWidget(title_label, 1)
        self.badge = StatusBadge("未知", "neutral")
        header.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(header)

        self._lines_layout = QVBoxLayout()
        self._lines_layout.setContentsMargins(0, 2, 0, 0)
        self._lines_layout.setSpacing(3)
        self.body.addLayout(self._lines_layout)

        self._lines: dict[str, InfoLine] = {}
        self.message = InlineMessage()
        self.body.addWidget(self.message)
        self.body.addStretch(1)

        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        self._actions.addStretch(1)
        self.body.addLayout(self._actions)

    def add_line(self, key: str) -> InfoLine:
        line = InfoLine(key)
        self._lines[key] = line
        self._lines_layout.addWidget(line)
        return line

    def set_line(self, key: str, value: str, color: str = style.INK_SOFT) -> None:
        line = self._lines.get(key)
        if line is not None:
            line.setValue(value, color)

    def add_action(self, text: str, kind: str = "", callback=None) -> QPushButton:
        action = button(text, kind, on_click=callback)
        action.setMinimumWidth(78)
        self._actions.addWidget(action, 0)
        return action

    def set_badge(self, state: str, text: str, busy: bool = False) -> None:
        self.badge.set_state(state, text)
        color = {
            "ok": style.OK,
            "warn": style.WARN,
            "error": style.DANGER,
            "info": style.CORAL,
            "neutral": style.IDLE,
            "busy": style.WARN,
        }.get(state, style.IDLE)
        self.dot.set_state(color, busy=busy)

    def set_message(self, text: str, level: str = "info") -> None:
        if text:
            self.message.set_message(text, level)
        else:
            self.message.clear()


class StatusPage(BasePage):
    PAGE_KEY = "status"
    PAGE_TITLE = "linknote 链记"
    PAGE_SUBTITLE = "手机发链接，电脑做笔记"

    MAX_EVENTS = 8

    # ------------------------------------------------------------ 头部（品牌）
    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 2)
        row.setSpacing(14)

        icon = QLabel()
        icon.setPixmap(utils.app_pixmap(46))
        icon.setFixedSize(46, 46)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        title = QLabel("linknote")
        title_font = QFont("Segoe UI")
        title_font.setPointSizeF(19.0)
        title_font.setWeight(QFont.Weight.Bold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        title.setFont(title_font)
        column.addWidget(title)
        subtitle = QLabel("链记 · 手机发链接，电脑做笔记")
        subtitle.setObjectName("Muted")
        column.addWidget(subtitle)
        row.addLayout(column, 1)

        self._overall_badge = StatusBadge("正在初始化", "neutral")
        row.addWidget(self._overall_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        refresh = button("刷新状态", "", on_click=self.controller.refresh_status_async)
        refresh.setToolTip("立即刷新引擎 / Agent / Relay 状态")
        row.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._status: dict = self.controller.last_status or {}
        self._events: deque[str] = deque(maxlen=self.MAX_EVENTS)
        self._ffmpeg_downloading = False
        self._ffmpeg_last_text = ""

        self._build_cards()
        self._build_pairing_panel()
        self._build_events_card()

        if self._status:
            self._apply_status(self._status)

    def _build_cards(self) -> None:
        grid = QWidget()
        layout = QGridLayout(grid)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(14)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        self._relay_card = self._build_relay_card()
        self._agent_card = self._build_agent_card()
        self._engine_card = self._build_engine_card()
        self._ffmpeg_card = self._build_ffmpeg_card()
        layout.addWidget(self._relay_card, 0, 0)
        layout.addWidget(self._agent_card, 0, 1)
        layout.addWidget(self._engine_card, 1, 0)
        layout.addWidget(self._ffmpeg_card, 1, 1)
        self.add(grid)

    def _build_relay_card(self) -> _StatusCard:
        card = _StatusCard("Relay 中转")
        card.add_line("地址")
        card.add_line("Agent")
        card.add_line("队列")
        card.add_action("测试连接", "Primary", self._test_relay)
        card.set_line("地址", str(getattr(self.controller.config, "relay_base_url", "")) or "—")
        return card

    def _build_agent_card(self) -> _StatusCard:
        card = _StatusCard("电脑 Agent")
        card.add_line("状态")
        card.add_line("PID")
        card.add_line("配对")
        self._agent_button = card.add_action("启动", "Primary", self._toggle_agent)
        return card

    def _build_engine_card(self) -> _StatusCard:
        card = _StatusCard("BiliNote 引擎")
        card.add_line("状态")
        card.add_line("PID")
        card.add_line("路径")
        self._engine_button = card.add_action("启动", "Primary", self._toggle_engine)
        self._engine_restart_button = card.add_action("重启", "", self._restart_engine)
        return card

    def _build_ffmpeg_card(self) -> _StatusCard:
        card = _StatusCard("ffmpeg 音视频")
        card.add_line("版本")
        card.add_line("路径")
        self._ffmpeg_progress = progress_bar()
        self._ffmpeg_progress.hide()
        card.body.addWidget(self._ffmpeg_progress)
        self._ffmpeg_progress_label = QLabel("")
        self._ffmpeg_progress_label.setObjectName("Help")
        self._ffmpeg_progress_label.hide()
        card.body.addWidget(self._ffmpeg_progress_label)
        self._ffmpeg_download_button = card.add_action("下载 ffmpeg", "Primary", self._download_ffmpeg)
        card.add_action("重新检测", "", self._refresh_status)
        return card

    def _build_pairing_panel(self) -> None:
        self._pairing_panel = PairingPanel(self.controller)
        self._pairing_panel.message.connect(self.toast)
        self.add(self._pairing_panel)

    def _build_events_card(self) -> None:
        card = Card(spacing=8)
        card.add_header(
            "最近事件",
            "来自 Agent 与 BiliNote 引擎的实时消息。",
            trailing=button("查看全部日志", "Link", lambda: self.goto("logs")),
        )
        self._events_view = CodeView(max_height=150, wrap=True)
        self._events_view.setMinimumHeight(96)
        card.add(self._events_view)
        self.add(card)
        self._render_events()

    # ------------------------------------------------------------ 事件
    def on_show(self) -> None:
        current = self.controller.last_status or {}
        if current:
            self._status = current
            self._apply_status(current)
        elif not self._status:
            self.controller.refresh_status_async()
        self._pairing_panel.refresh()
        self._render_events()

    def on_status(self, status: dict) -> None:
        super().on_status(status)
        self._status = status or {}
        self._apply_status(self._status)
        self._pairing_panel.update_status(self._status)

    def on_pairing_changed(self, state: dict) -> None:
        self._pairing_panel.update_pairing(state or {})

    def on_log_appended(self, line: str) -> None:
        text = utils.clip_text(line, 220)
        if not text:
            return
        import datetime as _dt

        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        self._events.appendleft(f"{stamp}  {text}")
        self._render_events()

    def on_error(self, message: str) -> None:
        self._pairing_panel.on_error(message)

    def on_notification(self, title: str, message: str, level: str) -> None:
        combined = f"{title} {message}".lower()
        if "ffmpeg" not in combined:
            return
        if level in ("error", "warning", "success"):
            self._ffmpeg_downloading = False
            self._ffmpeg_progress.hide()
            self._ffmpeg_progress_label.hide()
            self._ffmpeg_download_button.setEnabled(True)
            self._ffmpeg_download_button.setText("重新下载")
            self._ffmpeg_card.set_message(message, "success" if level == "success" else level)

    def on_ffmpeg_progress(self, done: int, total: int) -> None:
        self._ffmpeg_downloading = True
        self._ffmpeg_progress.show()
        self._ffmpeg_progress_label.show()
        if total and total > 0:
            percent = max(0, min(100, int(done * 100 / total)))
            self._ffmpeg_progress.setRange(0, 100)
            self._ffmpeg_progress.setValue(percent)
            text = f"正在下载 ffmpeg · {utils.format_size_pair(done, total)}"
        else:
            self._ffmpeg_progress.setRange(0, 0)
            text = f"正在下载 ffmpeg · {utils.format_bytes(done)}"
        self._ffmpeg_progress_label.setText(text)
        self._ffmpeg_card.set_message(text, "info")

    # ------------------------------------------------------------ 状态应用
    def _apply_status(self, status: dict) -> None:
        status = status or {}
        engine = status.get("engine") or {}
        agent = status.get("agent") or {}
        relay = status.get("relay") or {}
        ffmpeg = status.get("ffmpeg") or {}

        overall = utils.overall_state(status)
        self._overall_badge.set_state(
            {"ok": "ok", "warn": "warn", "error": "error"}.get(overall, "neutral"),
            utils.overall_text(status),
        )

        # Relay
        paired = bool(agent.get("paired"))
        online = utils.relay_online(status)
        if online:
            self._relay_card.set_badge("ok", "在线")
        elif paired:
            self._relay_card.set_badge("error", "离线")
        else:
            self._relay_card.set_badge("neutral", "未连接")
        agent_info = relay.get("agent") or {}
        relay_agent = agent_info.get("name") or self.controller.config.agent_name or "—"
        queue = relay.get("queue") or {}
        queue_text = "—"
        if isinstance(queue, dict):
            queue_text = f"等待 {int(queue.get('queued') or 0)} · 运行 {int(queue.get('running') or 0)}"
        self._relay_card.set_line("地址", relay.get("url") or self.controller.config.relay_base_url)
        self._relay_card.set_line("Agent", relay_agent)
        self._relay_card.set_line("队列", queue_text)
        relay_error = relay.get("error")
        if online:
            self._relay_card.set_message("")
        elif relay_error:
            self._relay_card.set_message(f"Relay 连接异常：{utils.clip_text(relay_error, 120)}", "warning")

        # Agent
        agent_running = bool(agent.get("running"))
        if agent_running:
            self._agent_card.set_badge("ok", "运行中")
        elif paired:
            self._agent_card.set_badge("neutral", "已停止")
        else:
            self._agent_card.set_badge("error", "未配对")
        status_text = "运行中" if agent_running else "未运行"
        if agent.get("error"):
            status_text = utils.clip_text(agent.get("error"), 80)
        self._agent_card.set_line("状态", status_text)
        self._agent_card.set_line("PID", str(agent.get("pid") or "—"))
        self._agent_card.set_line("配对", "已配对" if paired else "未配对")
        self._agent_button.setText("停止" if agent_running else "启动")
        self._agent_button.setEnabled(agent_running or paired)
        self._agent_button.setToolTip("" if (agent_running or paired) else "请先完成手机配对")
        if not paired:
            self._agent_card.set_message("还没有配对手机，请先在下方生成配对码。", "warning")
        elif not agent_running:
            self._agent_card.set_message("Agent 未运行，手机任务不会被接收。", "warning")
        else:
            self._agent_card.set_message("")

        # BiliNote 引擎
        engine_state = engine.get("state") or "stopped"
        engine_text, _level, _color = utils.engine_state_text(
            engine_state, engine.get("message") or ""
        )
        engine_badge = {
            "running": ("ok", "运行中"),
            "starting": ("warn", "启动中"),
            "error": ("error", "异常"),
        }.get(engine_state, ("neutral", "已停止"))
        self._engine_card.set_badge(engine_badge[0], engine_badge[1], busy=engine_state == "starting")
        self._engine_card.set_line(
            "状态",
            utils.clip_text(engine.get("message") or engine_text, 90),
            style.DANGER if engine_state == "error" else style.INK_SOFT,
        )
        self._engine_card.set_line("PID", str(engine.get("pid") or "—"))
        engine_path = engine.get("path") or self.controller.config.resolved_engine_exe() or "未设置"
        self._engine_card.set_line("路径", engine_path)
        if engine_state in ("running", "starting"):
            self._engine_button.setText("停止")
            self._engine_button.setEnabled(True)
        else:
            self._engine_button.setText("启动")
            self._engine_button.setEnabled(True)
        self._engine_restart_button.setEnabled(engine_state in ("running", "starting", "error"))
        if engine_state == "error":
            self._engine_card.set_message(utils.clip_text(engine.get("message") or "引擎异常", 160), "error")
        elif engine_state == "starting":
            self._engine_card.set_message("正在启动 BiliNote 引擎，首次启动可能需要几十秒…", "info")
        else:
            self._engine_card.set_message("")

        # ffmpeg
        available = bool(ffmpeg.get("available"))
        self._ffmpeg_card.set_badge("ok" if available else "error", "已安装" if available else "缺失")
        self._ffmpeg_card.set_line("版本", utils.clip_text(ffmpeg.get("version") or "—", 110))
        self._ffmpeg_card.set_line("路径", ffmpeg.get("path") or ffmpeg.get("bin_dir") or "未找到")
        self._ffmpeg_download_button.setVisible(not available)
        if available:
            if self._ffmpeg_downloading:
                self._ffmpeg_downloading = False
                self._ffmpeg_progress.hide()
                self._ffmpeg_progress_label.hide()
                self._ffmpeg_card.set_message("ffmpeg 安装完成。", "success")
            else:
                self._ffmpeg_card.set_message("")
        elif not self._ffmpeg_downloading:
            self._ffmpeg_card.set_message(
                "未检测到 ffmpeg：下载、转写和截图功能会受影响。", "warning"
            )

    # ------------------------------------------------------------ 卡片动作
    def _refresh_status(self) -> None:
        self._ffmpeg_card.set_message("正在检测 ffmpeg…", "info")
        self.controller.refresh_status_async()

    def _test_relay(self) -> None:
        self._relay_card.set_message("正在连接 Relay…", "info")

        def on_result(data) -> None:
            version = (data or {}).get("version") or ""
            suffix = f" · 版本 {version}" if version else ""
            self._relay_card.set_message(f"Relay 在线，响应正常{suffix}", "success")

        def on_error(message: str) -> None:
            self._relay_card.set_message(f"连接失败：{message}", "error")

        self.run_bg(self.controller.relay.health, on_result=on_result, on_error=on_error)

    def _toggle_agent(self) -> None:
        agent = self._status.get("agent") or {}
        if agent.get("running"):
            self.controller.stop_agent()
            self._agent_card.set_message("正在停止 Agent…", "info")
        elif agent.get("paired"):
            self.controller.start_agent()
            self._agent_card.set_message("正在启动 Agent…", "info")
        else:
            self._agent_card.set_message("还没有配对手机，请先生成配对码。", "warning")

    def _toggle_engine(self) -> None:
        state = (self._status.get("engine") or {}).get("state") or "stopped"
        if state in ("running", "starting"):
            self._engine_card.set_message("正在停止引擎…", "info")
            self.controller.stop_engine_async()
        else:
            self._engine_card.set_message("正在启动引擎…", "info")
            self.controller.start_engine_async()

    def _restart_engine(self) -> None:
        self._engine_card.set_message("正在重启引擎…", "info")
        self.controller.restart_engine_async()

    def _download_ffmpeg(self) -> None:
        self._ffmpeg_downloading = True
        self._ffmpeg_download_button.setEnabled(False)
        self._ffmpeg_download_button.setText("下载中…")
        self._ffmpeg_progress.setRange(0, 0)
        self._ffmpeg_progress.show()
        self._ffmpeg_progress_label.setText("正在准备下载 ffmpeg…")
        self._ffmpeg_progress_label.show()
        self._ffmpeg_card.set_message("正在下载 ffmpeg，完成后会自动安装。", "info")
        self.controller.download_ffmpeg_async()

    # ------------------------------------------------------------ 事件渲染
    def _render_events(self) -> None:
        if not self._events:
            self._events_view.set_text("暂无事件。手机任务开始后，这里会实时显示进度。")
            return
        self._events_view.set_text("\n".join(self._events))



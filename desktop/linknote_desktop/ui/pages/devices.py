"""设备页：手机配对面板 + Relay/Agent/引擎 连接状态简洁卡。

一级导航只有 4 个（笔记库/任务/设备/设置），这里承载配对与「电脑是否在线」类信息，
不再把四张技术状态卡堆成首页。
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import style, utils
from ..pairing_panel import PairingPanel
from ..widgets import Card, InlineMessage, StatusBadge, StatusDot, button
from .base import BasePage


class _ConnCard(Card):
    """连接状态小卡：标题 + 状态徽章 + 一行说明 + 操作按钮。"""

    def __init__(self, title: str):
        super().__init__(shadow=False, spacing=6)
        self.setMinimumHeight(132)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.dot = StatusDot(style.IDLE, 8)
        header.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        header.addWidget(title_label, 1)
        self.badge = StatusBadge("未知", "neutral")
        header.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(header)
        self.value = QLabel("—")
        self.value.setObjectName("Muted")
        self.value.setWordWrap(True)
        self.body.addWidget(self.value)
        self.message = InlineMessage()
        self.body.addWidget(self.message)
        self.body.addStretch(1)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(6)
        self._actions.addStretch(1)
        self.body.addLayout(self._actions)

    def add_action(self, text: str, kind: str = "", callback=None):
        action = button(text, kind, on_click=callback)
        action.setFixedHeight(30)
        action.setMinimumWidth(72)
        self._actions.addWidget(action, 0)
        return action

    def set_state(self, state: str, text: str, busy: bool = False) -> None:
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

    def set_value(self, text: str) -> None:
        self.value.setText(str(text or "—"))

    def set_message(self, text: str, level: str = "info") -> None:
        if text:
            self.message.set_message(text, level)
        else:
            self.message.clear()


class DevicesPage(BasePage):
    PAGE_KEY = "devices"
    PAGE_TITLE = "设备"
    PAGE_SUBTITLE = "手机配对、电脑在线状态与任务服务。配对成功后设备会一直显示在这里。"

    def build(self) -> None:
        self._status: dict[str, Any] = dict(self.controller.last_status or {})
        self._build_pairing_panel()
        self._build_connection_cards()
        self._build_footer_links()
        if self._status:
            self._apply_status(self._status)

    # ------------------------------------------------------------ 构建
    def _build_pairing_panel(self) -> None:
        self._pairing_panel = PairingPanel(self.controller)
        self._pairing_panel.message.connect(self.toast)
        self.add(self._pairing_panel)

    def _build_connection_cards(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._relay_card = self._build_relay_card()
        self._agent_card = self._build_agent_card()
        self._engine_card = self._build_engine_card()
        for card in (self._relay_card, self._agent_card, self._engine_card):
            layout.addWidget(card, 1)
        self.add(row)

    def _build_relay_card(self) -> _ConnCard:
        card = _ConnCard("Relay 中转")
        card.add_action("测试连接", "Primary", self._test_relay)
        return card

    def _build_agent_card(self) -> _ConnCard:
        card = _ConnCard("电脑 Agent")
        self._agent_button = card.add_action("启动", "Primary", self._toggle_agent)
        return card

    def _build_engine_card(self) -> _ConnCard:
        card = _ConnCard("BiliNote 引擎")
        self._engine_button = card.add_action("启动", "Primary", self._toggle_engine)
        self._engine_restart_button = card.add_action("重启", "", self._restart_engine)
        return card

    def _build_footer_links(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        hint = QLabel("更多诊断：")
        hint.setObjectName("Help")
        layout.addWidget(hint, 0)
        layout.addWidget(button("引擎与诊断", "Link", lambda: self.goto("engine")), 0)
        layout.addWidget(button("运行日志", "Link", lambda: self.goto("logs")), 0)
        layout.addStretch(1)
        self.add(row)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        self._pairing_panel.refresh()
        current = self.controller.last_status or self._status
        if current:
            self._status = current
            self._apply_status(current)

    def on_status(self, status: dict) -> None:
        super().on_status(status)
        self._status = status or {}
        self._apply_status(self._status)
        self._pairing_panel.update_status(self._status)

    def on_pairing_changed(self, state: dict) -> None:
        self._pairing_panel.update_pairing(state or {})

    def on_error(self, message: str) -> None:
        self._pairing_panel.on_error(message)

    # ------------------------------------------------------------ 状态渲染
    def _apply_status(self, status: dict) -> None:
        engine = status.get("engine") or {}
        agent = status.get("agent") or {}
        relay = status.get("relay") or {}
        paired = bool(agent.get("paired"))

        # Relay
        online = utils.relay_online(status)
        if online:
            self._relay_card.set_state("ok", "在线")
            agent_name = (relay.get("agent") or {}).get("name") or self.controller.config.agent_name
            self._relay_card.set_value(f"连接到 {relay.get('url') or self.controller.config.relay_base_url}\n{agent_name or '本机'}")
            self._relay_card.set_message("")
        elif paired:
            self._relay_card.set_state("error", "离线")
            self._relay_card.set_value(relay.get("url") or self.controller.config.relay_base_url)
            error = str(relay.get("error") or "")
            self._relay_card.set_message(
                f"无法连接 Relay：{utils.clip_text(error, 90)}" if error else "检查网络或 Relay 地址",
                "warning",
            )
        else:
            self._relay_card.set_state("neutral", "未连接")
            self._relay_card.set_value(relay.get("url") or self.controller.config.relay_base_url)
            self._relay_card.set_message("配对手机后会显示在线状态。", "info")

        # Agent
        running = bool(agent.get("running"))
        if running:
            self._agent_card.set_state("ok", "运行中")
        elif paired:
            self._agent_card.set_state("neutral", "已停止")
        else:
            self._agent_card.set_state("error", "未配对")
        pid = agent.get("pid")
        pair_text = "已配对" if paired else "未配对"
        self._agent_card.set_value(
            f"{'运行中' if running else '未运行'} · {pair_text}" + (f" · PID {pid}" if pid else "")
        )
        if agent.get("error"):
            self._agent_card.set_message(utils.clip_text(agent.get("error"), 90), "error")
        elif not paired:
            self._agent_card.set_message("先在上方完成手机配对，Agent 才能接收任务。", "warning")
        else:
            self._agent_card.set_message("")
        self._agent_button.setText("停止" if running else "启动")
        self._agent_button.setEnabled(running or paired)

        # 引擎
        state = str(engine.get("state") or "stopped")
        badge = {
            "running": ("ok", "运行中"),
            "starting": ("warn", "启动中"),
            "error": ("error", "异常"),
        }.get(state, ("neutral", "已停止"))
        self._engine_card.set_state(badge[0], badge[1], busy=state == "starting")
        engine_pid = engine.get("pid")
        self._engine_card.set_value(
            str(engine.get("message") or utils.engine_state_text(state)[0])
            + (f" · PID {engine_pid}" if engine_pid else "")
        )
        if state == "error":
            self._engine_card.set_message(
                utils.clip_text(engine.get("message") or "引擎异常", 90), "error"
            )
        elif state == "running":
            self._engine_card.set_message("")
        else:
            self._engine_card.set_message("引擎未运行，AI 生成笔记会失败。", "warning")
        self._engine_button.setText("停止" if state in ("running", "starting") else "启动")
        self._engine_button.setEnabled(True)
        self._engine_restart_button.setEnabled(state in ("running", "starting", "error"))

    # ------------------------------------------------------------ 动作
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
            self._agent_card.set_message("还没有配对手机，请先在上方生成配对码。", "warning")

    def _toggle_engine(self) -> None:
        state = str((self._status.get("engine") or {}).get("state") or "stopped")
        if state in ("running", "starting"):
            self._engine_card.set_message("正在停止引擎…", "info")
            self.controller.stop_engine_async()
        else:
            self._engine_card.set_message("正在启动引擎（首次可能需要几十秒）…", "info")
            self.controller.start_engine_async()

    def _restart_engine(self) -> None:
        self._engine_card.set_message("正在重启引擎…", "info")
        self.controller.restart_engine_async()

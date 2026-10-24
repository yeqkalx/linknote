"""日志页：Agent / BiliNote 引擎日志，每 2 秒刷新。"""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from .. import utils
from ..widgets import Card, CodeView, button, hint_label
from .base import BasePage


class LogsPage(BasePage):
    PAGE_KEY = "logs"
    PAGE_TITLE = "日志"
    PAGE_SUBTITLE = "查看 Agent 与 BiliNote 引擎的最近运行日志，方便排查问题。"
    REFRESH_INTERVAL_MS = 2000
    TAIL_LINES = 300

    def build(self) -> None:
        self._build_log_card()
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_logs)

    def _build_log_card(self) -> None:
        card = Card()
        self._log_card = card
        self._open_dir_button = button("打开日志目录", "", self._open_log_dir)
        card.add_header(
            "运行日志",
            f"每 {self.REFRESH_INTERVAL_MS // 1000} 秒自动刷新最近 {self.TAIL_LINES} 行。",
            trailing=self._open_dir_button,
        )

        self._tabs = QTabWidget()
        self._agent_view = CodeView(wrap=False)
        self._agent_view.setMinimumHeight(300)
        self._engine_view = CodeView(wrap=False)
        self._engine_view.setMinimumHeight(300)
        self._tabs.addTab(self._agent_view, "Agent")
        self._tabs.addTab(self._engine_view, "BiliNote 引擎")
        self._tabs.currentChanged.connect(lambda _index: self._refresh_logs())
        card.body.addWidget(self._tabs)

        paths = self.controller.data_paths()
        path_column = QVBoxLayout()
        path_column.setContentsMargins(0, 0, 0, 0)
        path_column.setSpacing(1)
        agent_path = QLabel(f"Agent 日志：{paths.get('agent_log') or '—'}")
        agent_path.setObjectName("Help")
        agent_path.setWordWrap(True)
        path_column.addWidget(agent_path)
        engine_path = QLabel(f"引擎日志：{paths.get('engine_log') or '—'}")
        engine_path.setObjectName("Help")
        engine_path.setWordWrap(True)
        path_column.addWidget(engine_path)
        card.body.addLayout(path_column)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
        self._refresh_label = QLabel("尚未刷新")
        self._refresh_label.setObjectName("Help")
        footer.addWidget(self._refresh_label, 1)
        self._refresh_button = button("立即刷新", "", self._refresh_logs)
        self._refresh_button.setFixedHeight(30)
        footer.addWidget(self._refresh_button, 0)
        card.body.addLayout(footer)
        card.body.addWidget(hint_label("日志可能包含本机路径与任务信息，分享前请先检查内容。"))
        self.add(card)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        self._refresh_logs()
        self._timer.start()

    def on_hide(self) -> None:
        self._timer.stop()

    def show_engine_tab(self) -> None:
        self._tabs.setCurrentIndex(1)
        self._refresh_logs()

    def show_agent_tab(self) -> None:
        self._tabs.setCurrentIndex(0)
        self._refresh_logs()

    # ------------------------------------------------------------ 刷新
    def _refresh_logs(self) -> None:
        try:
            agent_text = self.controller.agent_log_tail(self.TAIL_LINES)
        except Exception:  # noqa: BLE001 - 读日志失败不应影响页面
            agent_text = ""
        try:
            engine_text = self.controller.engine_log_tail(self.TAIL_LINES)
        except Exception:  # noqa: BLE001
            engine_text = ""
        self._agent_view.set_text(agent_text.strip() or _empty_placeholder("Agent", agent_text))
        self._engine_view.set_text(
            engine_text.strip() or _empty_placeholder("BiliNote 引擎", engine_text)
        )
        self._refresh_label.setText(
            "最后刷新：" + _dt.datetime.now().strftime("%H:%M:%S")
        )

    def _open_log_dir(self) -> None:
        paths = self.controller.data_paths()
        if not utils.open_path(paths.get("logs")):
            self.toast("日志目录不存在", "warning")


def _empty_placeholder(name: str, raw: str) -> str:
    if raw:
        return raw
    return f"暂无 {name} 日志。\n引擎 / Agent 启动后，这里会显示最新输出。"

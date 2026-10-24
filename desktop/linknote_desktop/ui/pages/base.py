"""页面基类：滚动容器 + 标题 + 引擎门禁 + 异步调用辅助。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import style
from ..widgets import InlineMessage, button
from .. import utils

ENGINE_GATE_TEXT = "BiliNote 引擎未运行。请先到「引擎与 ffmpeg」页启动引擎，以下功能暂不可用。"


class BasePage(QScrollArea):
    """所有页面继承此类：统一的滚动区、标题排版与引擎可用性门禁。"""

    navigateRequested = Signal(str)

    PAGE_KEY = ""
    PAGE_TITLE = ""
    PAGE_SUBTITLE = ""
    NEEDS_ENGINE = False

    def __init__(self, controller, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("PageScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._content = QWidget()
        self._content.setObjectName("PageContent")
        self._body = QVBoxLayout(self._content)
        self._body.setContentsMargins(26, 22, 26, 30)
        self._body.setSpacing(16)
        self._body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content.setLayout(self._body)
        self.setWidget(self._content)

        self._body.addWidget(self._build_header())

        if self.NEEDS_ENGINE:
            self._engine_gate = InlineMessage(ENGINE_GATE_TEXT, "warning")
            self._body.addWidget(self._engine_gate)
        else:
            self._engine_gate = None

        self.build()
        self._body.addStretch(1)
        self.set_engine_available(self.engine_running())

    # ------------------------------------------------------------ 构建
    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 2)
        row.setSpacing(12)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        title = QLabel(self.PAGE_TITLE)
        title.setObjectName("H1")
        title.setWordWrap(True)
        column.addWidget(title)
        if self.PAGE_SUBTITLE:
            subtitle = QLabel(self.PAGE_SUBTITLE)
            subtitle.setObjectName("Muted")
            subtitle.setWordWrap(True)
            column.addWidget(subtitle)
        row.addLayout(column, 1)

        self._header_actions = QHBoxLayout()
        self._header_actions.setContentsMargins(0, 0, 0, 0)
        self._header_actions.setSpacing(8)
        row.addLayout(self._header_actions, 0)
        self._back_button = None
        self._back_target = "settings"
        return header

    def set_back_visible(
        self,
        visible: bool,
        target: str = "settings",
        label: str = "← 返回设置",
    ) -> None:
        """隐藏的二级页面（AI/转写/引擎/日志）显示「返回设置」按钮。"""
        self._back_target = target
        if self._back_button is None:
            self._back_button = button(
                label,
                "",
                on_click=lambda: self.navigateRequested.emit(self._back_target),
            )
            self._back_button.setFixedHeight(30)
            self._header_actions.addWidget(
                self._back_button, 0, Qt.AlignmentFlag.AlignTop
            )
        self._back_button.setVisible(bool(visible))

    def build(self) -> None:
        """子类在这里创建页面内容（所有控件初始化也在这一步完成）。"""
        raise NotImplementedError

    # ------------------------------------------------------------ 生命周期
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.on_show()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.on_hide()

    def on_show(self) -> None:
        """页面变为可见时调用；子类可加载数据 / 启动轮询。"""

    def on_hide(self) -> None:
        """页面隐藏时调用；子类应停止轮询。"""

    # ------------------------------------------------------------ 状态/异步
    def status(self) -> dict:
        cached = getattr(self, "_page_status", None)
        if cached:
            return cached
        return self.controller.last_status or {}

    def on_status(self, status: dict) -> None:
        """controller.statusChanged 的转发（页面隐藏时也会收到）。"""
        self._page_status = status or {}
        if self._engine_gate is not None:
            engine = (status or {}).get("engine") or {}
            running = engine.get("state") == "running"
            if running:
                self._engine_gate.clear()
            else:
                if engine.get("state") == "error":
                    message = (
                        "BiliNote 引擎启动异常："
                        f"{engine.get('message') or '请到「引擎与 ffmpeg」页查看日志'}"
                    )
                else:
                    message = ENGINE_GATE_TEXT
                self._engine_gate.set_message(message, "warning")
            self.set_engine_available(running)

    def on_ffmpeg_progress(self, done: int, total: int) -> None:
        """controller.ffmpegProgress 的转发。"""

    def on_pairing_changed(self, state: dict) -> None:
        """controller.pairingChanged 的转发。"""

    def on_log_appended(self, line: str) -> None:
        """controller.logAppended 的转发。"""

    def on_notification(self, title: str, message: str, level: str) -> None:
        """controller.notification 的转发。"""

    def on_error(self, message: str) -> None:
        """controller.error 的转发。"""

    # ------------------------------------------------------------ 状态/异步
    def status(self) -> dict:
        return self.controller.last_status or {}

    def engine_state(self) -> str:
        return ((self.status().get("engine") or {}).get("state") or "stopped")

    def engine_running(self) -> bool:
        return self.engine_state() == "running"

    def set_engine_available(self, running: bool) -> None:
        """引擎可用性变化；需要门禁的页面在子类里禁用/启用控件。"""

    def toast(self, text: str, level: str = "info") -> None:
        window = self.window()
        show = getattr(window, "show_toast", None)
        if callable(show):
            show(text, level)

    def goto(self, page_key: str) -> None:
        self.navigateRequested.emit(page_key)

    def run_api(
        self,
        method_name: str,
        *args: Any,
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        inline: Optional[InlineMessage] = None,
        require_engine: bool = True,
        **kwargs: Any,
    ) -> bool:
        """通过 controller.run_async 调用 EngineAPI 方法（不要在 GUI 线程直连）。"""
        if require_engine and not self.engine_running():
            message = "BiliNote 引擎未运行，请先到「引擎与 ffmpeg」页启动引擎。"
            if inline is not None:
                inline.set_message(message, "warning")
            else:
                self.toast(message, "warning")
            return False
        try:
            api = self.controller.engine_api()
            method = getattr(api, method_name)
        except AttributeError:
            message = f"当前引擎客户端不支持接口 {method_name}"
            if inline is not None:
                inline.set_message(message, "error")
            else:
                self.toast(message, "error")
            return False

        def _on_error(message: str) -> None:
            text = str(message or "未知错误")
            if inline is not None:
                inline.set_message(f"操作失败：{text}", "error")
            if on_error is not None:
                on_error(text)
            else:
                self.toast(f"操作失败：{text}", "error")

        try:
            self.controller.run_async(method, *args, on_result=on_result, on_error=_on_error, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 兜底，不让 UI 崩溃
            _on_error(str(exc))
            return False
        return True

    def run_bg(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        inline: Optional[InlineMessage] = None,
        **kwargs: Any,
    ) -> bool:
        """执行任意本地/网络函数（同样走后台线程）。"""

        def _on_error(message: str) -> None:
            text = str(message or "未知错误")
            if inline is not None:
                inline.set_message(f"操作失败：{text}", "error")
            if on_error is not None:
                on_error(text)
            else:
                self.toast(f"操作失败：{text}", "error")

        try:
            self.controller.run_async(fn, *args, on_result=on_result, on_error=_on_error, **kwargs)
        except Exception as exc:  # noqa: BLE001
            _on_error(str(exc))
            return False
        return True

    # ------------------------------------------------------------ 小工具
    def add(self, widget: QWidget) -> QWidget:
        self._body.addWidget(widget)
        return widget

    def add_row(self, *widgets: QWidget, stretch_last: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        for widget in widgets:
            row.addWidget(widget, 1 if stretch_last else 0)
        self._body.addLayout(row)
        return row

    @staticmethod
    def muted(text: str) -> QLabel:
        widget = QLabel(text)
        widget.setObjectName("Muted")
        widget.setWordWrap(True)
        return widget

    @staticmethod
    def help_text(text: str) -> QLabel:
        widget = QLabel(text)
        widget.setObjectName("Help")
        widget.setWordWrap(True)
        return widget

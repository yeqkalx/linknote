"""语音转写页：转写引擎、Whisper 模型下载与引擎侧状态。"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import style, utils
from ..widgets import (
    Card,
    InlineMessage,
    StatusBadge,
    button,
    clear_layout,
    field_label,
    hint_label,
)
from .base import BasePage

FALLBACK_TYPES = [
    {"value": "fast-whisper", "label": "Faster Whisper（本地）"},
    {"value": "groq", "label": "Groq（在线）"},
    {"value": "bcut", "label": "必剪（在线）"},
    {"value": "kuaishou", "label": "快手（在线）"},
]
FALLBACK_SIZES = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]

ENGINE_HINTS = {
    "fast-whisper": "本地运行、免费、无需 API Key；首次使用需要先下载模型（tiny 约 75MB）。",
    "groq": "在线引擎：速度快，需要先在「AI 模型」页配置 Groq 供应商的 API Key。",
    "bcut": "在线引擎（必剪）：无需 API Key，偶尔会限流。",
    "kuaishou": "在线引擎（快手）：无需 API Key，可作为备选。",
    "mlx-whisper": "MLX Whisper 仅 macOS 可用，Windows 桌面端不可用。",
}


class TranscriptionPage(BasePage):
    PAGE_KEY = "transcription"
    PAGE_TITLE = "语音转写"
    PAGE_SUBTITLE = "选择把声音转成文字的引擎；本地 Whisper 需要先下载模型。"
    NEEDS_ENGINE = True

    POLL_INTERVAL_MS = 3000

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._config: dict[str, Any] = {}
        self._status_data: dict[str, Any] = {}
        self._config_loaded = False
        self._config_loading = False
        self._health_loaded = False
        self._health_loading = False
        self._rows_signature: Optional[tuple] = None
        self._poll_error_shown = False

        self._build_engine_card()
        self._build_models_card()
        self._build_health_card()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_status)

    def _build_engine_card(self) -> None:
        card = Card()
        self._engine_card = card
        self._refresh_config_button = button("刷新", "", self._reload_all)
        card.add_header(
            "转写引擎",
            "手机端任务会按这里的设置把音频转写成文字。",
            trailing=self._refresh_config_button,
        )

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(field_label("转写引擎"), 0)
        self._engine_combo = QComboBox()
        self._engine_combo.setMinimumWidth(240)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        row.addWidget(self._engine_combo, 1)
        card.body.addLayout(row)

        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(12)
        size_row.addWidget(field_label("Whisper 模型"), 0)
        self._size_combo = QComboBox()
        self._size_combo.setMinimumWidth(240)
        self._size_combo.currentIndexChanged.connect(self._on_size_changed)
        size_row.addWidget(self._size_combo, 1)
        card.body.addLayout(size_row)

        self._engine_hint = QLabel("")
        self._engine_hint.setObjectName("Help")
        self._engine_hint.setWordWrap(True)
        card.body.addWidget(self._engine_hint)

        self._groq_message = InlineMessage()
        self._groq_message.add_action("去配置 AI 模型", lambda: self.goto("ai"))
        self._groq_message.hide()
        card.body.addWidget(self._groq_message)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self._save_button = button("保存设置", "Primary", self._save_config)
        action_row.addWidget(self._save_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._config_msg = InlineMessage()
        card.body.addWidget(self._config_msg)
        self.add(card)

    def _build_models_card(self) -> None:
        card = Card()
        self._models_card = card
        self._refresh_models_button = button("刷新状态", "", self._poll_status)
        card.add_header(
            "本地模型",
            "下载后保存在 BiliNote 引擎的模型目录，不会上传到任何服务器。",
            trailing=self._refresh_models_button,
        )
        self._models_msg = InlineMessage()
        card.body.addWidget(self._models_msg)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(7)
        card.body.addWidget(self._rows_container)
        self._models_empty = QLabel("正在读取模型状态…")
        self._models_empty.setObjectName("Help")
        self._models_empty.setWordWrap(True)
        card.body.addWidget(self._models_empty)
        card.body.addWidget(
            hint_label("下载大型模型（large-v3）可能需要较长时间，请保持引擎运行。")
        )
        self.add(card)

    def _build_health_card(self) -> None:
        card = Card()
        self._health_card = card
        self._refresh_health_button = button("刷新", "", self._load_health)
        card.add_header(
            "引擎侧状态",
            "由 BiliNote 引擎 /sys_health 返回的真实状态。",
            trailing=self._refresh_health_button,
        )
        self._health_msg = InlineMessage()
        card.body.addWidget(self._health_msg)
        self._health_container = QWidget()
        self._health_layout = QVBoxLayout(self._health_container)
        self._health_layout.setContentsMargins(0, 0, 0, 0)
        self._health_layout.setSpacing(4)
        card.body.addWidget(self._health_container)
        self._health_empty = QLabel("引擎未运行，暂时无法读取系统健康状态。")
        self._health_empty.setObjectName("Help")
        self._health_empty.setWordWrap(True)
        card.body.addWidget(self._health_empty)
        self.add(card)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        if self.engine_running():
            if not self._config_loaded:
                self._load_config()
            self._load_health()
            self._poll_status()
            self._poll_timer.start()
        else:
            self._poll_timer.stop()
            self._render_health_unavailable()

    def on_hide(self) -> None:
        self._poll_timer.stop()

    def set_engine_available(self, running: bool) -> None:
        controls = [
            self._engine_combo,
            self._size_combo,
            self._save_button,
            self._refresh_config_button,
            self._refresh_models_button,
            self._refresh_health_button,
        ]
        for control in controls:
            control.setEnabled(running)
        for row_button in self._download_buttons():
            row_button.setEnabled(running)
        if not running:
            self._poll_timer.stop()
            self._render_health_unavailable()
            return
        # 引擎可用后预加载配置，页面打开时直接有内容；轮询只在页面可见时进行
        if not self._config_loaded and not self._config_loading:
            self._load_config()
        if not self._health_loaded:
            self._load_health()
        if self.isVisible():
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            self._poll_status()

    def _download_buttons(self) -> list:
        buttons = []
        for index in range(self._rows_layout.count()):
            item = self._rows_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            finder = getattr(widget, "download_button", None)
            if finder is not None:
                buttons.append(finder)
        return buttons

    # ------------------------------------------------------------ 配置
    def _reload_all(self) -> None:
        self._config_loaded = False
        self._load_config()
        self._poll_status()
        self._load_health()

    def _load_config(self) -> None:
        if self._config_loading:
            return
        self._config_loading = True
        self._refresh_config_button.setEnabled(False)
        self._config_msg.set_message("正在读取转写配置…", "info")

        def on_result(data) -> None:
            self._config_loading = False
            self._refresh_config_button.setEnabled(self.engine_running())
            self._config = data if isinstance(data, dict) else {}
            self._config_loaded = True
            self._fill_engine_combo()
            self._fill_size_combo()
            self._apply_selection()
            self._config_msg.clear()

        def on_error(message: str) -> None:
            self._config_loading = False
            self._refresh_config_button.setEnabled(self.engine_running())
            self._config_msg.set_message(f"读取转写配置失败：{message}", "error")

        self.run_api("transcriber_config", on_result=on_result, on_error=on_error)

    def _fill_engine_combo(self) -> None:
        types = self._config.get("available_types")
        if not isinstance(types, list) or not types:
            types = FALLBACK_TYPES
        mlx_available = bool(self._config.get("mlx_whisper_available"))
        self._engine_combo.blockSignals(True)
        self._engine_combo.clear()
        for item in types:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "")
            if not value:
                continue
            if value == "mlx-whisper" and not mlx_available:
                continue
            self._engine_combo.addItem(str(item.get("label") or value), value)
        self._engine_combo.blockSignals(False)
        if self._engine_combo.count() == 0:
            for item in FALLBACK_TYPES:
                self._engine_combo.addItem(item["label"], item["value"])

    def _fill_size_combo(self) -> None:
        sizes = self._config.get("whisper_model_sizes")
        if not isinstance(sizes, list) or not sizes:
            sizes = FALLBACK_SIZES
        self._size_combo.blockSignals(True)
        self._size_combo.clear()
        for size in sizes:
            value = str(size)
            self._size_combo.addItem(value, value)
        self._size_combo.blockSignals(False)

    def _apply_selection(self) -> None:
        engine_type = str(self._config.get("transcriber_type") or "fast-whisper")
        index = self._engine_combo.findData(engine_type)
        self._engine_combo.blockSignals(True)
        self._engine_combo.setCurrentIndex(index if index >= 0 else 0)
        self._engine_combo.blockSignals(False)
        size = str(self._config.get("whisper_model_size") or "tiny")
        size_index = self._size_combo.findData(size)
        self._size_combo.blockSignals(True)
        self._size_combo.setCurrentIndex(size_index if size_index >= 0 else 0)
        self._size_combo.blockSignals(False)
        self._on_engine_changed(self._engine_combo.currentIndex())

    def _on_engine_changed(self, _index: int) -> None:
        value = str(self._engine_combo.currentData() or "fast-whisper")
        self._engine_hint.setText(ENGINE_HINTS.get(value, ""))
        is_local = value == "fast-whisper"
        self._size_combo.setEnabled(is_local and self.engine_running())
        if value == "groq":
            self._groq_message.set_message(
                "Groq 转写需要先在「AI 模型」页选择 Groq 供应商并填写 API Key。",
                "warning",
            )
            self._groq_message.set_action_visible(True)
        else:
            self._groq_message.clear()

    def _on_size_changed(self, _index: int) -> None:
        """档位变化目前只影响保存值；下载按钮在模型列表里单独操作。"""

    def _save_config(self) -> None:
        engine_type = str(self._engine_combo.currentData() or "fast-whisper")
        size = str(self._size_combo.currentData() or "")
        if not size:
            self._config_msg.set_message("请选择一个 Whisper 模型档位。", "warning")
            return
        self._save_button.setEnabled(False)
        self._config_msg.set_message("正在保存转写设置…", "info")

        def on_result(_data) -> None:
            self._save_button.setEnabled(self.engine_running())
            self._config["transcriber_type"] = engine_type
            self._config["whisper_model_size"] = size
            self._config_msg.set_message("已保存，新任务将使用该转写引擎。", "success")
            self.toast("转写设置已保存", "success")
            self._load_health()

        def on_error(message: str) -> None:
            self._save_button.setEnabled(self.engine_running())
            self._config_msg.set_message(f"保存失败：{message}", "error")

        self.run_api(
            "update_transcriber_config",
            engine_type,
            size,
            on_result=on_result,
            on_error=on_error,
        )

    # ------------------------------------------------------------ 模型下载状态
    def _poll_status(self) -> None:
        if not self.engine_running():
            return

        def on_result(data) -> None:
            self._poll_error_shown = False
            self._status_data = data if isinstance(data, dict) else {}
            self._render_model_rows()
            self._refresh_models_button.setEnabled(self.engine_running())

        def on_error(message: str) -> None:
            self._refresh_models_button.setEnabled(self.engine_running())
            if not self._poll_error_shown:
                self._poll_error_shown = True
                self._models_msg.set_message(f"读取模型状态失败：{message}", "error")

        self.run_api("transcriber_status", on_result=on_result, on_error=on_error)

    def _model_rows(self) -> list[dict[str, Any]]:
        rows = self._status_data.get("whisper")
        if not isinstance(rows, list):
            rows = []
        if self._status_data.get("mlx_available"):
            mlx_rows = self._status_data.get("mlx_whisper")
            if isinstance(mlx_rows, list):
                rows = list(rows) + list(mlx_rows)
        return [row for row in rows if isinstance(row, dict)]

    def _render_model_rows(self) -> None:
        rows = self._model_rows()
        signature = tuple(
            (
                str(row.get("model_size") or ""),
                bool(row.get("downloaded")),
                bool(row.get("downloading")),
                bool(row.get("failed")),
            )
            for row in rows
        )
        if signature == self._rows_signature:
            self._models_empty.setVisible(not rows)
            return
        self._rows_signature = signature
        clear_layout(self._rows_layout)
        self._models_empty.setVisible(not rows)
        if not rows:
            self._models_empty.setText(
                "暂无模型信息：请先保存设置并确认引擎已完整启动。"
            )
            return
        self._models_empty.hide()
        for row in rows:
            widget = _ModelRow(row)
            widget.downloadRequested.connect(self._download_model)
            self._rows_layout.addWidget(widget)
        for button_widget in self._download_buttons():
            button_widget.setEnabled(self.engine_running())

    def _download_model(self, model_size: str) -> None:
        if not model_size:
            return
        self._models_msg.set_message(f"正在请求下载模型 {model_size}…", "info")

        def on_result(_data) -> None:
            self._models_msg.set_message(
                f"已开始下载 {model_size}，状态每 3 秒自动刷新。", "success"
            )
            self._poll_status()

        self.run_api(
            "download_whisper",
            model_size,
            "fast-whisper",
            on_result=on_result,
            inline=self._models_msg,
        )

    # ------------------------------------------------------------ 系统健康
    def _load_health(self) -> None:
        if not self.engine_running():
            self._render_health_unavailable()
            return
        if self._health_loading:
            return
        self._health_loading = True

        def on_result(data) -> None:
            self._health_loading = False
            self._health_loaded = True
            self._health_empty.hide()
            clear_layout(self._health_layout)
            data = data if isinstance(data, dict) else {}
            whisper = data.get("whisper_model") if isinstance(data.get("whisper_model"), dict) else {}
            rows = [
                ("BiliNote 引擎", "运行中", "ok"),
                (
                    "ffmpeg",
                    "可用" if data.get("ffmpeg") == "ok" else "缺失",
                    "ok" if data.get("ffmpeg") == "ok" else "error",
                ),
                (
                    "数据库",
                    "正常" if data.get("db") == "ok" else "异常",
                    "ok" if data.get("db") == "ok" else "error",
                ),
                ("当前转写引擎", str(whisper.get("type") or "—"), "info"),
                ("当前模型档位", str(whisper.get("size") or "—"), "info"),
            ]
            if whisper.get("checked"):
                downloaded = bool(whisper.get("downloaded"))
                rows.append(
                    (
                        "模型是否就绪",
                        "已下载" if downloaded else "未下载",
                        "ok" if downloaded else "warn",
                    )
                )
            elif whisper.get("type"):
                rows.append(("模型是否就绪", "在线引擎无需本地模型", "info"))
            for key, value, level in rows:
                self._health_layout.addWidget(_health_row(key, value, level))

        def on_error(message: str) -> None:
            self._health_loading = False
            self._health_msg.set_message(f"读取系统健康失败：{message}", "error")

        self.run_api("sys_health", on_result=on_result, on_error=on_error)

    def _render_health_unavailable(self) -> None:
        self._health_loaded = False
        self._health_loading = False
        clear_layout(self._health_layout)
        self._health_empty.show()
        self._health_msg.clear()


class _ModelRow(QFrame):
    """单个模型档位一行：名称 + 状态 + 下载按钮。"""

    downloadRequested = Signal(str)

    def __init__(self, row: dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.model_size = str(row.get("model_size") or "")
        downloaded = bool(row.get("downloaded"))
        downloading = bool(row.get("downloading"))
        failed = bool(row.get("failed"))
        error = str(row.get("error") or "")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(10)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        name = QLabel(self.model_size or "未知模型")
        name.setStyleSheet(f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}")
        column.addWidget(name)
        meta = QLabel()
        meta.setWordWrap(True)
        if downloaded:
            meta.setText("已下载，可以直接用于转写。")
        elif downloading:
            meta.setText("正在下载…")
        elif failed:
            meta.setText(f"下载失败：{utils.clip_text(error or '未知原因', 120)}")
        else:
            meta.setText("尚未下载。")
        meta.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11px; }}")
        column.addWidget(meta)
        layout.addLayout(column, 1)

        if downloaded:
            badge = StatusBadge("已下载", "ok")
        elif downloading:
            badge = StatusBadge("下载中", "busy")
        elif failed:
            badge = StatusBadge("失败", "error")
        else:
            badge = StatusBadge("未下载", "neutral")
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self.download_button = button(
            "下载" if not (downloaded or downloading) else ("下载中…" if downloading else "已就绪"),
            "" if not (downloaded or downloading) else "Primary",
            on_click=lambda: self.downloadRequested.emit(self.model_size),
        )
        self.download_button.setFixedHeight(30)
        self.download_button.setMinimumWidth(76)
        self.download_button.setEnabled(not downloaded and not downloading)
        layout.addWidget(self.download_button, 0, Qt.AlignmentFlag.AlignVCenter)


def _health_row(key: str, value: str, level: str) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(8)
    key_label = QLabel(key)
    key_label.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11.5px; }}")
    key_label.setFixedWidth(110)
    value_label = QLabel(value)
    color = {
        "ok": style.OK,
        "warn": style.WARN,
        "error": style.DANGER,
        "info": style.INK_SOFT,
    }.get(level, style.INK_SOFT)
    value_label.setStyleSheet(f"QLabel {{ color: {color}; font-size: 12px; }}")
    value_label.setWordWrap(True)
    layout.addWidget(key_label, 0)
    layout.addWidget(value_label, 1)
    return container

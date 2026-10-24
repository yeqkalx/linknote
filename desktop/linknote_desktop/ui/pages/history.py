"""历史笔记页：本地 notes/*.md + outbox 待回传笔记，Markdown 预览与文件操作。

说明：手机端生成的笔记默认回传手机；当 Relay 不可达、笔记过大或回传被拒时，
Agent 会把 Markdown 留档在 `%LOCALAPPDATA%\\LinkNote\\notes`，这里展示的就是这些
本机留档 + 仍在 outbox 里等待补传的笔记（不新增后端接口）。
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import style, utils
from ..widgets import (
    Card,
    ElidedLabel,
    EmptyState,
    InlineMessage,
    MarkdownView,
    StatTile,
    StatusBadge,
    button,
    clear_layout,
    hint_label,
)
from .base import BasePage


class HistoryPage(BasePage):
    PAGE_KEY = "history"
    PAGE_TITLE = "历史笔记"
    PAGE_SUBTITLE = "保存在这台电脑上的笔记。回传失败的笔记会自动留档，可在这里预览或打开文件。"

    def build(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._current_markdown = ""
        self._counts: dict[str, Any] = {}
        self._notes_dir = ""
        self._rows: list[_HistoryRow] = []
        self._list_items: list[QListWidgetItem] = []

        self._build_toolbar()
        self._build_stats()
        self._build_body()
        self.controller.historyChanged.connect(self._on_history_changed)

    # ------------------------------------------------------------ 构建
    def _build_toolbar(self) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._refreshed_label = QLabel("正在读取本地笔记…")
        self._refreshed_label.setObjectName("Help")
        layout.addWidget(self._refreshed_label, 1)
        self._open_dir_button = button("打开笔记目录", "", self._open_notes_dir)
        layout.addWidget(self._open_dir_button, 0)
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
        self._notes_tile = StatTile("本地笔记", "—", "notes 目录里的 Markdown")
        self._pending_tile = StatTile("待回传", "—", "Relay 恢复后自动补传")
        self._words_tile = StatTile("合计字数", "—", "中文字符 + 英文单词")
        for tile in (self._notes_tile, self._pending_tile, self._words_tile):
            layout.addWidget(tile, 1)
        self.add(row)

    def _build_body(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        # ---- 左：列表
        list_card = Card()
        self._list_count_badge = StatusBadge("0 篇", "neutral")
        list_card.add_header(
            "笔记列表",
            "点击一条笔记可在右侧预览。",
            trailing=self._list_count_badge,
        )
        self._list = QListWidget()
        self._list.setObjectName("HistoryList")
        self._list.setMinimumHeight(320)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        list_card.body.addWidget(self._list, 1)
        self._list_empty = QLabel("")
        self._list_empty.setObjectName("Help")
        self._list_empty.setWordWrap(True)
        list_card.body.addWidget(self._list_empty)
        splitter.addWidget(list_card)

        # ---- 右：预览
        preview_card = Card()
        self._preview_title_label = QLabel("笔记预览")
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self._open_file_button = button("打开原文件", "", self._open_selected_file)
        self._copy_path_button = button("复制路径", "", self._copy_selected_path)
        self._copy_markdown_button = button("复制 Markdown", "", self._copy_selected_markdown)
        for action in (self._open_file_button, self._copy_path_button, self._copy_markdown_button):
            action.setFixedHeight(30)
            action_row.addWidget(action, 0)
        action_row.addStretch(1)
        preview_card.add_header(
            "预览", "只读预览；完整内容可用「打开原文件」查看。", trailing=None
        )
        preview_card.body.addLayout(action_row)

        self._preview_meta = QLabel("")
        self._preview_meta.setObjectName("Muted")
        self._preview_meta.setWordWrap(True)
        preview_card.body.addWidget(self._preview_meta)

        self._preview_msg = InlineMessage()
        preview_card.body.addWidget(self._preview_msg)

        self._preview_view = MarkdownView()
        self._preview_view.setMinimumHeight(260)
        preview_card.body.addWidget(self._preview_view, 1)

        self._preview_empty = EmptyState(
            "还没有可预览的笔记",
            "从左边选择一条笔记，或等手机生成第一条「回传失败」的笔记后自动出现。",
        )
        preview_card.body.addWidget(self._preview_empty)
        self._preview_view.hide()
        splitter.addWidget(preview_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([360, 520])
        self.add(splitter)
        self._set_preview_actions_enabled(False)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        pass

    # ------------------------------------------------------------ 数据
    def refresh(self) -> None:
        self._refresh_button.setEnabled(False)
        self.controller.refresh_history_async()

    def _on_history_changed(self, data: dict) -> None:
        self._refresh_button.setEnabled(True)
        data = data or {}
        self._items = [item for item in (data.get("items") or []) if isinstance(item, dict)]
        self._counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
        self._notes_dir = str(data.get("notes_dir") or "")
        error = str(data.get("error") or "")
        if error:
            self._error_msg.set_message(error, "error")
        else:
            self._error_msg.clear()
        self._render_stats()
        self._rebuild_list()

    def _render_stats(self) -> None:
        notes = int(self._counts.get("notes") or 0)
        pending = int(self._counts.get("pending") or 0)
        words = int(self._counts.get("words") or 0)
        self._notes_tile.set_value(str(notes), hint="notes 目录里的 Markdown")
        self._pending_tile.set_value(
            str(pending),
            hint=(f"另有 {int(self._counts.get('failed_pending') or 0)} 条失败状态待补传" if int(self._counts.get("failed_pending") or 0) else "Relay 恢复后自动补传"),
            color=style.WARN if pending else style.INK,
        )
        self._words_tile.set_value(f"{words:,}", hint="中文字符 + 英文单词")
        self._list_count_badge.set_state(
            "info" if self._items else "neutral", f"{len(self._items)} 篇"
        )
        if not self._items:
            self._list_empty.setText(
                "还没有本地笔记。手机生成的笔记默认回传手机；"
                "当回传失败或笔记过大时，LinkNote 会自动把它们留档到本机，"
                "之后就会出现在这里。"
            )
            self._list_empty.show()
        else:
            self._list_empty.hide()

    def _rebuild_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        self._rows = []
        self._list_items = []
        for index, item in enumerate(self._items):
            list_item = QListWidgetItem()
            list_item.setSizeHint(QSize(0, 60))
            self._list.addItem(list_item)
            row = _HistoryRow(item, index)
            self._list.setItemWidget(list_item, row)
            self._rows.append(row)
            self._list_items.append(list_item)
        self._list.blockSignals(False)
        if self._items:
            self._list.setCurrentRow(0)
            self._on_selection_changed()
        else:
            self._clear_preview()

    def _on_selection_changed(self) -> None:
        current = self._list.currentRow()
        for index, row in enumerate(self._rows):
            row.set_selected(index == current)
        if 0 <= current < len(self._items):
            self._load_preview(self._items[current])
        else:
            self._clear_preview()

    def _load_preview(self, item: dict[str, Any]) -> None:
        title = str(item.get("title") or "未命名笔记")
        self._preview_title_label.setText(title)
        bits = [utils.format_platform(item.get("platform")) if item.get("platform") else "",
                str(item.get("time_label") or "")]
        chars = int(item.get("markdown_chars") or 0)
        if chars:
            bits.append(f"{chars} 字")
        bits.append(str(item.get("status_label") or ""))
        self._preview_meta.setText(" · ".join(part for part in bits if part))
        self._preview_empty.hide()
        self._preview_view.show()
        self._preview_msg.set_message("正在读取笔记内容…", "info")
        self._set_preview_actions_enabled(True)
        _current = item

        def on_result(text) -> None:
            self._current_markdown = str(text or "")
            self._preview_msg.clear()
            if self._current_markdown.strip():
                self._preview_view.set_markdown(self._current_markdown)
            else:
                self._preview_view.setPlainText("（这条笔记没有正文内容）")

        def on_error(message: str) -> None:
            self._current_markdown = ""
            self._preview_view.setPlainText("")
            self._preview_msg.set_message(f"读取笔记失败：{message}", "error")

        self.run_bg(
            self.controller.load_note_markdown,
            str(item.get("path") or ""),
            on_result=on_result,
            on_error=on_error,
        )

    def _clear_preview(self) -> None:
        self._list.setCurrentRow(-1)
        for row in self._rows:
            row.set_selected(False)
        self._current_markdown = ""
        self._preview_title_label.setText("笔记预览")
        self._preview_meta.clear()
        self._preview_msg.clear()
        self._preview_view.clear()
        self._preview_view.hide()
        self._preview_empty.show()
        self._set_preview_actions_enabled(False)

    def _set_preview_actions_enabled(self, enabled: bool) -> None:
        for action in (
            self._open_file_button,
            self._copy_path_button,
            self._copy_markdown_button,
        ):
            action.setEnabled(enabled)

    def _current_item(self) -> Optional[dict[str, Any]]:
        current = self._list.currentRow()
        if 0 <= current < len(self._items):
            return self._items[current]
        return None

    # ------------------------------------------------------------ 动作
    def _open_notes_dir(self) -> None:
        directory = self._notes_dir
        if not directory:
            try:
                directory = str(self.controller.history_dirs()[0])
            except Exception:  # noqa: BLE001
                directory = ""
        if not directory or not utils.open_path(directory):
            self.toast("笔记目录还没有创建或无法打开", "warning")

    def _open_selected_file(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if not utils.open_file(item.get("path")):
            self.toast("打不开这个笔记文件（可能已被移动或删除）", "warning")

    def _copy_selected_path(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if utils.copy_text(str(item.get("path") or "")):
            self.toast("文件路径已复制", "success")
        else:
            self.toast("复制失败", "error")

    def _copy_selected_markdown(self) -> None:
        if not self._current_markdown:
            self.toast("还没有读取到笔记内容", "warning")
            return
        if utils.copy_text(self._current_markdown):
            self.toast("Markdown 已复制", "success")
        else:
            self.toast("复制失败", "error")


class _HistoryRow(QFrame):
    """历史笔记列表里的一行（标题 + 时间/状态）。"""

    def __init__(self, item: dict[str, Any], index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._selected = False
        self._hover = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(8)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        title = str(item.get("title") or "未命名笔记")
        self._title = ElidedLabel(title, color=style.INK)
        column.addWidget(self._title)
        meta_parts = [
            utils.format_platform(item.get("platform")) if item.get("platform") else "",
            str(item.get("time_label") or ""),
        ]
        chars = int(item.get("markdown_chars") or 0)
        if chars:
            meta_parts.append(f"{chars} 字")
        self._meta = QLabel(" · ".join(part for part in meta_parts if part))
        self._meta.setObjectName("Help")
        self._meta.setWordWrap(True)
        column.addWidget(self._meta)
        layout.addLayout(column, 1)

        status = str(item.get("status") or "")
        if status == "pending":
            self._badge = StatusBadge("待回传", "warn")
        else:
            self._badge = StatusBadge("已保存", "neutral")
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_style()

    def _refresh_style(self) -> None:
        if self._selected:
            background, border = style.CORAL_SOFT, style.CORAL
        elif self._hover:
            background, border = "#EFF4FF", style.LINE_STRONG
        else:
            background, border = style.CARD_ALT, style.LINE
        self.setStyleSheet(
            "QFrame#HoverCard {"
            f"background-color: {background}; border: 1px solid {border}; border-radius: 9px;"
            "}"
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._refresh_style()
        super().leaveEvent(event)

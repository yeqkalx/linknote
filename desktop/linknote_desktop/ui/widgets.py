"""可复用 UI 控件：卡片、状态点、徽章、开关、提示、表单行、日志框等。

全部用原生 QPainter 绘制，不依赖任何外部图片或字体文件。
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import style
from . import utils

# ================================================================ 基础控件


class Card(QFrame):
    """白卡片：12px 圆角 + 细边框 + 可选轻阴影。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        shadow: bool = False,
        object_name: str = "Card",
        spacing: int = 10,
        margins: tuple[int, int, int, int] = (16, 14, 16, 14),
    ):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)
        if shadow:
            apply_shadow(self)

    @property
    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: QLayout) -> QLayout:
        self._layout.addLayout(layout)
        return layout

    def add_header(
        self,
        title: str,
        subtitle: str = "",
        trailing: Optional[QWidget] = None,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        column.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("Muted")
            subtitle_label.setWordWrap(True)
            column.addWidget(subtitle_label)
        row.addLayout(column, 1)
        if trailing is not None:
            row.addWidget(trailing, 0, Qt.AlignmentFlag.AlignTop)
        self._layout.addLayout(row)
        return row

    def add_divider(self) -> QFrame:
        line = divider()
        self._layout.addWidget(line)
        return line


class StatusDot(QWidget):
    """带柔光的小圆点；busy=True 时呼吸闪烁。"""

    def __init__(self, color: str = style.MUTED, diameter: int = 9, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._diameter = max(6, int(diameter))
        self._color = QColor(color)
        self._busy = False
        self._bright = True
        self.setFixedSize(self._diameter + 6, self._diameter + 6)
        self._timer = QTimer(self)
        self._timer.setInterval(560)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._bright = not self._bright
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_state(self, color: str, busy: bool = False) -> None:
        self.set_color(color)
        if busy and not self._timer.isActive():
            self._timer.start()
        elif not busy and self._timer.isActive():
            self._timer.stop()
            self._bright = True
        self._busy = busy
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            outer = QColor(self._color)
            outer.setAlpha(48)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(outer)
            painter.drawEllipse(QRectF(0, 0, self.width(), self.height()))
            inner = QColor(self._color)
            if self._busy and not self._bright:
                inner.setAlpha(110)
            size = float(self._diameter)
            offset = (self.width() - size) / 2.0
            painter.setBrush(inner)
            painter.drawEllipse(QRectF(offset, offset, size, size))
        finally:
            painter.end()


class StatusBadge(QLabel):
    """圆角小徽章：ok / warn / error / info / neutral。"""

    _THEMES = {
        "ok": (style.OK_BG, style.OK),
        "warn": (style.WARN_BG, "#92400E"),
        "error": (style.DANGER_BG, style.DANGER),
        "info": (style.CORAL_SOFT, style.CORAL_INK),
        "neutral": ("#F1F5FB", style.MUTED),
        "busy": (style.WARN_BG, "#92400E"),
    }

    def __init__(self, text: str = "", state: str = "neutral", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._state = ""
        self.set_state(state, text)

    def set_state(self, state: str, text: Optional[str] = None) -> None:
        if text is not None:
            self.setText(text)
        self._state = state
        background, foreground = self._THEMES.get(state, self._THEMES["neutral"])
        self.setStyleSheet(
            "QLabel {"
            f"background-color: {background}; color: {foreground};"
            "border-radius: 9px; padding: 2px 8px;"
            "font-size: 11px; font-weight: bold;"
            "}"
        )


class ToggleSwitch(QAbstractButton):
    """自绘开关（44×24），避免依赖 QSS indicator 图片。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = float(value)
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(44, 24)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            track = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            enabled = self.isEnabled()
            if self.isChecked():
                track_color = QColor(style.CORAL if enabled else "#BFD3FF")
            else:
                track_color = QColor("#CBD8EA" if enabled else "#E2E8F0")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track_color)
            painter.drawRoundedRect(track, track.height() / 2.0, track.height() / 2.0)

            knob_size = 18.0
            x = 3.0 + self._offset * (self.width() - knob_size - 6.0)
            knob = QRectF(x, 3.0, knob_size, knob_size)
            painter.setBrush(QColor(style.WHITE if enabled else "#F8FAFF"))
            painter.setPen(QPen(QColor(0, 0, 0, 22), 1))
            painter.drawEllipse(knob)
        finally:
            painter.end()


class InlineMessage(QFrame):
    """内联提示条：info / success / warning / error，可带一个文字按钮。"""

    _THEMES = {
        "info": (style.INFO_BG, style.INFO_BORDER, style.INK_SOFT, style.CORAL),
        "success": (style.OK_BG, style.OK_BORDER, style.OK, style.OK),
        "warning": (style.WARN_BG, style.WARN_BORDER, "#92400E", style.WARN),
        "error": (style.DANGER_BG, style.DANGER_BORDER, style.DANGER, style.DANGER),
    }

    def __init__(
        self,
        text: str = "",
        level: str = "info",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("InlineMessage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        self._dot = StatusDot(style.CORAL, diameter=7)
        layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignTop)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setObjectName("InlineMessageText")
        layout.addWidget(self._label, 1)
        self._layout = layout
        self._action: Optional[QPushButton] = None
        if text:
            self.set_message(text, level)
        else:
            self.hide()

    def set_message(self, text: str, level: str = "info") -> None:
        background, border, foreground, dot_color = self._THEMES.get(level, self._THEMES["info"])
        self.setStyleSheet(
            "QFrame#InlineMessage {"
            f"background-color: {background}; border: 1px solid {border}; border-radius: 9px;"
            "}"
            "QFrame#InlineMessage QLabel {"
            f"color: {foreground}; background: transparent; border: none;"
            "font-size: 12px;"
            "}"
        )
        self._dot.set_color(dot_color)
        self._label.setText(str(text or ""))
        self.setVisible(bool(text))
        self.updateGeometry()

    def clear(self) -> None:
        self._label.clear()
        self.hide()

    def add_action(self, text: str, callback: Callable[[], None]) -> QPushButton:
        if self._action is not None:
            self._layout.removeWidget(self._action)
            self._action.deleteLater()
        button = QPushButton(text)
        button.setObjectName("Link")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: callback())
        self._action = button
        self._layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return button

    def set_action_visible(self, visible: bool) -> None:
        if self._action is not None:
            self._action.setVisible(visible)


class NavButton(QAbstractButton):
    """左侧导航按钮（自绘，选中时白底 + 珊瑚竖条）。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = False

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(180, 42)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            checked = self.isChecked()
            if checked:
                painter.setPen(QPen(QColor(style.LINE), 1))
                painter.setBrush(QColor(style.CARD))
                painter.drawRoundedRect(rect, 10, 10)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(style.CORAL))
                painter.drawRoundedRect(QRectF(0.0, 11.0, 3.0, 20.0), 1.5, 1.5)
            elif self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 253, 249, 170))
                painter.drawRoundedRect(rect, 10, 10)

            font = QFont(self.font())
            font.setPointSizeF(10.5)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(style.CORAL_INK if checked else style.INK_SOFT))
            painter.drawText(
                self.rect().adjusted(16, 0, -12, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.text(),
            )
        finally:
            painter.end()


class ElidedLabel(QLabel):
    """单行省略号文本（长路径友好），完整内容放 tooltip。"""

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        color: str = style.INK_SOFT,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._full_text = str(text or "")
        self._mode = mode
        self._color = QColor(color)
        self.setToolTip(self._full_text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(40)
        self.setFullText(self._full_text)

    def setFullText(self, text: str) -> None:  # noqa: N802 - 与 Qt 命名风格保持一致
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        metrics = QFontMetrics(self.font())
        self.setFixedHeight(max(18, metrics.height() + 2))
        self.update()

    def fullText(self) -> str:  # noqa: N802
        return self._full_text

    def set_text_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setFont(self.font())
            painter.setPen(self._color)
            metrics = QFontMetrics(self.font())
            text = metrics.elidedText(self._full_text, self._mode, max(10, self.width() - 2))
            painter.drawText(
                self.rect().adjusted(1, 0, -1, 0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        finally:
            painter.end()


class InfoLine(QWidget):
    """状态卡片里的「键 — 值」行。"""

    def __init__(self, key: str, value: str = "—", parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._key = QLabel(key)
        self._key.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11.5px; }}")
        self._key.setFixedWidth(46)
        self._value = ElidedLabel(value, color=style.INK_SOFT)
        layout.addWidget(self._key, 0)
        layout.addWidget(self._value, 1)

    def setValue(self, text: str, color: str = style.INK_SOFT) -> None:  # noqa: N802
        self._value.setFullText(str(text or "—"))
        self._value.set_text_color(color)


class CodeView(QPlainTextEdit):
    """深色等宽日志块。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        wrap: bool = False,
        max_height: Optional[int] = None,
        placeholder: str = "",
    ):
        super().__init__(parent)
        self.setObjectName("CodeView")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSizeF(9.5)
        self.setFont(font)
        self._placeholder = placeholder
        self.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if wrap
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        if max_height is not None:
            self.setMaximumHeight(max_height)
            self.setMinimumHeight(min(max_height, 96))
        self.setPlaceholderText(placeholder)

    def set_text(self, text: str, stick_to_bottom: bool = True) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 6
        new_text = str(text or "").strip()
        if new_text == self.toPlainText():
            return
        self.setPlainText(new_text)
        if stick_to_bottom and at_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class EmptyMark(QWidget):
    """空状态的小图形（珊瑚色圆角方块 + 圆点）。"""

    def __init__(self, size: int = 46, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(0, 0, self.width(), self.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(style.CORAL_SOFT))
            painter.drawRoundedRect(rect, 14, 14)
            painter.setPen(QPen(QColor(style.CORAL), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            center = rect.center()
            painter.drawEllipse(center, 9, 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(style.CORAL))
            painter.drawEllipse(center, 3, 3)
        finally:
            painter.end()


class EmptyState(QWidget):
    """居中的空状态 / 错误状态。"""

    def __init__(
        self,
        title: str,
        text: str = "",
        parent: Optional[QWidget] = None,
        *,
        mark: bool = True,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 18, 8, 20)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        if mark:
            empty_mark = EmptyMark()
            layout.addWidget(empty_mark, 0, Qt.AlignmentFlag.AlignHCenter)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"QLabel {{ color: {style.INK}; font-size: 13px; font-weight: bold; }}")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        if text:
            text_label = QLabel(text)
            text_label.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 12px; }}")
            text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text_label.setWordWrap(True)
            layout.addWidget(text_label)


class DeviceRow(QFrame):
    """已配对手机一行。"""

    revokeRequested = Signal(str)

    def __init__(
        self,
        device: dict,
        parent: Optional[QWidget] = None,
        *,
        revoke_text: str = "撤下",
    ):
        super().__init__(parent)
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(10)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        name = QLabel(str(device.get("name") or "未知设备"))
        name.setStyleSheet(f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}")
        meta_bits = [
            utils.format_platform(device.get("platform")),
            f"配对于 {utils.format_datetime(device.get('paired_at'))}",
        ]
        last_seen = device.get("last_seen_at")
        if last_seen:
            meta_bits.append(f"最近在线 {utils.format_datetime(last_seen)}")
        meta = QLabel(" · ".join(meta_bits))
        meta.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11px; }}")
        meta.setWordWrap(True)
        column.addWidget(name)
        column.addWidget(meta)
        layout.addLayout(column, 1)

        revoke = button(revoke_text, "Danger")
        revoke.setFixedHeight(30)
        revoke.setMinimumWidth(64)
        revoke.clicked.connect(lambda: self.revokeRequested.emit(str(device.get("id") or "")))
        layout.addWidget(revoke, 0, Qt.AlignmentFlag.AlignVCenter)


class Toast(QFrame):
    """窗口底部的浮动提示（深色胶囊，自动消失）。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(9)
        self._dot = StatusDot(style.CORAL, diameter=7)
        layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._label = QLabel("")
        self._label.setStyleSheet(f"QLabel {{ color: {style.TOAST_INK}; font-size: 12.5px; }}")
        layout.addWidget(self._label, 1)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(24, 16, 8, 90))
        self.setGraphicsEffect(shadow)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self._label.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, level: str = "info") -> None:
        colors = {
            "info": style.CORAL,
            "success": style.OK,
            "warning": style.WARN,
            "error": style.DANGER,
        }
        self._dot.set_color(colors.get(level, style.CORAL))
        self._label.setText(str(text or ""))
        self.setMinimumWidth(0)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hide_timer.start(3600)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        max_width = max(240, parent.width() - 160)
        if self.width() > max_width:
            self.setFixedWidth(max_width)
            self.adjustSize()
        x = max(20, int((parent.width() - self.width()) / 2))
        y = max(20, parent.height() - self.height() - 26)
        self.move(x, y)


# ================================================================ 工具函数


def apply_shadow(widget: QWidget, blur: int = 26, dy: int = 7) -> QGraphicsDropShadowEffect:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(48, 33, 18, 42))
    widget.setGraphicsEffect(effect)
    return effect


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


def button(
    text: str,
    kind: str = "",
    on_click: Optional[Callable[[], None]] = None,
    parent: Optional[QWidget] = None,
) -> QPushButton:
    widget = QPushButton(text, parent)
    if kind:
        widget.setObjectName(kind)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        widget.clicked.connect(lambda: on_click())
    return widget


def label(text: str, object_name: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    if wrap:
        widget.setWordWrap(True)
    return widget


def hint_label(text: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName("Help")
    widget.setWordWrap(True)
    return widget


def field_label(text: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName("FieldLabel")
    return widget


def form_field(
    title: str,
    field: QWidget,
    hint: str = "",
) -> QWidget:
    """竖排表单行：字段名 + 控件 + 帮助文字。"""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(field_label(title))
    layout.addWidget(field)
    if hint:
        layout.addWidget(hint_label(hint))
    return container


def form_row(
    title: str,
    field: QWidget,
    hint: str = "",
) -> QWidget:
    """横排表单行：左侧字段名，右侧控件。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    label_widget = field_label(title)
    label_widget.setFixedWidth(88)
    layout.addWidget(label_widget, 0, Qt.AlignmentFlag.AlignVCenter)
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    column.addWidget(field)
    if hint:
        column.addWidget(hint_label(hint))
    layout.addLayout(column, 1)
    return container


def switch_row(title: str, description: str, switch: ToggleSwitch) -> QWidget:
    """设置页的开关行：标题 + 说明 + 右侧开关。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(12)
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    title_label = QLabel(title)
    title_label.setStyleSheet(f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}")
    description_label = QLabel(description)
    description_label.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11.5px; }}")
    description_label.setWordWrap(True)
    column.addWidget(title_label)
    column.addWidget(description_label)
    layout.addLayout(column, 1)
    layout.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
    return container


def progress_bar() -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    return bar


def set_button_kind(widget: QPushButton, kind: str) -> None:
    """动态切换按钮样式（Primary / Danger / 空=次按钮）并刷新 QSS。"""
    if widget.objectName() == kind:
        return
    widget.setObjectName(kind)
    button_style = widget.style()
    if button_style is not None:
        button_style.unpolish(widget)
        button_style.polish(widget)
    widget.update()


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)


def toolbar(*widgets: QWidget, spacing: int = 8) -> QWidget:
    """一行操作按钮容器（右对齐）。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    layout.addStretch(1)
    for widget in widgets:
        layout.addWidget(widget, 0)
    return container


class CheckIndicator(QWidget):
    """自绘复选框（品牌蓝，不依赖 QSS indicator 图片）。"""

    def __init__(self, parent: Optional[QWidget] = None, size: int = 18):
        super().__init__(parent)
        self._checked = False
        self._enabled = True
        self.setFixedSize(size, size)

    def set_checked(self, checked: bool) -> None:
        self._checked = bool(checked)
        self.update()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            if self._checked:
                painter.setPen(QPen(QColor(style.CORAL_STRONG), 1))
                painter.setBrush(QColor(style.CORAL if self._enabled else "#BFD3FF"))
            else:
                border = style.LINE_STRONG if self._enabled else style.LINE
                painter.setPen(QPen(QColor(border), 1.4))
                painter.setBrush(QColor(style.CARD if self._enabled else style.PAPER_DEEP))
            painter.drawRoundedRect(rect, 5.5, 5.5)
            if self._checked:
                pen = QPen(QColor(style.WHITE), 2.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                left = self.width() * 0.24
                mid_x = self.width() * 0.44
                right = self.width() * 0.76
                top = self.height() * 0.52
                bottom = self.height() * 0.74
                painter.drawLine(int(left), int(top), int(mid_x), int(bottom))
                painter.drawLine(int(mid_x), int(bottom), int(right), int(self.height() * 0.28))
        finally:
            painter.end()


class CheckRow(QFrame):
    """可勾选的单行（模型列表等）：点整行切换勾选。"""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: Optional[QWidget] = None,
        *,
        badge_text: str = "",
        badge_state: str = "neutral",
    ):
        super().__init__(parent)
        self.setObjectName("CheckRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._checked = False
        self._hover = False
        self._enabled = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)

        self.indicator = CheckIndicator(self)
        layout.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignVCenter)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        self._title = QLabel(title)
        self._title.setWordWrap(True)
        column.addWidget(self._title)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        column.addWidget(self._subtitle)
        layout.addLayout(column, 1)

        self._badge: Optional[StatusBadge] = None
        if badge_text:
            self._badge = StatusBadge(badge_text, badge_state)
            layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self._refresh_style()

    # ---------------------------------------------------------------- 状态
    def isChecked(self) -> bool:  # noqa: N802 - 与 Qt 命名一致
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self._checked = bool(checked)
        self.indicator.set_checked(self._checked)
        self._refresh_style()

    def set_badge(self, text: str, state: str = "neutral") -> None:
        if self._badge is None:
            return
        self._badge.set_state(state, text)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._enabled = bool(enabled)
        self.indicator.set_enabled(self._enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        self._refresh_style()

    def _refresh_style(self) -> None:
        if not self._enabled:
            background, border = style.PAPER_DEEP, style.LINE
            title_color, subtitle_color = style.MUTED, style.MUTED
        elif self._checked:
            background, border = style.CORAL_SOFT, style.CORAL
            title_color, subtitle_color = style.INK, style.CORAL_INK
        elif self._hover:
            background, border = "#EFF4FF", style.LINE_STRONG
            title_color, subtitle_color = style.INK, style.MUTED
        else:
            background, border = style.CARD_ALT, style.LINE
            title_color, subtitle_color = style.INK, style.MUTED
        self.setStyleSheet(
            "QFrame#CheckRow {"
            f"background-color: {background}; border: 1px solid {border}; border-radius: 9px;"
            "}"
        )
        weight = "bold" if self._checked else "normal"
        self._title.setStyleSheet(
            f"QLabel {{ color: {title_color}; font-size: 12.5px; font-weight: {weight}; "
            "background: transparent; border: none; }"
        )
        self._subtitle.setStyleSheet(
            f"QLabel {{ color: {subtitle_color}; font-size: 11px; "
            "background: transparent; border: none; }"
        )

    # ---------------------------------------------------------------- 事件
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._enabled and event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._refresh_style()
        super().leaveEvent(event)


class MarkdownView(QTextBrowser):
    """只读 Markdown 预览（Qt 原生渲染，不加载外部资源）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("MarkdownView")
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(4)

    def set_markdown(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            self.setPlainText("")
            return
        try:
            self.setMarkdown(content)
        except Exception:  # noqa: BLE001 - 极端 Markdown 也不该让预览崩
            self.setPlainText(content)

    def markdown_text(self) -> str:
        return self.toPlainText()


class StatTile(QFrame):
    """仪表盘风格的小统计块（数值 + 说明）。"""

    def __init__(
        self,
        label: str,
        value: str = "—",
        hint: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("StatTile")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(1)
        label_widget = QLabel(label)
        label_widget.setObjectName("Help")
        layout.addWidget(label_widget)
        self._value = QLabel(value)
        self._value.setObjectName("StatValue")
        layout.addWidget(self._value)
        self._hint = QLabel(hint)
        self._hint.setObjectName("Help")
        self._hint.setWordWrap(True)
        self._hint.setVisible(bool(hint))
        layout.addWidget(self._hint)

    def set_value(self, value: str, hint: Optional[str] = None, color: str = "") -> None:
        self._value.setText(str(value))
        if color:
            self._value.setStyleSheet(f"QLabel#StatValue {{ color: {color}; }}")
        if hint is not None:
            self._hint.setText(hint)
            self._hint.setVisible(bool(hint))


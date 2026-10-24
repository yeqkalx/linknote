"""UI 通用工具：状态推导、时间/路径格式化、图标与二维码生成。"""
from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path
from typing import Any, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from ..config import (  # noqa: F401  (部分常量转给页面直接使用)
    APP_NAME,
    APP_NAME_CN,
    APP_VERSION,
    DEFAULT_RELAY_URL,
    resource_dir,
)

from . import style

# ---------------------------------------------------------------- 资源 / 图标

def resource_path(name: str) -> str:
    return str(Path(resource_dir()) / name)


_APP_ICON: Optional[QIcon] = None
_STATUS_ICONS: dict[str, QIcon] = {}


def app_icon() -> QIcon:
    global _APP_ICON
    if _APP_ICON is None:
        icon = QIcon(resource_path("icon.ico"))
        if icon.isNull():
            icon = QIcon(resource_path("icon.png"))
        _APP_ICON = icon
    return _APP_ICON


def app_pixmap(size: int) -> QPixmap:
    pix = QPixmap(resource_path("icon.png"))
    if pix.isNull():
        return pix
    return pix.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def status_icon(state: str) -> QIcon:
    """在品牌图标右下角叠加绿/黄/红状态点，用于托盘。"""
    key = state if state in style.STATE_COLORS else "unknown"
    cached = _STATUS_ICONS.get(key)
    if cached is not None:
        return cached

    base = QPixmap(resource_path("icon.png"))
    canvas = QPixmap(32, 32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not base.isNull():
            scaled = base.scaled(
                30,
                30,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(1, 1, scaled)
        color = QColor(style.STATE_COLORS[key])
        painter.setPen(QPen(QColor(style.WHITE), 2))
        painter.setBrush(color)
        painter.drawEllipse(QRectF(19.0, 19.0, 12.0, 12.0))
    finally:
        painter.end()
    icon = QIcon(canvas)
    _STATUS_ICONS[key] = icon
    return icon


def state_color(state: str) -> str:
    return style.STATE_COLORS.get(state, style.MUTED)


# ---------------------------------------------------------------- 状态推导

def engine_running(status: Optional[dict]) -> bool:
    return ((status or {}).get("engine") or {}).get("state") == "running"


def agent_running(status: Optional[dict]) -> bool:
    return bool(((status or {}).get("agent") or {}).get("running"))


def agent_paired(status: Optional[dict]) -> bool:
    return bool(((status or {}).get("agent") or {}).get("paired"))


def relay_online(status: Optional[dict]) -> bool:
    """兼容 controller 的状态结构。

    controller 目前把 `/agent/me` 的结果塞进 `relay`，而该接口没有顶层 `online`
    字段，所以这里除了 `online` 还会看 `agent.online` / `devices`+`queue`。
    """
    relay = ((status or {}).get("relay") or {})
    if relay.get("online"):
        return True
    agent = relay.get("agent") or {}
    if isinstance(agent, dict) and agent.get("online"):
        return True
    if not relay.get("error") and relay.get("devices") is not None and relay.get("queue") is not None:
        return True
    return False


def overall_state(status: Optional[dict]) -> str:
    """托盘/侧栏汇总状态：ok（全绿）/ warn（部分）/ error（出错或未配对）。"""
    if not status:
        return "unknown"
    engine = status.get("engine") or {}
    agent = status.get("agent") or {}
    engine_state = engine.get("state") or "stopped"
    if engine_state == "error" or agent.get("error"):
        return "error"
    if not agent.get("paired"):
        return "error"
    if engine_state == "running" and agent.get("running") and relay_online(status):
        return "ok"
    return "warn"


def overall_text(status: Optional[dict]) -> str:
    state = overall_state(status)
    return {
        "ok": "一切就绪",
        "warn": "部分服务未就绪",
        "error": "需要处理",
        "unknown": "正在初始化",
    }.get(state, "正在初始化")


def engine_state_text(state: str, message: str = "") -> Tuple[str, str, str]:
    """返回 (文案, 级别, 颜色)。级别用于状态点（ok/warn/error/idle）。"""
    mapping = {
        "running": ("运行中", "ok", style.OK),
        "starting": ("启动中", "warn", style.WARN),
        "error": ("异常", "error", style.DANGER),
        "stopped": ("已停止", "idle", style.IDLE),
    }
    text, level, color = mapping.get(state or "stopped", ("未知", "idle", style.IDLE))
    if state == "error" and message:
        text = f"异常：{message}"
    return text, level, color


def tray_status_text(status: Optional[dict]) -> str:
    if not status:
        return "正在启动…"
    engine = status.get("engine") or {}
    agent = status.get("agent") or {}
    relay = status.get("relay") or {}
    if not agent.get("paired"):
        return "未配对 · 等待手机连接"
    parts = []
    engine_state = engine.get("state") or "stopped"
    parts.append(
        {
            "running": "引擎运行",
            "starting": "引擎启动中",
            "error": "引擎异常",
        }.get(engine_state, "引擎未启动")
    )
    parts.append("Agent 在线" if agent.get("running") else "Agent 未启动")
    if not relay_online(status):
        parts.append("Relay 离线")
    queue = relay.get("queue") or {}
    waiting = int(queue.get("queued") or 0) + int(queue.get("running") or 0)
    text = " · ".join(parts)
    if waiting:
        text += f" · 队列 {waiting}"
    return text


# ---------------------------------------------------------------- 时间/格式化

def _parse_datetime(value: Any) -> Optional[_dt.datetime]:
    if not value:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def format_datetime(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "—"
    try:
        local = parsed.astimezone()
    except (OSError, ValueError):
        local = parsed
    return local.strftime("%Y-%m-%d %H:%M")


def format_time_only(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "—"
    try:
        local = parsed.astimezone()
    except (OSError, ValueError):
        local = parsed
    return local.strftime("%H:%M:%S")


def format_remaining(expires_at: Any) -> Tuple[str, bool]:
    """配对码倒计时：(文案, 是否已过期)。"""
    parsed = _parse_datetime(expires_at)
    if parsed is None:
        return "有效期未知", False
    now = _dt.datetime.now(_dt.timezone.utc)
    remain = (parsed - now).total_seconds()
    if remain <= 0:
        return "配对码已过期", True
    minutes, seconds = divmod(int(remain), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"剩余 {hours} 小时 {minutes:02d} 分", False
    return f"剩余 {minutes:02d}:{seconds:02d}", False


def format_platform(value: Any) -> str:
    mapping = {
        "windows": "Windows 电脑",
        "android": "Android 手机",
        "ios": "iPhone",
        "ipados": "iPad",
        "web": "网页",
        "macos": "Mac",
        "linux": "Linux",
    }
    key = str(value or "").strip().lower()
    return mapping.get(key, str(value or "未知设备"))


def format_bytes(count: Any) -> str:
    try:
        value = float(count)
    except (TypeError, ValueError):
        return "0 B"
    if value <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = min(int(math.log(value, 1024)), len(units) - 1)
    scaled = value / (1024 ** index)
    if index == 0:
        return f"{int(scaled)} {units[index]}"
    return f"{scaled:.1f} {units[index]}"


def format_size_pair(done: int, total: int) -> str:
    if total and total > 0:
        percent = max(0, min(100, int(done * 100 / total)))
        return f"{format_bytes(done)} / {format_bytes(total)} · {percent}%"
    return format_bytes(done)


def normalize_pair_code(code: Any) -> str:
    """8 位配对码 → XXXX-XXXX 展示形式。"""
    raw = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:]}"
    return str(code or "").strip().upper()


def clip_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# ---------------------------------------------------------------- 二维码

def pairing_qr_pixmap(code: str, size: int = 136, relay_url: str | None = None) -> QPixmap:
    """把配对链接画成二维码（手机相机扫码打开 LinkNote 并自动填写配对码）。"""
    try:
        import qrcode
        from urllib.parse import quote

        base = (relay_url or DEFAULT_RELAY_URL).rstrip("/")
        payload = f"{base}/#/pair?code={quote(str(code or ''), safe='')}"
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        image = qr.make_image(fill_color=style.INK, back_color="#FFFFFF").convert("RGB")
        width, height = image.size
        raw = image.tobytes("raw", "RGB")
        qimage = QImage(raw, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimage)
        return pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    except Exception:  # noqa: BLE001 - 二维码生成失败不应影响配对流程
        return QPixmap()


# ---------------------------------------------------------------- 系统交互

def copy_text(text: str) -> bool:
    try:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(str(text or ""))
        return True
    except Exception:  # noqa: BLE001
        return False


def open_path(path: Any) -> bool:
    """在资源管理器里打开目录；传文件时打开其所在目录。"""
    if not path:
        return False
    try:
        target = Path(str(path))
        if not target.is_dir():
            target = target.parent
        if not target.exists():
            return False
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))))
    except (OSError, ValueError):
        return False

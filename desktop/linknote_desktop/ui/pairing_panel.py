"""配对面板：状态页与首次配置向导共用同一套配对 UI。

设计目标（用户反馈）：
- 配对成功后配对区域不消失：始终显示已配对设备 + 添加/更换/解除入口；
- 等待中 / 成功 / 过期三种状态清晰可辨；
- 二维码继续支持扫码自动填码。

面板是自包含的 Card：宿主只需要转发 controller 的 status / pairingChanged 事件，
并把 ``message`` 信号接到自己的 toast / 内联提示上。
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from . import style, utils
from .widgets import (
    Card,
    DeviceRow,
    InlineMessage,
    StatusBadge,
    StatusDot,
    button,
    clear_layout,
)


class PairingPanel(Card):
    """配对码 + 二维码 + 已配对设备（始终可见）+ 三态状态机。"""

    message = Signal(str, str)  # (提示文案, info/success/warning/error)

    def __init__(self, controller, parent: Optional[QWidget] = None):
        super().__init__(parent, spacing=10)
        self.controller = controller
        self._status: dict[str, Any] = {}
        self._pairing: dict[str, Any] = dict(controller.pairing_state() or {})
        self._devices: list[dict[str, Any]] = []
        self._devices_signature: tuple = ()
        self._success_device: dict[str, Any] = {}
        self._inflight = False
        self._qr_code = ""

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick_countdown)

        self._build_ui()

    # ================================================================ 构建
    def _build_ui(self) -> None:
        self._state_badge = StatusBadge("未配对", "neutral")
        self.add_header(
            "配对手机",
            "手机 LinkNote 输入配对码或扫码，就能把视频链接发到这台电脑。",
            trailing=self._state_badge,
        )

        state_row = QHBoxLayout()
        state_row.setContentsMargins(0, 0, 0, 0)
        state_row.setSpacing(8)
        self._state_dot = StatusDot(style.IDLE, 8)
        state_row.addWidget(self._state_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._state_label = QLabel("还没有配对手机")
        self._state_label.setWordWrap(True)
        self._state_label.setStyleSheet(
            f"QLabel {{ color: {style.INK_SOFT}; font-size: 12.5px; font-weight: bold; }}"
        )
        state_row.addWidget(self._state_label, 1)
        self._countdown_label = QLabel("")
        self._countdown_label.setObjectName("Muted")
        state_row.addWidget(self._countdown_label, 0)
        self.body.addLayout(state_row)

        # ---- 有配对码：大号码 + 二维码
        self._code_box = QWidget()
        code_row = QHBoxLayout(self._code_box)
        code_row.setContentsMargins(0, 0, 0, 0)
        code_row.setSpacing(20)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(5)

        self._pair_code_label = QLabel("")
        code_font = QFont("Consolas")
        code_font.setStyleHint(QFont.StyleHint.Monospace)
        code_font.setPointSizeF(22.0)
        code_font.setWeight(QFont.Weight.Bold)
        code_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.0)
        self._pair_code_label.setFont(code_font)
        self._pair_code_label.setStyleSheet(f"QLabel {{ color: {style.CORAL_INK}; }}")
        self._pair_code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        left.addWidget(self._pair_code_label)

        self._pair_expiry_label = QLabel("")
        self._pair_expiry_label.setObjectName("Muted")
        left.addWidget(self._pair_expiry_label)

        self._pair_relay_label = QLabel("")
        self._pair_relay_label.setObjectName("MonoMuted")
        self._pair_relay_label.setWordWrap(True)
        left.addWidget(self._pair_relay_label)

        hint = QLabel("手机打开 LinkNote → 配对 → 扫码或输入配对码（10 分钟内有效）。")
        hint.setObjectName("Help")
        hint.setWordWrap(True)
        left.addWidget(hint)
        left.addSpacing(2)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self._copy_button = button("复制配对码", "Primary", self._copy_pair_code)
        self._cancel_button = button("取消", "", self._cancel_pairing)
        button_row.addWidget(self._copy_button, 0)
        button_row.addWidget(self._cancel_button, 0)
        button_row.addStretch(1)
        left.addLayout(button_row)
        code_row.addLayout(left, 1)

        qr_frame = QFrame()
        qr_frame.setObjectName("QRFrame")
        qr_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        qr_frame.setFixedSize(168, 196)
        qr_layout = QVBoxLayout(qr_frame)
        qr_layout.setContentsMargins(10, 10, 10, 8)
        qr_layout.setSpacing(4)
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(146, 146)
        qr_layout.addWidget(self._qr_label, 0, Qt.AlignmentFlag.AlignHCenter)
        qr_caption = QLabel("手机扫码自动填写")
        qr_caption.setObjectName("Muted")
        qr_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(qr_caption)
        code_row.addWidget(qr_frame, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(self._code_box)

        # ---- 没有配对码：引导生成 / 已配对后的换机提示
        self._idle_box = QWidget()
        idle_layout = QVBoxLayout(self._idle_box)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        idle_layout.setSpacing(8)
        self._idle_msg = InlineMessage()
        idle_layout.addWidget(self._idle_msg)
        idle_row = QHBoxLayout()
        idle_row.setContentsMargins(0, 0, 0, 0)
        idle_row.setSpacing(16)
        idle_column = QVBoxLayout()
        idle_column.setContentsMargins(0, 0, 0, 0)
        idle_column.setSpacing(2)
        self._idle_title = QLabel("")
        self._idle_title.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 13px; font-weight: bold; }}"
        )
        self._idle_text = QLabel("")
        self._idle_text.setObjectName("Help")
        self._idle_text.setWordWrap(True)
        idle_column.addWidget(self._idle_title)
        idle_column.addWidget(self._idle_text)
        idle_row.addLayout(idle_column, 1)
        self._generate_button = button("生成配对码", "Primary", self._start_pairing)
        self._generate_button.setMinimumWidth(122)
        idle_row.addWidget(self._generate_button, 0, Qt.AlignmentFlag.AlignVCenter)
        idle_layout.addLayout(idle_row)
        self.body.addWidget(self._idle_box)

        # ---- 配对码过期
        self._expired_box = QWidget()
        expired_row = QHBoxLayout(self._expired_box)
        expired_row.setContentsMargins(0, 0, 0, 0)
        expired_row.setSpacing(12)
        self._expired_msg = InlineMessage("配对码已过期，请重新生成一个。", "warning")
        expired_row.addWidget(self._expired_msg, 1, Qt.AlignmentFlag.AlignVCenter)
        self._regenerate_button = button("重新生成", "Primary", self._start_pairing)
        expired_row.addWidget(self._regenerate_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addWidget(self._expired_box)

        self._msg = InlineMessage()
        self.body.addWidget(self._msg)

        self.add_divider()

        # ---- 已配对设备（始终显示）
        devices_header = QHBoxLayout()
        devices_header.setContentsMargins(0, 0, 0, 0)
        devices_header.setSpacing(8)
        self._devices_title = QLabel("已配对设备")
        self._devices_title.setObjectName("CardTitle")
        devices_header.addWidget(self._devices_title, 0)
        self._devices_count = StatusBadge("0 台", "neutral")
        devices_header.addWidget(self._devices_count, 0)
        devices_header.addStretch(1)
        self._add_device_button = button("添加/更换手机", "", self._start_pairing)
        devices_header.addWidget(self._add_device_button, 0)
        self._unpair_button = button("解除本机配对", "Danger", self._unpair)
        devices_header.addWidget(self._unpair_button, 0)
        self.body.addLayout(devices_header)

        self._devices_container = QWidget()
        self._devices_layout = QVBoxLayout(self._devices_container)
        self._devices_layout.setContentsMargins(0, 0, 0, 0)
        self._devices_layout.setSpacing(7)
        self.body.addWidget(self._devices_container)

        self._devices_empty = QLabel("")
        self._devices_empty.setObjectName("Help")
        self._devices_empty.setWordWrap(True)
        self.body.addWidget(self._devices_empty)

        self._devices_hint = QLabel(
            "「添加/更换手机」会生成新的配对码，旧手机不会被自动移除；"
            "如不再使用旧设备，可在上方逐个解除。"
        )
        self._devices_hint.setObjectName("Help")
        self._devices_hint.setWordWrap(True)
        self.body.addWidget(self._devices_hint)

    # ================================================================ 对外接口
    def update_status(self, status: Optional[dict]) -> None:
        self._status = status or {}
        self._render()

    def update_pairing(self, state: Optional[dict]) -> None:
        self._pairing = dict(state or {})
        status = str(self._pairing.get("status") or "")
        # 收到 controller 的配对事件说明网络请求已有结果：退出 loading 态
        self._inflight = False
        if status:
            self._msg.clear()
        if status == "claimed":
            device = self._pairing.get("device")
            if isinstance(device, dict) and device.get("name"):
                self._success_device = dict(device)
        self._render()

    def refresh(self) -> None:
        """页面显示时从 controller 同步一次（配对状态 + 最新 status）。"""
        self._pairing = dict(self.controller.pairing_state() or {})
        last_status = getattr(self.controller, "last_status", None) or {}
        if last_status:
            self._status = last_status
        self._render()

    def ensure_pairing_code(self) -> None:
        """onboarding 配对步骤用：没有配对码就自动生成一个。"""
        self.refresh()
        if not self._relay_configured():
            return
        if self._device_count() > 0 or bool(getattr(self.controller.agent, "is_paired", False)):
            return
        state = self._pairing or {}
        code = str(state.get("code") or "")
        status = str(state.get("status") or "")
        if code and status not in ("expired",):
            return
        self._start_pairing()

    def on_error(self, text: str) -> None:
        self._inflight = False
        self._msg.set_message(f"操作失败：{text}", "error")
        self._render()

    # ================================================================ 状态渲染
    def _relay_url(self) -> str:
        relay = (self._status or {}).get("relay") or {}
        return str(relay.get("url") or self.controller.config.relay_base_url or "").strip()

    def _relay_configured(self) -> bool:
        # Relay 地址以 config 为准：v0.5 起没有默认地址，未配置时禁止生成配对码。
        return bool(str(self.controller.config.relay_base_url or "").strip())

    def _device_count(self) -> int:
        return len(self._devices) if self._devices else 0

    def _paired(self) -> bool:
        agent = (self._status or {}).get("agent") or {}
        if agent.get("paired"):
            return True
        if self._devices:
            return True
        try:
            return bool(self.controller.agent.is_paired)
        except Exception:  # noqa: BLE001
            return False

    def _render(self) -> None:
        status = self._status or {}
        relay = status.get("relay") or {}
        pairing = self._pairing or {}
        code = utils.normalize_pair_code(pairing.get("code")) if pairing.get("code") else ""
        state = str(pairing.get("status") or "")
        remaining, expired = utils.format_remaining(pairing.get("expires_at"))
        if code and (expired or state == "expired"):
            state = "expired"
        claimed = state == "claimed"
        paired = self._paired()
        if claimed:
            self._success_device = self._success_device or {}

        success_name = str((self._success_device or {}).get("name") or "")
        if claimed or (paired and success_name and not code):
            self._state_badge.set_state("ok", "配对成功")
            self._state_dot.set_state(style.OK, busy=False)
            self._state_label.setText(
                f"配对成功：{success_name} 已绑定" if success_name else "配对成功，可以开始发链接了"
            )
            self._state_label.setStyleSheet(
                f"QLabel {{ color: {style.OK}; font-size: 12.5px; font-weight: bold; }}"
            )
            self._countdown_label.setText("")
        elif state == "expired":
            self._state_badge.set_state("warn", "配对码已过期")
            self._state_dot.set_state(style.WARN, busy=False)
            self._state_label.setText("配对码已过期，请重新生成")
            self._state_label.setStyleSheet(
                f"QLabel {{ color: {style.WARN}; font-size: 12.5px; font-weight: bold; }}"
            )
            self._countdown_label.setText("")
        elif state == "pending" and code:
            self._state_badge.set_state("warn", "等待手机领取")
            self._state_dot.set_state(style.WARN, busy=True)
            self._state_label.setText("等待手机领取配对码…")
            self._state_label.setStyleSheet(
                f"QLabel {{ color: {style.WARN}; font-size: 12.5px; font-weight: bold; }}"
            )
            self._countdown_label.setText(remaining)
        elif paired:
            device_count = self._device_count()
            count_text = f"已配对 {device_count} 台设备" if device_count else "本机已配对"
            self._state_badge.set_state("ok", "已配对")
            self._state_dot.set_state(style.OK, busy=False)
            self._state_label.setText(count_text)
            self._state_label.setStyleSheet(
                f"QLabel {{ color: {style.OK}; font-size: 12.5px; font-weight: bold; }}"
            )
            self._countdown_label.setText("")
        else:
            self._state_badge.set_state("neutral", "未配对")
            self._state_dot.set_state(style.IDLE, busy=False)
            self._state_label.setText("还没有配对手机")
            self._state_label.setStyleSheet(
                f"QLabel {{ color: {style.MUTED}; font-size: 12.5px; font-weight: bold; }}"
            )
            self._countdown_label.setText("")

        show_code = bool(code) and state not in ("expired", "claimed")
        self._code_box.setVisible(show_code)
        self._expired_box.setVisible(bool(code) and state == "expired")
        # 未生成配对码且未配对时才显示「生成配对码」引导；已配对/刚成功时
        # 由顶部状态行 + 设备区承载信息，避免重复的大块引导。
        self._idle_box.setVisible(
            not show_code and state != "expired" and not paired and not claimed
        )

        if show_code:
            self._render_code(code, pairing, relay, remaining)
            if not self._countdown.isActive():
                self._countdown.start()
        else:
            self._countdown.stop()
            if self._idle_box.isVisible():
                self._render_idle()

        self._render_devices(paired)
        self._render_buttons()

    def _render_code(
        self,
        code: str,
        pairing: dict[str, Any],
        relay: dict[str, Any],
        remaining: str,
    ) -> None:
        self._pair_code_label.setText(code)
        self._pair_expiry_label.setText(remaining)
        relay_url = str(
            pairing.get("relay_url")
            or relay.get("url")
            or self.controller.config.relay_base_url
            or ""
        )
        self._pair_relay_label.setText(f"Relay：{relay_url}")
        if code != self._qr_code:
            self._qr_code = code
            pixmap = utils.pairing_qr_pixmap(code, 146, relay_url)
            if pixmap.isNull():
                self._qr_label.setText("二维码生成失败")
            else:
                self._qr_label.setPixmap(pixmap)

    def _render_idle(self) -> None:
        self._idle_msg.clear()
        if not self._relay_configured():
            self._idle_title.setText("还没有配置 Relay")
            self._idle_text.setText(
                "先按 docs/self-hosting.md 用你自己的 Cloudflare 部署 Relay，"
                "再到「设置 → 连接」填写 Relay 地址，才能生成配对码。"
            )
            self._idle_msg.set_message(
                "未配置 Relay：暂时无法生成配对码。", "warning"
            )
            self._generate_button.setText("生成配对码")
            return
        self._idle_title.setText("还没有配对手机")
        self._idle_text.setText(
            "生成配对码后，用手机 LinkNote 扫码或输入配对码即可完成配对。"
        )
        self._generate_button.setText("生成配对码")
        self._generate_button.setObjectName("Primary")
        _repolish(self._generate_button)

    def _render_buttons(self) -> None:
        configured = self._relay_configured()
        self._generate_button.setEnabled(not self._inflight and configured)
        self._regenerate_button.setEnabled(not self._inflight and configured)
        self._add_device_button.setEnabled(not self._inflight and configured)
        self._copy_button.setEnabled(not self._inflight)
        self._cancel_button.setEnabled(not self._inflight)
        self._unpair_button.setVisible(self._paired())
        self._add_device_button.setVisible(self._paired())
        tip = "" if configured else "请先在设置页填写你自己的 Relay 地址"
        for widget in (
            self._generate_button,
            self._regenerate_button,
            self._add_device_button,
        ):
            widget.setToolTip(tip)

    def _render_devices(self, paired: bool) -> None:
        relay = (self._status or {}).get("relay") or {}
        devices = relay.get("devices") or []
        if not isinstance(devices, list):
            devices = []
        clean = [device for device in devices if isinstance(device, dict)]
        signature = tuple(
            (
                str(device.get("id")),
                str(device.get("name")),
                str(device.get("platform")),
                str(device.get("paired_at")),
                str(device.get("last_seen_at")),
            )
            for device in clean
        )
        self._devices = clean
        self._devices_count.set_state("ok" if clean else "neutral", f"{len(clean)} 台")
        if signature != self._devices_signature:
            self._devices_signature = signature
            clear_layout(self._devices_layout)
            for device in clean:
                row = DeviceRow(device, revoke_text="解除")
                row.revokeRequested.connect(self._revoke_device)
                self._devices_layout.addWidget(row)
        if clean:
            self._devices_empty.hide()
            self._devices_hint.show()
        else:
            self._devices_empty.show()
            self._devices_hint.hide()
            if paired:
                relay_error = str(relay.get("error") or "")
                self._devices_empty.setText(
                    "本机已配对，但暂时读不到手机列表"
                    + (f"（{utils.clip_text(relay_error, 60)}）" if relay_error else "（Relay 离线）")
                    + "。恢复网络后会自动刷新。"
                )
            else:
                self._devices_empty.setText(
                    "还没有配对设备。生成配对码并在手机上完成配对后，会在这里显示手机名称、平台与连接时间。"
                )

    # ================================================================ 动作
    def _start_pairing(self) -> None:
        if self._inflight:
            return
        if not self._relay_configured():
            self._msg.set_message(
                "还没有配置 Relay：请先在设置页填写你自己的 Relay 地址（见 docs/self-hosting.md）。",
                "warning",
            )
            return
        self._inflight = True
        self._success_device = {}
        self._msg.set_message("正在向 Relay 申请配对码…", "info")
        self._render_buttons()
        try:
            self.controller.create_pairing_async()
        except Exception as exc:  # noqa: BLE001
            self.on_error(str(exc))

    def _cancel_pairing(self) -> None:
        try:
            self.controller.cancel_pairing()
        except Exception as exc:  # noqa: BLE001
            self.on_error(str(exc))
            return
        self._pairing = {}
        self._inflight = False
        self._msg.clear()
        self.message.emit("已取消当前配对码", "info")
        self._render()

    def _copy_pair_code(self) -> None:
        code = self._pair_code_label.text().strip()
        if not code:
            return
        if utils.copy_text(code):
            self.message.emit("配对码已复制", "success")
        else:
            self.message.emit("复制失败，请手动选择配对码", "error")

    def _revoke_device(self, device_id: str) -> None:
        if not device_id:
            return
        device = next(
            (item for item in self._devices if str(item.get("id")) == str(device_id)), {}
        )
        name = str(device.get("name") or "这台手机")
        answer = QMessageBox.question(
            self,
            "解除配对",
            f"解除「{name}」后，该手机将无法再向这台电脑发送任务。确定解除吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.revoke_device(device_id)
        except Exception as exc:  # noqa: BLE001
            self.on_error(str(exc))
            return
        self.message.emit(f"已解除「{name}」", "info")

    def _unpair(self) -> None:
        answer = QMessageBox.question(
            self,
            "解除本机配对",
            "解除本机配对后，这台电脑的配对身份会被清除，所有手机都需要重新配对。\n"
            "BiliNote 引擎、模型与转写设置不受影响。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.unpair()
        except Exception as exc:  # noqa: BLE001
            self.on_error(str(exc))
            return
        self._pairing = {}
        self._success_device = {}
        self._msg.set_message("已解除本机配对，请生成新的配对码。", "success")
        self.message.emit("已解除本机配对", "info")
        self._render()

    def _tick_countdown(self) -> None:
        if not self._code_box.isVisible():
            self._countdown.stop()
            return
        remaining, expired = utils.format_remaining((self._pairing or {}).get("expires_at"))
        self._pair_expiry_label.setText(remaining)
        self._countdown_label.setText(remaining)
        if expired:
            self._pairing["status"] = "expired"
            self._render()


def _repolish(widget: QWidget) -> None:
    widget_style = widget.style()
    if widget_style is not None:
        widget_style.unpolish(widget)
        widget_style.polish(widget)
    widget.update()

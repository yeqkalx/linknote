"""引擎与 ffmpeg 页：引擎路径/进程管理、ffmpeg 安装、代理与系统健康。"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...config import detect_engine_exe
from ...services.ffmpeg import find_ffmpeg
from .. import style, utils
from ..widgets import (
    Card,
    ElidedLabel,
    InlineMessage,
    StatusBadge,
    StatusDot,
    ToggleSwitch,
    button,
    clear_layout,
    field_label,
    hint_label,
    progress_bar,
    set_button_kind,
    switch_row,
)
from .base import BasePage


class EnginePage(BasePage):
    PAGE_KEY = "engine"
    PAGE_TITLE = "引擎与 ffmpeg"
    PAGE_SUBTITLE = "BiliNote 引擎负责下载、转写与生成笔记；ffmpeg 用于处理音视频。"
    NEEDS_ENGINE = False

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._status: dict = self.controller.last_status or {}
        self._ffmpeg_downloading = False
        self._health_loaded = False
        self._engine_was_running = False

        self._build_engine_card()
        self._build_ffmpeg_card()
        self._build_network_card()
        self._build_health_card()

        self._apply_status(self._status)

    def _build_engine_card(self) -> None:
        card = Card()
        self._engine_card = card
        card.add_header(
            "BiliNote 引擎",
            "本机引擎程序；启动后 AI 模型、语音转写等页面才可用。",
        )

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self._engine_dot = StatusDot(style.IDLE, 9)
        status_row.addWidget(self._engine_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._engine_state_label = QLabel("已停止")
        self._engine_state_label.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}"
        )
        status_row.addWidget(self._engine_state_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._engine_pid_label = QLabel("")
        self._engine_pid_label.setObjectName("Muted")
        status_row.addWidget(self._engine_pid_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch(1)
        self._engine_badge = StatusBadge("已停止", "neutral")
        status_row.addWidget(self._engine_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(status_row)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(8)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("选择 BiliNoteBackend.exe 的完整路径")
        self._path_edit.setText(self.controller.config.resolved_engine_exe() or "")
        path_row.addWidget(self._path_edit, 1)
        self._browse_engine_button = button("浏览", "", self._browse_engine)
        path_row.addWidget(self._browse_engine_button, 0)
        self._detect_engine_button = button("检测", "", self._detect_engine)
        path_row.addWidget(self._detect_engine_button, 0)
        self._save_path_button = button("保存路径", "", self._save_engine_path)
        path_row.addWidget(self._save_path_button, 0)
        card.body.addWidget(_field("引擎程序", _wrap(path_row)))

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._start_button = button("启动", "Primary", self._toggle_engine)
        self._stop_button = button("停止", "", self._stop_engine)
        self._restart_button = button("重启", "", self._restart_engine)
        action_row.addWidget(self._start_button, 0)
        action_row.addWidget(self._stop_button, 0)
        action_row.addWidget(self._restart_button, 0)
        action_row.addStretch(1)
        self._open_install_dir_button = button("打开安装目录", "", self._open_install_dir)
        action_row.addWidget(self._open_install_dir_button, 0)
        self._open_engine_log_button = button("查看引擎日志", "", lambda: self.goto("logs"))
        action_row.addWidget(self._open_engine_log_button, 0)
        card.body.addLayout(action_row)

        self._engine_msg = InlineMessage()
        card.body.addWidget(self._engine_msg)
        self.add(card)

    def _build_ffmpeg_card(self) -> None:
        card = Card()
        self._ffmpeg_card = card
        card.add_header(
            "ffmpeg",
            "下载与转写音视频的必需组件；选择包含 ffmpeg.exe 的 bin 目录即可。",
        )

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self._ffmpeg_dot = StatusDot(style.IDLE, 9)
        status_row.addWidget(self._ffmpeg_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._ffmpeg_state_label = QLabel("未检测")
        self._ffmpeg_state_label.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}"
        )
        status_row.addWidget(self._ffmpeg_state_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch(1)
        self._ffmpeg_badge = StatusBadge("未知", "neutral")
        status_row.addWidget(self._ffmpeg_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(status_row)

        self._ffmpeg_version_label = QLabel("版本：—")
        self._ffmpeg_version_label.setObjectName("Muted")
        self._ffmpeg_version_label.setWordWrap(True)
        card.body.addWidget(self._ffmpeg_version_label)

        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(8)
        self._ffmpeg_dir_edit = QLineEdit()
        self._ffmpeg_dir_edit.setReadOnly(True)
        self._ffmpeg_dir_edit.setPlaceholderText("未设置（自动查找）")
        dir_row.addWidget(self._ffmpeg_dir_edit, 1)
        self._browse_ffmpeg_button = button("选择 bin 目录", "", self._browse_ffmpeg)
        dir_row.addWidget(self._browse_ffmpeg_button, 0)
        self._detect_ffmpeg_button = button("自动检测", "", self._detect_ffmpeg)
        dir_row.addWidget(self._detect_ffmpeg_button, 0)
        card.body.addWidget(_field("ffmpeg 目录", _wrap(dir_row)))

        self._ffmpeg_progress = progress_bar()
        self._ffmpeg_progress.hide()
        card.body.addWidget(self._ffmpeg_progress)
        self._ffmpeg_progress_label = QLabel("")
        self._ffmpeg_progress_label.setObjectName("Help")
        self._ffmpeg_progress_label.hide()
        card.body.addWidget(self._ffmpeg_progress_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._download_ffmpeg_button = button("下载安装", "Primary", self._download_ffmpeg)
        action_row.addWidget(self._download_ffmpeg_button, 0)
        self._open_ffmpeg_dir_button = button("打开目录", "", self._open_ffmpeg_dir)
        action_row.addWidget(self._open_ffmpeg_dir_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._ffmpeg_msg = InlineMessage()
        card.body.addWidget(self._ffmpeg_msg)
        self.add(card)

    def _build_network_card(self) -> None:
        card = Card()
        self._network_card = card
        card.add_header(
            "网络与代理",
            "代理与镜像会同时用于引擎、Agent 和模型下载；修改后需重启引擎生效。",
        )

        proxy_row = QHBoxLayout()
        proxy_row.setContentsMargins(0, 0, 0, 0)
        proxy_row.setSpacing(10)
        self._proxy_switch = ToggleSwitch()
        self._proxy_switch.setChecked(bool(self.controller.config.proxy_enabled))
        self._proxy_switch.toggled.connect(self._on_proxy_toggled)
        proxy_row.addWidget(self._proxy_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self._proxy_edit = QLineEdit()
        self._proxy_edit.setText(str(self.controller.config.proxy_url or ""))
        self._proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        proxy_row.addWidget(self._proxy_edit, 1)
        card.body.addWidget(_field("启用代理", _wrap(proxy_row)))

        self._hf_endpoint_edit = QLineEdit()
        self._hf_endpoint_edit.setText(str(self.controller.config.hf_endpoint or ""))
        self._hf_endpoint_edit.setPlaceholderText("https://hf-mirror.com")
        card.body.addWidget(
            _field(
                "HuggingFace 镜像",
                self._hf_endpoint_edit,
                "留空使用官方地址；国内网络可填 https://hf-mirror.com（重启引擎生效）。",
            )
        )

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._save_network_button = button("保存网络设置", "Primary", self._save_network)
        action_row.addWidget(self._save_network_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._network_msg = InlineMessage()
        card.body.addWidget(self._network_msg)
        self.add(card)

    def _build_health_card(self) -> None:
        card = Card()
        self._health_card = card
        self._refresh_health_button = button("刷新", "", self._load_health)
        card.add_header(
            "系统健康",
            "来自 BiliNote 引擎 /sys_health 的实时检查结果。",
            trailing=self._refresh_health_button,
        )
        self._health_msg = InlineMessage()
        card.body.addWidget(self._health_msg)
        self._health_container = QWidget()
        self._health_layout = QVBoxLayout(self._health_container)
        self._health_layout.setContentsMargins(0, 0, 0, 0)
        self._health_layout.setSpacing(4)
        card.body.addWidget(self._health_container)
        self._health_empty = QLabel("引擎未运行，启动引擎后可查看系统健康。")
        self._health_empty.setObjectName("Help")
        self._health_empty.setWordWrap(True)
        card.body.addWidget(self._health_empty)
        self.add(card)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        self._status = self.controller.last_status or self._status
        self._apply_status(self._status)
        if not self._status:
            self.controller.refresh_status_async()
        if self.engine_running():
            self._load_health()
        else:
            self._render_health_unavailable()

    def set_engine_available(self, running: bool) -> None:
        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(running)
        self._restart_button.setEnabled(running)
        self._open_engine_log_button.setEnabled(True)
        if running and not self._engine_was_running:
            # 引擎刚就绪：预加载一次系统健康（隐藏页也加载，打开页面即有内容）
            self._load_health()
        elif not running:
            self._render_health_unavailable()
        self._engine_was_running = running

    def on_status(self, status: dict) -> None:
        super().on_status(status)
        self._status = status or {}
        self._apply_status(self._status)
        if not self.engine_running():
            self._render_health_unavailable()

    def on_ffmpeg_progress(self, done: int, total: int) -> None:
        self._ffmpeg_downloading = True
        self._ffmpeg_progress.show()
        self._ffmpeg_progress_label.show()
        if total and total > 0:
            percent = max(0, min(100, int(done * 100 / total)))
            self._ffmpeg_progress.setRange(0, 100)
            self._ffmpeg_progress.setValue(percent)
            self._ffmpeg_progress_label.setText(
                f"正在下载 ffmpeg · {utils.format_size_pair(done, total)}"
            )
        else:
            self._ffmpeg_progress.setRange(0, 0)
            self._ffmpeg_progress_label.setText(
                f"正在下载 ffmpeg · {utils.format_bytes(done)}"
            )
        self._ffmpeg_msg.set_message("正在下载 ffmpeg，完成后会自动安装。", "info")

    def on_notification(self, title: str, message: str, level: str) -> None:
        if "ffmpeg" not in f"{title} {message}".lower():
            return
        if level in ("error", "warning", "success"):
            self._ffmpeg_downloading = False
            self._ffmpeg_progress.hide()
            self._ffmpeg_progress_label.hide()
            self._download_ffmpeg_button.setEnabled(True)
            self._ffmpeg_msg.set_message(
                message, "success" if level == "success" else level
            )

    # ------------------------------------------------------------ 状态应用
    def _apply_status(self, status: dict) -> None:
        status = status or {}
        engine = status.get("engine") or {}
        ffmpeg = status.get("ffmpeg") or {}
        state = engine.get("state") or "stopped"
        text, _level, color = utils.engine_state_text(state, engine.get("message") or "")
        self._engine_state_label.setText(text)
        self._engine_dot.set_state(color, busy=state == "starting")
        badge = {
            "running": ("ok", "运行中"),
            "starting": ("warn", "启动中"),
            "error": ("error", "异常"),
        }.get(state, ("neutral", "已停止"))
        self._engine_badge.set_state(badge[0], badge[1])
        pid = engine.get("pid")
        owned = engine.get("owned")
        pid_text = f"PID {pid}" if pid else ""
        if pid and owned:
            pid_text += "（由 LinkNote 启动）"
        self._engine_pid_label.setText(pid_text)
        self._start_button.setEnabled(state not in ("running", "starting"))
        self._stop_button.setEnabled(state in ("running", "starting"))
        self._restart_button.setEnabled(state in ("running", "starting", "error"))

        current_path = self._path_edit.text().strip()
        detected_path = engine.get("path") or self.controller.config.resolved_engine_exe() or ""
        if not current_path and detected_path:
            self._path_edit.setText(detected_path)
        if not self._path_edit.hasFocus():
            self._path_edit.setCursorPosition(0)

        available = bool(ffmpeg.get("available"))
        if available:
            self._ffmpeg_dot.set_state(style.OK)
            self._ffmpeg_state_label.setText("已安装")
            self._ffmpeg_badge.set_state("ok", "可用")
            self._ffmpeg_version_label.setText(
                f"版本：{utils.clip_text(ffmpeg.get('version') or '未知', 120)}"
            )
            self._ffmpeg_dir_edit.setText(str(ffmpeg.get("bin_dir") or ""))
            self._ffmpeg_dir_edit.setCursorPosition(0)
            self._download_ffmpeg_button.setText("重新下载")
            set_button_kind(self._download_ffmpeg_button, "")
        else:
            self._ffmpeg_dot.set_state(style.DANGER)
            self._ffmpeg_state_label.setText("未安装")
            self._ffmpeg_badge.set_state("error", "缺失")
            self._ffmpeg_version_label.setText("版本：未检测到 ffmpeg")
            self._ffmpeg_dir_edit.setText(str(self.controller.config.ffmpeg_bin_dir or ""))
            self._ffmpeg_dir_edit.setCursorPosition(0)
            self._download_ffmpeg_button.setText("下载安装")
            set_button_kind(self._download_ffmpeg_button, "Primary")
        if available and self._ffmpeg_downloading:
            self._ffmpeg_downloading = False
            self._ffmpeg_progress.hide()
            self._ffmpeg_progress_label.hide()
            self._ffmpeg_msg.set_message("ffmpeg 已安装完成。", "success")
        if not available and not self._ffmpeg_downloading:
            self._download_ffmpeg_button.setEnabled(True)
            self._ffmpeg_progress.hide()
            self._ffmpeg_progress_label.hide()

    # ------------------------------------------------------------ 引擎动作
    def _browse_engine(self) -> None:
        start_dir = str(self.controller.config.resolved_engine_workdir() or "")
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择 BiliNoteBackend.exe",
            start_dir,
            "可执行文件 (*.exe);;所有文件 (*)",
        )
        if path:
            self._path_edit.setText(path)
            self._save_engine_path()

    def _detect_engine(self) -> None:
        self._engine_msg.set_message("正在自动检测引擎…", "info")
        self._detect_engine_button.setEnabled(False)

        def on_result(found) -> None:
            self._detect_engine_button.setEnabled(True)
            if found:
                self._path_edit.setText(str(found))
                self._save_engine_path()
                self._engine_msg.set_message(f"已找到引擎：{found}", "success")
            else:
                self._engine_msg.set_message(
                    "没有自动找到 BiliNoteBackend.exe，请手动选择引擎路径。", "warning"
                )

        def on_error(message: str) -> None:
            self._detect_engine_button.setEnabled(True)
            self._engine_msg.set_message(f"检测失败：{message}", "error")

        self.run_bg(detect_engine_exe, on_result=on_result, on_error=on_error)

    def _save_engine_path(self) -> None:
        path = self._path_edit.text().strip()
        if not path:
            self._engine_msg.set_message("请先选择 BiliNoteBackend.exe。", "warning")
            return
        from pathlib import Path

        target = Path(path)
        if not target.is_file():
            self._engine_msg.set_message("这个路径下没有找到文件，请重新选择。", "error")
            return
        self.controller.config.engine_exe = str(target)
        self.controller.config.engine_workdir = str(target.parent)
        self.controller.save_config()
        self._engine_msg.set_message("引擎路径已保存，重启引擎后生效。", "success")
        self.toast("引擎路径已保存", "success")

    def _toggle_engine(self) -> None:
        state = (self._status.get("engine") or {}).get("state") or "stopped"
        if state in ("running", "starting"):
            self._stop_engine()
        else:
            self._engine_msg.set_message("正在启动引擎，首次启动可能需要几十秒…", "info")
            self.controller.start_engine_async()

    def _stop_engine(self) -> None:
        self._engine_msg.set_message("正在停止引擎…", "info")
        self.controller.stop_engine_async()

    def _restart_engine(self) -> None:
        self._engine_msg.set_message("正在重启引擎…", "info")
        self.controller.restart_engine_async()

    def _open_install_dir(self) -> None:
        workdir = self.controller.config.resolved_engine_workdir()
        if utils.open_path(workdir):
            return
        self.toast("还没有设置引擎安装目录", "warning")

    # ------------------------------------------------------------ ffmpeg 动作
    def _browse_ffmpeg(self) -> None:
        start_dir = str(self.controller.config.ffmpeg_bin_dir or "")
        directory = QFileDialog.getExistingDirectory(self, "选择包含 ffmpeg.exe 的 bin 目录", start_dir)
        if not directory:
            return
        from pathlib import Path

        if not (Path(directory) / "ffmpeg.exe").is_file():
            self._ffmpeg_msg.set_message(
                "这个目录下没有 ffmpeg.exe，请选择正确的 bin 目录。", "error"
            )
            return
        self.controller.config.ffmpeg_bin_dir = directory
        self.controller.save_config()
        self._ffmpeg_msg.set_message("ffmpeg 目录已保存。", "success")
        self.controller.refresh_status_async()

    def _detect_ffmpeg(self) -> None:
        self._ffmpeg_msg.set_message("正在自动检测 ffmpeg…", "info")
        self._detect_ffmpeg_button.setEnabled(False)

        def on_result(info) -> None:
            self._detect_ffmpeg_button.setEnabled(True)
            if info is not None and getattr(info, "available", False):
                self.controller.config.ffmpeg_bin_dir = str(info.bin_dir)
                self.controller.save_config()
                self._ffmpeg_msg.set_message(f"已找到 ffmpeg：{info.path}", "success")
                self.controller.refresh_status_async()
            else:
                self._ffmpeg_msg.set_message(
                    "没有自动找到 ffmpeg，请手动选择 bin 目录，或点击「下载安装」。", "warning"
                )

        def on_error(message: str) -> None:
            self._detect_ffmpeg_button.setEnabled(True)
            self._ffmpeg_msg.set_message(f"检测失败：{message}", "error")

        self.run_bg(find_ffmpeg, self.controller.config, on_result=on_result, on_error=on_error)

    def _download_ffmpeg(self) -> None:
        self._ffmpeg_downloading = True
        self._download_ffmpeg_button.setEnabled(False)
        self._ffmpeg_progress.setRange(0, 0)
        self._ffmpeg_progress.show()
        self._ffmpeg_progress_label.setText("正在准备下载 ffmpeg…")
        self._ffmpeg_progress_label.show()
        self._ffmpeg_msg.set_message("正在下载 ffmpeg，完成后会自动解压安装。", "info")
        self.controller.download_ffmpeg_async()

    def _open_ffmpeg_dir(self) -> None:
        info = (self._status.get("ffmpeg") or {})
        target = info.get("bin_dir") or self.controller.config.ffmpeg_bin_dir
        if target and utils.open_path(target):
            return
        self.toast("还没有设置 ffmpeg 目录", "warning")

    # ------------------------------------------------------------ 网络
    def _on_proxy_toggled(self, checked: bool) -> None:
        self._proxy_edit.setEnabled(checked)

    def _save_network(self) -> None:
        self.controller.config.proxy_enabled = self._proxy_switch.isChecked()
        self.controller.config.proxy_url = self._proxy_edit.text().strip()
        self.controller.config.hf_endpoint = self._hf_endpoint_edit.text().strip()
        self.controller.save_config()
        self._network_msg.set_message("网络设置已保存。", "success")
        if self.engine_running():
            self._save_network_button.setEnabled(False)

            def on_result(_data) -> None:
                self._save_network_button.setEnabled(True)
                self._network_msg.set_message(
                    "已保存并同步到引擎；代理和镜像对新任务生效。", "success"
                )

            def on_error(message: str) -> None:
                self._save_network_button.setEnabled(True)
                self._network_msg.set_message(
                    f"已保存到桌面端，但同步引擎失败：{message}（重启引擎后生效）", "warning"
                )

            self.run_api(
                "update_proxy_config",
                self._proxy_switch.isChecked(),
                self.controller.config.proxy_url,
                on_result=on_result,
                on_error=on_error,
            )
        else:
            self._network_msg.set_message("网络设置已保存，启动引擎后生效。", "success")
        self.toast("网络设置已保存", "success")

    # ------------------------------------------------------------ 健康
    def _load_health(self) -> None:
        if not self.engine_running():
            self._render_health_unavailable()
            return
        self._refresh_health_button.setEnabled(False)

        def on_result(data) -> None:
            self._refresh_health_button.setEnabled(True)
            self._health_loaded = True
            self._health_empty.hide()
            self._health_msg.clear()
            clear_layout(self._health_layout)
            data = data if isinstance(data, dict) else {}
            whisper = data.get("whisper_model") if isinstance(data.get("whisper_model"), dict) else {}
            rows = [
                ("引擎服务", "正常" if data.get("backend") == "ok" else "异常",
                 "ok" if data.get("backend") == "ok" else "error"),
                ("ffmpeg", "可用" if data.get("ffmpeg") == "ok" else "缺失",
                 "ok" if data.get("ffmpeg") == "ok" else "error"),
                ("数据库", "正常" if data.get("db") == "ok" else "异常",
                 "ok" if data.get("db") == "ok" else "error"),
            ]
            if whisper:
                engine_type = whisper.get("type") or "—"
                size = whisper.get("size") or "—"
                if whisper.get("checked"):
                    ready = "已下载" if whisper.get("downloaded") else "未下载"
                    level = "ok" if whisper.get("downloaded") else "warn"
                else:
                    ready = "在线引擎无需本地模型"
                    level = "info"
                rows.append(("Whisper 模型", f"{engine_type} / {size} · {ready}", level))
            for key, value, level in rows:
                self._health_layout.addWidget(_health_row(key, value, level))

        def on_error(message: str) -> None:
            self._refresh_health_button.setEnabled(True)
            self._health_msg.set_message(f"读取系统健康失败：{message}", "error")

        self.run_api("sys_health", on_result=on_result, on_error=on_error)

    def _render_health_unavailable(self) -> None:
        clear_layout(self._health_layout)
        self._health_empty.show()
        self._health_msg.clear()


def _wrap(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget


def _field(title: str, field: QWidget, hint: str = "") -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(field_label(title))
    layout.addWidget(field)
    if hint:
        layout.addWidget(hint_label(hint))
    return container


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

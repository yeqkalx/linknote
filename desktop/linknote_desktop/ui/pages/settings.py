"""设置页：连接、启动方式、生成参数、本机路径与关于信息。"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...config import APP_VERSION, DEFAULT_RELAY_URL
from .. import style, utils
from ..widgets import (
    Card,
    InlineMessage,
    ToggleSwitch,
    button,
    field_label,
    hint_label,
    switch_row,
)
from .base import BasePage

QUALITY_OPTIONS = [
    ("快速（优先速度）", "fast"),
    ("均衡", "medium"),
    ("高质量（较慢）", "slow"),
]
STYLE_OPTIONS = [
    ("精简", "minimal"),
    ("详细", "detailed"),
    ("学术", "academic"),
    ("教程", "tutorial"),
    ("小红书", "xiaohongshu"),
    ("生活向", "life_journal"),
    ("任务导向", "task_oriented"),
    ("商业风格", "business"),
    ("会议纪要", "meeting_minutes"),
]


class SettingsPage(BasePage):
    PAGE_KEY = "settings"
    PAGE_TITLE = "设置"
    PAGE_SUBTITLE = "连接方式、启动行为与手机端的默认生成参数。"

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._build_connection_card()
        self._build_advanced_card()
        self._build_archive_card()
        self._build_startup_card()
        self._build_generation_card()
        self._build_paths_card()
        self._build_about_card()

    def _build_advanced_card(self) -> None:
        card = Card()
        card.add_header(
            "高级功能",
            "日常使用一般不需要进来；需要细调时再打开，页面右上角可随时返回设置。",
        )
        for key, title, description in (
            ("ai", "AI 模型", "供应商、API Key、手机默认模型（保存后自动拉取可用模型）"),
            ("transcription", "语音转写", "Whisper 模型下载与转写引擎选择"),
            ("engine", "引擎与诊断", "BiliNote 引擎路径、ffmpeg、代理与系统健康"),
            ("logs", "运行日志", "Agent 与引擎日志，排查问题时查看"),
        ):
            card.body.addWidget(self._entry_row(title, description, key))
        self.add(card)

    def _entry_row(self, title: str, description: str, page_key: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(12)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}"
        )
        column.addWidget(title_label)
        description_label = QLabel(description)
        description_label.setStyleSheet(
            f"QLabel {{ color: {style.MUTED}; font-size: 11.5px; }}"
        )
        description_label.setWordWrap(True)
        column.addWidget(description_label)
        layout.addLayout(column, 1)
        open_button = button("打开", "", lambda key=page_key: self.goto(key))
        open_button.setFixedHeight(30)
        open_button.setMinimumWidth(64)
        layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return container

    def _build_archive_card(self) -> None:
        card = Card()
        card.add_header(
            "笔记自动归档",
            "把本机 notes/ 里的 Markdown 复制到 Obsidian 仓库或任意本地目录，"
            "并补上 title / source_url / platform / created_at / model / app_version frontmatter。"
            "已存在的同名文件不会重复导出。",
        )
        config = self.controller.config
        self._archive_switch = ToggleSwitch()
        self._archive_switch.setChecked(bool(getattr(config, "archive_enabled", False)))
        self._archive_switch.toggled.connect(self._toggle_archive)
        card.body.addWidget(
            switch_row(
                "自动归档新笔记",
                "手机任务完成后自动导出（需要先选择可用的归档目录）。",
                self._archive_switch,
            )
        )

        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(8)
        self._archive_dir_edit = QLineEdit()
        self._archive_dir_edit.setText(str(getattr(config, "archive_dir", "") or ""))
        self._archive_dir_edit.setPlaceholderText("选择 Obsidian 仓库或任意文件夹")
        self._archive_dir_edit.editingFinished.connect(self._save_archive_dir)
        dir_row.addWidget(self._archive_dir_edit, 1)
        self._archive_browse_button = button("选择目录", "", self._choose_archive_dir)
        dir_row.addWidget(self._archive_browse_button, 0)
        self._archive_open_button = button("打开目录", "", self._open_archive_dir)
        dir_row.addWidget(self._archive_open_button, 0)
        card.body.addWidget(_field("归档目录", _wrap_row(dir_row)))

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._archive_export_button = button("立即导出", "Primary", self._export_now)
        action_row.addWidget(self._archive_export_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)
        self._archive_msg = InlineMessage()
        card.body.addWidget(self._archive_msg)
        card.body.addWidget(
            hint_label(
                "归档只读取本机 notes/ 目录，不会上传到 Relay；默认文件名沿用 notes 里的任务文件名。"
            )
        )
        self.add(card)

    def _build_connection_card(self) -> None:
        card = Card()
        card.add_header(
            "连接",
            "Relay 负责在手机和这台电脑之间中转配对与任务，并保存最终生成的 Markdown 笔记以便手机读取；"
            "视频、音频与 API Key 不会上传。",
        )
        self._relay_edit = QLineEdit()
        self._relay_edit.setText(self.controller.config.relay_base_url or DEFAULT_RELAY_URL)
        self._relay_edit.setPlaceholderText(DEFAULT_RELAY_URL)
        card.body.addWidget(
            _field(
                "Relay 地址",
                self._relay_edit,
                "一般无需修改；自建 Relay 时填写自己的地址（https 开头）。",
            )
        )
        self._agent_name_edit = QLineEdit()
        self._agent_name_edit.setText(self.controller.config.agent_name or "我的电脑")
        self._agent_name_edit.setPlaceholderText("我的电脑")
        card.body.addWidget(
            _field("电脑名称", self._agent_name_edit, "手机会显示这个名字，方便区分多台电脑。")
        )

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._save_connection_button = button("保存连接设置", "Primary", self._save_connection)
        action_row.addWidget(self._save_connection_button, 0)
        self._repair_button = button("重新配对手机", "Danger", self._repair_pairing)
        action_row.addWidget(self._repair_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)
        self._connection_msg = InlineMessage()
        card.body.addWidget(self._connection_msg)
        self.add(card)

    def _build_startup_card(self) -> None:
        card = Card()
        card.add_header("启动与托盘", "关闭窗口后 LinkNote 仍可在后台接收手机任务。")

        config = self.controller.config
        self._autostart_switch = ToggleSwitch()
        self._autostart_switch.setChecked(bool(getattr(config, "autostart", False)))
        self._autostart_switch.toggled.connect(self._toggle_autostart)
        card.body.addWidget(
            switch_row("开机自动启动", "登录 Windows 后在后台启动 LinkNote。", self._autostart_switch)
        )

        self._close_to_tray_switch = ToggleSwitch()
        self._close_to_tray_switch.setChecked(bool(config.close_to_tray))
        self._close_to_tray_switch.toggled.connect(self._toggle_close_to_tray)
        card.body.addWidget(
            switch_row(
                "关闭窗口时最小化到托盘",
                "推荐开启；关闭后会继续在托盘运行并接收任务。",
                self._close_to_tray_switch,
            )
        )

        self._start_minimized_switch = ToggleSwitch()
        self._start_minimized_switch.setChecked(bool(config.start_minimized))
        self._start_minimized_switch.toggled.connect(self._toggle_start_minimized)
        card.body.addWidget(
            switch_row(
                "启动时最小化到托盘",
                "开机自启时更安静，不弹出主窗口。",
                self._start_minimized_switch,
            )
        )
        self._startup_msg = InlineMessage()
        card.body.addWidget(self._startup_msg)
        self.add(card)

    def _build_generation_card(self) -> None:
        card = Card()
        card.add_header(
            "手机端默认生成参数",
            "手机提交任务时如果没有单独选择，会使用这里的参数。",
        )

        quality_row = QHBoxLayout()
        quality_row.setContentsMargins(0, 0, 0, 0)
        quality_row.setSpacing(12)
        quality_row.addWidget(field_label("下载画质"), 0)
        self._quality_combo = QComboBox()
        for label, value in QUALITY_OPTIONS:
            self._quality_combo.addItem(label, value)
        self._quality_combo.setMinimumWidth(200)
        quality_row.addWidget(self._quality_combo, 1)
        card.body.addLayout(quality_row)
        _select_combo(self._quality_combo, str(self.controller.config.quality or "fast"))

        style_row = QHBoxLayout()
        style_row.setContentsMargins(0, 0, 0, 0)
        style_row.setSpacing(12)
        style_row.addWidget(field_label("笔记风格"), 0)
        self._style_combo = QComboBox()
        for label, value in STYLE_OPTIONS:
            self._style_combo.addItem(label, value)
        self._style_combo.setMinimumWidth(200)
        style_row.addWidget(self._style_combo, 1)
        card.body.addLayout(style_row)
        _select_combo(self._style_combo, str(self.controller.config.style or "minimal"))

        self._screenshot_switch = ToggleSwitch()
        self._screenshot_switch.setChecked(bool(self.controller.config.screenshot))
        card.body.addWidget(
            switch_row("插入视频截图", "笔记中插入关键画面截图（需要 ffmpeg）。", self._screenshot_switch)
        )
        self._links_switch = ToggleSwitch()
        self._links_switch.setChecked(bool(self.controller.config.insert_video_links))
        card.body.addWidget(
            switch_row("插入原片链接", "在笔记里保留可跳转到原视频的时间点链接。", self._links_switch)
        )

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._save_generation_button = button("保存生成参数", "Primary", self._save_generation)
        action_row.addWidget(self._save_generation_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)
        self._generation_msg = InlineMessage()
        card.body.addWidget(self._generation_msg)
        self.add(card)

    def _build_paths_card(self) -> None:
        card = Card()
        card.add_header(
            "本机路径",
            "所有数据都保存在这台电脑上；卸载前如需备份，请先复制这些目录。",
        )
        self._paths = self.controller.data_paths()
        self._path_rows: list[tuple[str, str, QWidget]] = []
        for key, label, description in (
            ("data", "数据目录", "设置、配对信息与笔记输出"),
            ("logs", "日志目录", "Agent 与引擎运行日志"),
            ("agent_config", "Agent 配置", "包含本机配对令牌，请勿分享"),
        ):
            path = str(self._paths.get(key) or "")
            card.body.addWidget(self._path_row(label, description, path))
        card.body.addWidget(hint_label("高级的引擎路径与 ffmpeg 目录在「引擎与诊断」页维护。"))
        self.add(card)

    def _path_row(self, title: str, description: str, path: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"QLabel {{ color: {style.INK}; font-size: 12px; font-weight: bold; }}")
        column.addWidget(title_label)
        description_label = QLabel(description)
        description_label.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11px; }}")
        column.addWidget(description_label)
        column.addWidget(utils_elided(path))
        layout.addLayout(column, 1)
        open_button = button("打开", "", on_click=lambda p=path: self._open_path(p))
        open_button.setFixedHeight(30)
        layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._path_rows.append((title, path, open_button))
        return container

    def _build_about_card(self) -> None:
        card = Card()
        card.add_header("关于 LinkNote")
        info = QLabel(
            f"LinkNote 链记 · 桌面端 v{APP_VERSION}\n"
            "手机发链接，电脑做笔记。视频、API Key 与生成的笔记都保存在本机。"
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        card.body.addWidget(info)
        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 2, 0, 0)
        links_row.setSpacing(8)
        links_row.addWidget(button("打开数据目录", "", lambda: self._open_path(str(self._paths.get("data") or ""))))
        links_row.addWidget(button("打开日志目录", "", lambda: self._open_path(str(self._paths.get("logs") or ""))))
        links_row.addWidget(button("重新运行配置向导", "", self._open_onboarding))
        links_row.addStretch(1)
        card.body.addLayout(links_row)
        self.add(card)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        try:
            enabled = bool(self.controller.autostart_enabled())
        except Exception:  # noqa: BLE001 - 注册表异常不应影响设置页
            enabled = bool(getattr(self.controller.config, "autostart", False))
        previously = self._autostart_switch.blockSignals(True)
        self._autostart_switch.setChecked(enabled)
        self._autostart_switch.blockSignals(previously)
        self._autostart_switch._offset = 1.0 if enabled else 0.0
        self._autostart_switch.update()
        self._refresh_archive_status()

    # ------------------------------------------------------------ 归档
    def _toggle_archive(self, checked: bool) -> None:
        self.controller.config.archive_enabled = bool(checked)
        self.controller.save_config()
        if checked and not str(self.controller.config.archive_dir or "").strip():
            self._archive_msg.set_message("已开启自动归档，但还没有选择归档目录。", "warning")
        else:
            self._archive_msg.set_message(
                "已开启自动归档。" if checked else "已关闭自动归档。",
                "success" if checked else "info",
            )
        self._refresh_archive_status()

    def _save_archive_dir(self) -> None:
        directory = self._archive_dir_edit.text().strip()
        if directory == str(self.controller.config.archive_dir or "").strip():
            return
        self.controller.config.archive_dir = directory
        self.controller.save_config()
        self._refresh_archive_status()

    def _choose_archive_dir(self) -> None:
        start_dir = str(self.controller.config.archive_dir or "")
        directory = QFileDialog.getExistingDirectory(
            self, "选择笔记归档目录（Obsidian 仓库或任意文件夹）", start_dir
        )
        if not directory:
            return
        self.controller.config.archive_dir = directory
        self.controller.save_config()
        self._archive_dir_edit.setText(directory)
        self._refresh_archive_status()
        self.toast("归档目录已保存", "success")

    def _open_archive_dir(self) -> None:
        directory = str(self.controller.config.archive_dir or "").strip()
        if not directory or not utils.open_path(directory):
            self._archive_msg.set_message("归档目录不存在或无法打开，请重新选择。", "warning")
            return

    def _refresh_archive_status(self) -> None:
        current_dir = str(self.controller.config.archive_dir or "")
        if not self._archive_dir_edit.hasFocus() and self._archive_dir_edit.text() != current_dir:
            self._archive_dir_edit.setText(current_dir)
        try:
            status = self.controller.archive_status()
        except Exception as exc:  # noqa: BLE001
            self._archive_msg.set_message(f"读取归档状态失败：{exc}", "error")
            return
        error = str(status.get("error") or "")
        if error:
            self._archive_msg.set_message(f"归档目录不可用：{error}", "warning")
        elif status.get("enabled"):
            self._archive_msg.set_message(
                f"自动归档已开启，目录：{status.get('dir')}", "success"
            )
        else:
            self._archive_msg.set_message(
                "自动归档未开启；仍可用「立即导出」手动导出一次。", "info"
            )

    def _export_now(self) -> None:
        if not str(self.controller.config.archive_dir or "").strip():
            self._archive_msg.set_message("请先选择归档目录。", "warning")
            return
        self._archive_export_button.setEnabled(False)
        self._archive_msg.set_message("正在导出本地笔记…", "info")

        def on_result(result) -> None:
            self._archive_export_button.setEnabled(True)
            data = result if isinstance(result, dict) else {}
            error = str(data.get("error") or "")
            if error:
                self._archive_msg.set_message(f"导出失败：{error}", "error")
                return
            exported = int(data.get("exported") or 0)
            skipped = int(data.get("skipped") or 0)
            failed = int(data.get("failed") or 0)
            errors = data.get("errors") or []
            if not exported and not skipped and not failed:
                self._archive_msg.set_message(
                    "notes 目录里还没有可归档的笔记（正常情况下笔记会回传手机）。",
                    "info",
                )
                return
            level = "warning" if failed else "success"
            detail = ""
            if failed:
                detail = "；失败：" + "；".join(str(item) for item in errors[:3])
            self._archive_msg.set_message(
                f"已导出 {exported} 篇，跳过 {skipped} 篇（已存在）"
                + (f"，失败 {failed} 篇{detail}" if failed else "。"),
                level,
            )
            if exported:
                self.toast(f"已归档 {exported} 篇笔记", "success")

        def on_error(message: str) -> None:
            self._archive_export_button.setEnabled(True)
            self._archive_msg.set_message(f"导出失败：{message}", "error")

        self.run_bg(self.controller.archive_notes, on_result=on_result, on_error=on_error)

    # ------------------------------------------------------------ 动作
    def _save_connection(self) -> None:
        relay_url = self._relay_edit.text().strip().rstrip("/")
        agent_name = self._agent_name_edit.text().strip() or "我的电脑"
        if not relay_url.startswith(("http://", "https://")):
            self._connection_msg.set_message("Relay 地址需要以 http:// 或 https:// 开头。", "error")
            return
        self.controller.config.relay_base_url = relay_url
        self.controller.config.agent_name = agent_name
        self.controller.save_config()
        if self.controller.agent.running:
            self.controller.restart_agent()
            self._connection_msg.set_message("已保存，Agent 已按新设置重启。", "success")
        else:
            self._connection_msg.set_message("已保存。Agent 正在运行时生效。", "success")
        self.toast("连接设置已保存", "success")

    def _repair_pairing(self) -> None:
        answer = QMessageBox.question(
            self,
            "重新配对手机",
            "将取消当前待领取的配对码，并回到状态页重新生成配对码。\n"
            "已经配对的手机不受影响。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.controller.cancel_pairing()
        self.toast("请到「设备」页重新生成配对码", "info")
        self.goto("devices")

    def _toggle_autostart(self, checked: bool) -> None:
        try:
            self.controller.set_autostart(bool(checked))
            actual = bool(self.controller.autostart_enabled())
            self.controller.config.autostart = actual
        except Exception as exc:  # noqa: BLE001
            self._startup_msg.set_message(f"设置开机自启失败：{exc}", "error")
            return
        self._startup_msg.set_message(
            "开机自启已开启。" if checked else "开机自启已关闭。",
            "success" if checked else "info",
        )

    def _toggle_close_to_tray(self, checked: bool) -> None:
        self.controller.config.close_to_tray = bool(checked)
        self.controller.save_config()
        self._startup_msg.set_message(
            "关闭窗口后将最小化到托盘。" if checked else "关闭窗口后将退出 LinkNote。",
            "info",
        )

    def _toggle_start_minimized(self, checked: bool) -> None:
        self.controller.config.start_minimized = bool(checked)
        self.controller.save_config()
        self._startup_msg.set_message("启动行为已保存。", "success")

    def _save_generation(self) -> None:
        self.controller.config.quality = str(self._quality_combo.currentData() or "fast")
        self.controller.config.style = str(self._style_combo.currentData() or "minimal")
        self.controller.config.screenshot = self._screenshot_switch.isChecked()
        self.controller.config.insert_video_links = self._links_switch.isChecked()
        self.controller.save_config()
        self._generation_msg.set_message("生成参数已保存，手机新任务会使用这些设置。", "success")
        self.toast("生成参数已保存", "success")

    def _open_path(self, path: str) -> None:
        if not path or not utils.open_path(path):
            self.toast("路径不存在或无法打开", "warning")
            return

    def _open_onboarding(self) -> None:
        window = self.window()
        opener = getattr(window, "open_onboarding", None)
        if callable(opener):
            opener()
        else:
            self.toast("无法打开配置向导", "warning")

    def refresh_paths(self) -> None:
        self._paths = self.controller.data_paths()


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


def _wrap_row(layout: QHBoxLayout) -> QWidget:
    container = QWidget()
    container.setLayout(layout)
    return container


def _select_combo(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def utils_elided(text: str) -> QWidget:
    from ..widgets import ElidedLabel

    label = ElidedLabel(text or "", color=style.MUTED)
    label.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11px; }}")
    return label

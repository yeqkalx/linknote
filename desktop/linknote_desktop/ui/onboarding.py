"""首次配置向导（Onboarding）：让普通用户不碰命令行也能把客户端配好。

步骤：欢迎 → ffmpeg → 启动引擎 → AI 模型 → 语音转写 → 配对手机 → 完成。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import style, utils
from .pairing_panel import PairingPanel
from .widgets import (
    Card,
    InlineMessage,
    StatusBadge,
    StatusDot,
    button,
    field_label,
    hint_label,
    progress_bar,
)

STEP_TITLES = [
    "欢迎使用",
    "安装 ffmpeg",
    "启动引擎",
    "AI 模型",
    "语音转写",
    "配对手机",
    "完成",
]

TRANSCRIBER_OPTIONS = [
    ("Faster Whisper（本地，免费）", "fast-whisper"),
    ("Groq（在线，速度快）", "groq"),
]
SIZE_OPTIONS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]


class OnboardingWizard(QDialog):
    """首次配置向导，窗口 760×560，暖纸 / 白卡片 / 珊瑚主按钮。"""

    WELCOME = 0
    FFMPEG = 1
    ENGINE = 2
    AI = 3
    TRANSCRIBE = 4
    PAIR = 5
    FINISH = 6

    def __init__(self, controller, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("Onboarding")
        self.setWindowTitle("linknote 配置向导")
        self.setWindowIcon(utils.app_icon())
        self.setModal(True)
        self.resize(760, 560)
        self.setMinimumSize(720, 520)

        self._step = 0
        self._finished = False
        self.completed_fully = False
        self._status: dict = controller.last_status or {}
        self._ffmpeg_ok = False
        self._ffmpeg_checking = False
        self._ffmpeg_downloading = False
        self._engine_wait_ticks = 0
        self._engine_start_requested = False
        self._providers: list[dict[str, Any]] = []
        self._ob_models: list[dict[str, Any]] = []
        self._ai_saved = False
        self._trans_saved = False
        self._trans_status: dict[str, Any] = {}
        self._pairing: dict[str, Any] = dict(controller.pairing_state() or {})

        self._build_shell()
        self._build_pages()
        self._connect_controller()
        self._go(self.WELCOME)
        self._check_ffmpeg()

    # ============================================================ 外壳
    def _build_shell(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("WizardHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(22, 16, 22, 12)
        header_row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(utils.app_pixmap(30))
        icon.setFixedSize(30, 30)
        header_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand = QLabel("linknote 配置向导")
        brand.setObjectName("WizardTitle")
        header_row.addWidget(brand, 1, Qt.AlignmentFlag.AlignVCenter)
        self._step_counter = QLabel("第 1 / 7 步")
        self._step_counter.setObjectName("Help")
        header_row.addWidget(self._step_counter, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("WizardRail")
        rail.setFixedWidth(206)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(16, 16, 14, 16)
        rail_layout.setSpacing(4)
        rail_caption = QLabel("配置步骤")
        rail_caption.setObjectName("Help")
        rail_layout.addWidget(rail_caption)
        rail_layout.addSpacing(6)
        self._rail_rows: list[tuple[QLabel, QLabel]] = []
        for index, title in enumerate(STEP_TITLES):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(9)
            circle = QLabel(str(index + 1))
            circle.setFixedSize(22, 22)
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text = QLabel(title)
            row_layout.addWidget(circle, 0, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(text, 1)
            rail_layout.addWidget(row)
            self._rail_rows.append((circle, text))
        rail_layout.addStretch(1)
        rail_note = QLabel("全程无需命令行，随时可以跳过并在主界面继续设置。")
        rail_note.setObjectName("Help")
        rail_note.setWordWrap(True)
        rail_layout.addWidget(rail_note)
        body.addWidget(rail)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(26, 18, 26, 8)
        content_layout.setSpacing(10)
        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack, 1)
        body.addWidget(content, 1)
        root.addLayout(body, 1)

        footer = QWidget()
        footer.setObjectName("WizardFooter")
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(22, 10, 22, 16)
        footer_row.setSpacing(10)
        self._back_button = button("上一步", "", self._go_back)
        self._back_button.setMinimumWidth(88)
        footer_row.addWidget(self._back_button, 0)
        footer_row.addStretch(1)
        self._later_button = button("稍后设置", "Link", self._skip_all)
        footer_row.addWidget(self._later_button, 0)
        self._skip_button = button("跳过此步", "", self._skip_step)
        self._skip_button.setMinimumWidth(88)
        footer_row.addWidget(self._skip_button, 0)
        self._next_button = button("下一步", "Primary", self._go_next)
        self._next_button.setMinimumWidth(120)
        footer_row.addWidget(self._next_button, 0)
        root.addWidget(footer)

    def _build_pages(self) -> None:
        self._pages = [
            self._build_welcome_page(),
            self._build_ffmpeg_page(),
            self._build_engine_page(),
            self._build_ai_page(),
            self._build_transcribe_page(),
            self._build_pair_page(),
            self._build_finish_page(),
        ]
        for page in self._pages:
            scroll = QScrollArea()
            scroll.setObjectName("WizardScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.viewport().setAutoFillBackground(False)
            scroll.setWidget(page)
            self._stack.addWidget(scroll)

    # ------------------------------------------------------------ 通用小件
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        return widget, layout

    def _page_title(self, title: str, subtitle: str) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("WizardStepTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("WizardStepSub")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)
        return block

    # ============================================================ 各步骤页面
    def _build_welcome_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "欢迎使用 linknote 链记",
                "手机发链接，电脑做笔记。接下来用 1~2 分钟完成首次配置。",
            )
        )

        features = Card()
        for title, text in (
            ("手机发链接", "在手机上把 Bilibili / YouTube 等视频链接发给电脑，任务会自动排队处理。"),
            ("电脑做笔记", "电脑下载视频、转写音频并调用大模型生成结构化 Markdown 笔记。"),
            ("自动回传手机", "生成的笔记会回传到手机，随时打开阅读或分享。"),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(9)
            dot = StatusDot(style.CORAL, 8)
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(1)
            title_label = QLabel(title)
            title_label.setStyleSheet(
                f"QLabel {{ color: {style.INK}; font-size: 12.5px; font-weight: bold; }}"
            )
            column.addWidget(title_label)
            text_label = QLabel(text)
            text_label.setStyleSheet(f"QLabel {{ color: {style.MUTED}; font-size: 11.5px; }}")
            text_label.setWordWrap(True)
            column.addWidget(text_label)
            row.addLayout(column, 1)
            features.body.addLayout(row)
        layout.addWidget(features)

        privacy = Card()
        privacy.add_header(
            "隐私与数据",
            "视频文件、音频、API Key 和转写内容只保存在这台电脑上。"
            "Relay 只中转链接、任务状态和最终 Markdown（Markdown 会保存在 Relay 数据库中）以便手机读取。",
        )
        layout.addWidget(privacy)
        layout.addStretch(1)
        return page

    def _build_ffmpeg_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "安装 ffmpeg",
                "处理音视频需要 ffmpeg，首次使用会自动下载安装（约 80MB，只需一次）。",
            )
        )
        card = Card()
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(9)
        self._ffmpeg_dot = StatusDot(style.IDLE, 9)
        status_row.addWidget(self._ffmpeg_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._ffmpeg_status_label = QLabel("正在检测…")
        self._ffmpeg_status_label.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 13px; font-weight: bold; }}"
        )
        status_row.addWidget(self._ffmpeg_status_label, 1)
        self._ffmpeg_badge = StatusBadge("检测中", "neutral")
        status_row.addWidget(self._ffmpeg_badge, 0)
        card.body.addLayout(status_row)

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
        self._ffmpeg_download_button = button("下载 ffmpeg", "Primary", self._download_ffmpeg)
        action_row.addWidget(self._ffmpeg_download_button, 0)
        self._ffmpeg_recheck_button = button("重新检测", "", self._check_ffmpeg)
        action_row.addWidget(self._ffmpeg_recheck_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._ffmpeg_msg = InlineMessage()
        card.body.addWidget(self._ffmpeg_msg)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_engine_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "启动 BiliNote 引擎",
                "引擎负责下载视频、转写与生成笔记，安装包已内置，直接启动即可。",
            )
        )
        card = Card()
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(9)
        self._engine_dot = StatusDot(style.IDLE, 9)
        status_row.addWidget(self._engine_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._engine_status_label = QLabel("尚未启动")
        self._engine_status_label.setStyleSheet(
            f"QLabel {{ color: {style.INK}; font-size: 13px; font-weight: bold; }}"
        )
        status_row.addWidget(self._engine_status_label, 1)
        self._engine_badge = StatusBadge("未启动", "neutral")
        status_row.addWidget(self._engine_badge, 0)
        card.body.addLayout(status_row)
        card.body.addWidget(
            hint_label("首次启动可能需要几十秒；如果失败，请点击「重试启动」。")
        )

        self._engine_msg = InlineMessage()
        card.body.addWidget(self._engine_msg)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._engine_retry_button = button("重试启动", "Primary", self._start_engine)
        action_row.addWidget(self._engine_retry_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_ai_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "配置 AI 模型",
                "生成笔记需要一个大模型。推荐 DeepSeek，API Key 只保存在本机 BiliNote 引擎。",
            )
        )
        card = Card()
        provider_row = QHBoxLayout()
        provider_row.setContentsMargins(0, 0, 0, 0)
        provider_row.setSpacing(10)
        provider_row.addWidget(field_label("模型供应商"), 0)
        self._ob_provider_combo = QComboBox()
        self._ob_provider_combo.setMinimumWidth(240)
        self._ob_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        provider_row.addWidget(self._ob_provider_combo, 1)
        card.body.addLayout(provider_row)

        self._ob_key_edit = QLineEdit()
        self._ob_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._ob_key_edit.setPlaceholderText("粘贴 API Key，例如 sk-…")
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(8)
        key_row.addWidget(self._ob_key_edit, 1)
        self._ob_key_toggle = QPushButton("显示")
        self._ob_key_toggle.setCheckable(True)
        self._ob_key_toggle.setFixedWidth(62)
        self._ob_key_toggle.clicked.connect(self._toggle_ob_key)
        key_row.addWidget(self._ob_key_toggle, 0)
        key_container = QWidget()
        key_container.setLayout(key_row)
        card.body.addWidget(_field("API Key", key_container))

        self._ob_base_edit = QLineEdit()
        self._ob_base_edit.setPlaceholderText("可选：OpenAI 兼容接口地址")
        card.body.addWidget(_field("Base URL", self._ob_base_edit))

        self._ob_ai_msg = InlineMessage()
        card.body.addWidget(self._ob_ai_msg)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._ob_test_button = button("保存并测试连接", "Primary", self._save_and_test_ai)
        action_row.addWidget(self._ob_test_button, 0)
        self._ob_ai_refresh_button = button("刷新供应商", "", self._load_ai_providers)
        action_row.addWidget(self._ob_ai_refresh_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        card.body.addWidget(
            hint_label("也可以稍后在「AI 模型」页配置；不配置模型将无法生成笔记。")
        )
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_transcribe_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "选择语音转写",
                "默认使用本地 Faster Whisper（免费、离线），首次需要下载 small 模型（约 460MB）；"
                "也可以稍后设置。",
            )
        )
        card = Card()
        engine_row = QHBoxLayout()
        engine_row.setContentsMargins(0, 0, 0, 0)
        engine_row.setSpacing(10)
        engine_row.addWidget(field_label("转写引擎"), 0)
        self._ob_trans_engine_combo = QComboBox()
        self._ob_trans_engine_combo.setMinimumWidth(240)
        for label, value in TRANSCRIBER_OPTIONS:
            self._ob_trans_engine_combo.addItem(label, value)
        self._ob_trans_engine_combo.currentIndexChanged.connect(self._on_trans_engine_changed)
        engine_row.addWidget(self._ob_trans_engine_combo, 1)
        card.body.addLayout(engine_row)

        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(10)
        size_row.addWidget(field_label("本地模型"), 0)
        self._ob_trans_size_combo = QComboBox()
        for size in SIZE_OPTIONS:
            self._ob_trans_size_combo.addItem(size, size)
        self._ob_trans_size_combo.setCurrentIndex(SIZE_OPTIONS.index("small"))
        self._ob_trans_size_combo.setMinimumWidth(240)
        size_row.addWidget(self._ob_trans_size_combo, 1)
        card.body.addLayout(size_row)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 2, 0, 0)
        status_row.setSpacing(9)
        self._ob_trans_dot = StatusDot(style.IDLE, 8)
        status_row.addWidget(self._ob_trans_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._ob_trans_status_label = QLabel("尚未检查模型状态")
        self._ob_trans_status_label.setObjectName("Body")
        status_row.addWidget(self._ob_trans_status_label, 1)
        self._ob_trans_badge = StatusBadge("待检查", "neutral")
        status_row.addWidget(self._ob_trans_badge, 0)
        card.body.addLayout(status_row)

        self._ob_trans_msg = InlineMessage()
        card.body.addWidget(self._ob_trans_msg)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._ob_trans_download_button = button("保存并下载模型", "Primary", self._download_trans_model)
        action_row.addWidget(self._ob_trans_download_button, 0)
        self._ob_trans_refresh_button = button("刷新状态", "", self._refresh_trans_status)
        action_row.addWidget(self._ob_trans_refresh_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        card.body.addWidget(
            hint_label("选择 Groq 在线引擎无需下载模型，但需要先在「AI 模型」页配置 Groq 的 Key。")
        )
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_pair_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "配对手机",
                "用手机 LinkNote 扫描二维码或输入配对码。配对成功后，已配对手机会一直显示在这里，"
                "可随时添加/更换手机或解除设备。",
            )
        )
        self._pairing_panel = PairingPanel(self.controller)
        self._ob_pair_msg = InlineMessage()
        self._pairing_panel.message.connect(
            lambda text, level: self._ob_pair_msg.set_message(text, level)
        )
        layout.addWidget(self._pairing_panel)
        layout.addWidget(self._ob_pair_msg)
        layout.addWidget(
            hint_label("也可以稍后配对：完成向导后，随时可以在「状态」页生成配对码。")
        )
        layout.addStretch(1)
        return page

    def _build_finish_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._page_title(
                "准备就绪",
                "以下是当前配置状态；随时可以在主界面里调整。",
            )
        )
        card = Card()
        self._ob_summary_container = QWidget()
        self._ob_summary_layout = QVBoxLayout(self._ob_summary_container)
        self._ob_summary_layout.setContentsMargins(0, 0, 0, 0)
        self._ob_summary_layout.setSpacing(6)
        card.body.addWidget(self._ob_summary_container)
        self._ob_finish_note = QLabel("")
        self._ob_finish_note.setObjectName("Help")
        self._ob_finish_note.setWordWrap(True)
        card.body.addWidget(self._ob_finish_note)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    # ============================================================ 导航
    def _go(self, step: int) -> None:
        step = max(0, min(step, len(self._pages) - 1))
        self._step = step
        self._stack.setCurrentIndex(step)
        self._refresh_rail()
        self._refresh_footer()
        self._on_enter_step(step)

    def _refresh_rail(self) -> None:
        for index, (circle, text) in enumerate(self._rail_rows):
            if index < self._step:
                circle.setStyleSheet(
                    "QLabel {"
                    f"background-color: {style.OK}; color: {style.WHITE};"
                    "border-radius: 11px; font-size: 11px; font-weight: bold;"
                    "}"
                )
                text.setStyleSheet(
                    f"QLabel {{ color: {style.MUTED}; font-size: 12px; }}"
                )
            elif index == self._step:
                circle.setStyleSheet(
                    "QLabel {"
                    f"background-color: {style.CORAL}; color: {style.ON_CORAL};"
                    "border-radius: 11px; font-size: 11px; font-weight: bold;"
                    "}"
                )
                text.setStyleSheet(
                    f"QLabel {{ color: {style.CORAL_INK}; font-size: 12px; font-weight: bold; }}"
                )
            else:
                circle.setStyleSheet(
                    "QLabel {"
                    f"background-color: #EAF0FA; color: {style.MUTED};"
                    "border-radius: 11px; font-size: 11px; font-weight: bold;"
                    "}"
                )
                text.setStyleSheet(
                    f"QLabel {{ color: {style.INK_SOFT}; font-size: 12px; }}"
                )
        self._step_counter.setText(f"第 {self._step + 1} / {len(self._pages)} 步")

    def _refresh_footer(self) -> None:
        self._back_button.setEnabled(self._step > 0)
        self._skip_button.setVisible(0 < self._step < len(self._pages) - 1)
        self._next_button.setText(
            "完成，开始使用" if self._step == len(self._pages) - 1 else "下一步"
        )
        self._later_button.setVisible(True)

    def _go_back(self) -> None:
        if self._step > 0:
            self._go(self._step - 1)

    def _go_next(self) -> None:
        step = self._step
        if step == self.FFMPEG and not self._ffmpeg_ok:
            self._ffmpeg_msg.set_message(
                "还没有安装 ffmpeg。请点击「下载 ffmpeg」，或点击「跳过此步」稍后处理。",
                "warning",
            )
            return
        if step == self.ENGINE and not self._engine_running():
            self._engine_msg.set_message(
                "引擎还没有启动成功。请点击「重试启动」，或点击「跳过此步」稍后处理。",
                "warning",
            )
            return
        if step == len(self._pages) - 1:
            self._finish()
            return
        self._go(step + 1)

    def _skip_step(self) -> None:
        if self._step >= len(self._pages) - 1:
            self._finish()
            return
        self._go(self._step + 1)

    def _skip_all(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self.controller.config.onboarding_done = True
            self.controller.save_config()
        except Exception:  # noqa: BLE001
            pass
        self._disconnect_controller()
        self.accept()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.completed_fully = True
        try:
            self.controller.complete_onboarding()
        except Exception:  # noqa: BLE001 - 向导成功与否都不阻塞关闭
            try:
                self.controller.config.onboarding_done = True
                self.controller.save_config()
            except Exception:  # noqa: BLE001
                pass
        self._disconnect_controller()
        self.accept()

    # ============================================================ 进入步骤
    def _on_enter_step(self, step: int) -> None:
        self._stop_engine_wait()
        self._stop_trans_poll()
        if step == self.FFMPEG:
            self._check_ffmpeg()
        elif step == self.ENGINE:
            self._enter_engine_step()
        elif step == self.AI:
            self._load_ai_providers()
        elif step == self.TRANSCRIBE:
            self._enter_transcribe_step()
        elif step == self.PAIR:
            self._enter_pair_step()
        elif step == self.FINISH:
            self._refresh_summary()

    # ------------------------------------------------------------ ffmpeg
    def _check_ffmpeg(self) -> None:
        if self._ffmpeg_checking:
            return
        self._ffmpeg_checking = True
        self._ffmpeg_status_label.setText("正在检测…")
        self._ffmpeg_badge.set_state("neutral", "检测中")
        self._ffmpeg_dot.set_state(style.IDLE)

        def on_result(ready) -> None:
            self._ffmpeg_checking = False
            self._apply_ffmpeg_ready(bool(ready))

        def on_error(message: str) -> None:
            self._ffmpeg_checking = False
            self._ffmpeg_msg.set_message(f"检测 ffmpeg 失败：{message}", "error")

        self.controller.run_async(self.controller.ffmpeg_ready, on_result=on_result, on_error=on_error)

    def _apply_ffmpeg_ready(self, ready: bool) -> None:
        self._ffmpeg_ok = ready
        if ready:
            self._ffmpeg_dot.set_state(style.OK)
            self._ffmpeg_status_label.setText("ffmpeg 已准备好")
            self._ffmpeg_badge.set_state("ok", "已安装")
            self._ffmpeg_progress.hide()
            self._ffmpeg_progress_label.hide()
            self._ffmpeg_download_button.setText("重新下载")
            self._ffmpeg_msg.set_message("ffmpeg 已安装完成，可以继续下一步。", "success")
        else:
            self._ffmpeg_dot.set_state(style.WARN)
            self._ffmpeg_status_label.setText("还没有安装 ffmpeg")
            self._ffmpeg_badge.set_state("warn", "未安装")
            self._ffmpeg_download_button.setText("下载 ffmpeg")
        self._ffmpeg_recheck_button.setEnabled(True)

    def _download_ffmpeg(self) -> None:
        if self._ffmpeg_downloading:
            return
        self._ffmpeg_downloading = True
        self._ffmpeg_download_button.setEnabled(False)
        self._ffmpeg_download_button.setText("下载中…")
        self._ffmpeg_progress.setRange(0, 0)
        self._ffmpeg_progress.show()
        self._ffmpeg_progress_label.setText("正在准备下载 ffmpeg…")
        self._ffmpeg_progress_label.show()
        self._ffmpeg_msg.set_message("正在下载 ffmpeg，请保持网络连接，完成后会自动安装。", "info")
        self.controller.download_ffmpeg_async()

    def _on_ffmpeg_progress(self, done: int, total: int) -> None:
        self._ffmpeg_downloading = True
        self._ffmpeg_progress.show()
        self._ffmpeg_progress_label.show()
        if total and total > 0:
            percent = max(0, min(100, int(done * 100 / total)))
            self._ffmpeg_progress.setRange(0, 100)
            self._ffmpeg_progress.setValue(percent)
            text = f"正在下载 ffmpeg · {utils.format_size_pair(done, total)}"
        else:
            self._ffmpeg_progress.setRange(0, 0)
            text = f"正在下载 ffmpeg · {utils.format_bytes(done)}"
        self._ffmpeg_progress_label.setText(text)

    # ------------------------------------------------------------ 引擎
    def _engine_running(self) -> bool:
        return ((self._status or {}).get("engine") or {}).get("state") == "running"

    def _enter_engine_step(self) -> None:
        self._apply_engine_state()
        if self._engine_running():
            return
        self._start_engine()

    def _start_engine(self) -> None:
        self._engine_start_requested = True
        self._engine_wait_ticks = 0
        self._engine_msg.set_message(
            "正在启动 BiliNote 引擎，首次启动可能需要几十秒…", "info"
        )
        self._engine_retry_button.setEnabled(False)
        try:
            self.controller.start_engine_async()
        except Exception as exc:  # noqa: BLE001
            self._engine_msg.set_message(f"启动引擎失败：{exc}", "error")
            self._engine_retry_button.setEnabled(True)
        self._start_engine_wait()

    def _start_engine_wait(self) -> None:
        if not hasattr(self, "_engine_wait_timer"):
            self._engine_wait_timer = QTimer(self)
            self._engine_wait_timer.setInterval(2000)
            self._engine_wait_timer.timeout.connect(self._on_engine_wait_tick)
        self._engine_wait_ticks = 0
        self._engine_wait_timer.start()

    def _stop_engine_wait(self) -> None:
        timer = getattr(self, "_engine_wait_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _on_engine_wait_tick(self) -> None:
        if self._engine_running() or not self._engine_start_requested:
            self._stop_engine_wait()
            return
        self._engine_wait_ticks += 1
        if self._engine_wait_ticks > 45:
            self._stop_engine_wait()
            self._engine_msg.set_message(
                "启动时间较长仍未就绪，可以点击「重试启动」，或稍后在「引擎与 ffmpeg」页处理。",
                "warning",
            )
            self._engine_retry_button.setEnabled(True)
            return
        try:
            self.controller.refresh_status_async()
        except Exception:  # noqa: BLE001
            pass

    def _apply_engine_state(self) -> None:
        engine = (self._status or {}).get("engine") or {}
        state = engine.get("state") or "stopped"
        text, _level, color = utils.engine_state_text(state, engine.get("message") or "")
        self._engine_status_label.setText(text)
        self._engine_dot.set_state(color, busy=state == "starting")
        badge = {
            "running": ("ok", "运行中"),
            "starting": ("warn", "启动中"),
            "error": ("error", "异常"),
        }.get(state, ("neutral", "未启动"))
        self._engine_badge.set_state(badge[0], badge[1])
        if state == "running":
            self._engine_msg.set_message("引擎已成功启动，可以继续下一步。", "success")
            self._engine_retry_button.setEnabled(True)
            self._stop_engine_wait()
            self._engine_start_requested = False
        elif state == "error":
            self._engine_msg.set_message(
                f"引擎启动失败：{utils.clip_text(engine.get('message') or '请稍后重试', 120)}",
                "error",
            )
            self._engine_retry_button.setEnabled(True)
            self._stop_engine_wait()
            self._engine_start_requested = False
        elif state == "starting":
            self._engine_msg.set_message("正在启动引擎，请稍候…", "info")

    # ------------------------------------------------------------ AI
    def _load_ai_providers(self) -> None:
        self._ob_ai_msg.set_message("正在读取引擎里的模型供应商…", "info")
        self._ob_ai_refresh_button.setEnabled(False)

        def on_result(data) -> None:
            self._ob_ai_refresh_button.setEnabled(True)
            self._providers = [item for item in (data or []) if isinstance(item, dict)]
            self._ob_provider_combo.blockSignals(True)
            self._ob_provider_combo.clear()
            for provider in self._providers:
                name = provider.get("name") or provider.get("id") or "未命名供应商"
                type_name = provider.get("type") or "custom"
                self._ob_provider_combo.addItem(f"{name}（{type_name}）", str(provider.get("id") or ""))
            self._ob_provider_combo.blockSignals(False)
            if not self._providers:
                self._ob_ai_msg.set_message(
                    "引擎里还没有供应商数据。可以跳过此步，稍后在「AI 模型」页配置。",
                    "warning",
                )
                return
            index = self._preferred_provider_index()
            self._ob_provider_combo.setCurrentIndex(index)
            self._on_ai_provider_changed(index)
            self._ob_ai_msg.clear()

        def on_error(message: str) -> None:
            self._ob_ai_refresh_button.setEnabled(True)
            self._ob_ai_msg.set_message(
                f"读取供应商失败：{message}（可以跳过此步，稍后在「AI 模型」页配置）", "error"
            )

        try:
            api = self.controller.engine_api()
        except Exception as exc:  # noqa: BLE001
            self._ob_ai_msg.set_message(f"无法访问引擎：{exc}", "error")
            return
        self.controller.run_async(api.providers, on_result=on_result, on_error=on_error)

    def _preferred_provider_index(self) -> int:
        config_id = str(getattr(self.controller.config, "provider_id", "") or "")
        target_id = ""
        for provider in self._providers:
            if str(provider.get("id") or "") == config_id:
                target_id = config_id
                break
        if not target_id:
            for provider in self._providers:
                name = str(provider.get("name") or "").lower()
                if "deepseek" in name:
                    target_id = str(provider.get("id") or "")
                    break
        if not target_id and self._providers:
            target_id = str(self._providers[0].get("id") or "")
        for index, provider in enumerate(self._providers):
            if str(provider.get("id") or "") == target_id:
                return index
        return 0

    def _current_provider(self) -> Optional[dict[str, Any]]:
        index = self._ob_provider_combo.currentIndex()
        if 0 <= index < len(self._providers):
            return self._providers[index]
        return None

    def _on_ai_provider_changed(self, _index: int) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        self._ob_key_edit.setText(str(provider.get("api_key") or ""))
        self._ob_base_edit.setText(str(provider.get("base_url") or ""))

    def _toggle_ob_key(self) -> None:
        visible = self._ob_key_toggle.isChecked()
        self._ob_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self._ob_key_toggle.setText("隐藏" if visible else "显示")

    def _save_and_test_ai(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._ob_ai_msg.set_message(
                "还没有可用的供应商。可以跳过此步，稍后在「AI 模型」页配置。", "warning"
            )
            return
        key = self._ob_key_edit.text().strip()
        if not key:
            self._ob_ai_msg.set_message("请先填写 API Key。", "warning")
            return
        provider_id = str(provider.get("id") or "")
        base_url = self._ob_base_edit.text().strip()
        self._ob_test_button.setEnabled(False)
        self._ob_ai_msg.set_message("正在保存并测试连接…", "info")
        fields: dict[str, Any] = {"api_key": key}
        if base_url:
            fields["base_url"] = base_url
        try:
            api = self.controller.engine_api()
        except Exception as exc:  # noqa: BLE001
            self._ob_ai_msg.set_message(f"无法访问引擎：{exc}", "error")
            self._ob_test_button.setEnabled(True)
            return

        def save_done(_data) -> None:
            provider["api_key"] = key
            if base_url:
                provider["base_url"] = base_url
            self.controller.config.provider_id = provider_id
            self._ai_saved = True
            self._ob_ai_msg.set_message("API Key 已保存，正在加载模型列表…", "info")
            self.controller.run_async(api.models, on_result=after_models, on_error=after_models_error)

        def save_error(message: str) -> None:
            self._ob_test_button.setEnabled(True)
            self._ob_ai_msg.set_message(f"保存 API Key 失败：{message}", "error")

        def after_models(data) -> None:
            models = [
                item
                for item in (data or [])
                if isinstance(item, dict) and str(item.get("provider_id")) == provider_id
            ]
            self._ob_models = models
            model_name = ""
            configured = str(getattr(self.controller.config, "model_name", "") or "")
            names = [str(item.get("model_name") or "") for item in models]
            if configured and configured in names:
                model_name = configured
            elif names:
                model_name = names[0]
            if model_name:
                self.controller.config.model_name = model_name
                self.controller.save_config()
            self._ob_ai_msg.set_message("模型已保存，正在测试连接…", "info")
            self.controller.run_async(
                api.connect_test,
                provider_id,
                model_name or None,
                on_result=test_done,
                on_error=test_error,
            )

        def after_models_error(message: str) -> None:
            self._ob_ai_msg.set_message(
                f"API Key 已保存，但读取模型列表失败：{message}。"
                "可稍后在「AI 模型」页添加模型并测试。",
                "warning",
            )
            self._ob_test_button.setEnabled(True)

        def test_done(_data) -> None:
            self._ob_test_button.setEnabled(True)
            model_name = str(getattr(self.controller.config, "model_name", "") or "")
            suffix = f"（{model_name}）" if model_name else ""
            self._ob_ai_msg.set_message(f"连接成功！模型已配置{suffix}，可以继续下一步。", "success")

        def test_error(message: str) -> None:
            self._ob_test_button.setEnabled(True)
            self._ob_ai_msg.set_message(
                f"API Key 已保存，但测试连接失败：{message}。"
                "请检查 Key / Base URL，或稍后在「AI 模型」页重试。",
                "warning",
            )

        self.controller.run_async(
            api.update_provider, provider_id, on_result=save_done, on_error=save_error, **fields
        )

    # ------------------------------------------------------------ 转写
    def _enter_transcribe_step(self) -> None:
        if not self._engine_running():
            self._ob_trans_msg.set_message(
                "引擎未运行，暂时无法读取转写状态；可以先跳过，稍后设置。", "warning"
            )
            return
        self._ob_trans_msg.set_message("正在读取转写配置…", "info")
        try:
            api = self.controller.engine_api()
        except Exception as exc:  # noqa: BLE001
            self._ob_trans_msg.set_message(f"无法访问引擎：{exc}", "warning")
            return

        def on_config(data) -> None:
            data = data if isinstance(data, dict) else {}
            engine_type = str(data.get("transcriber_type") or "fast-whisper")
            index = self._ob_trans_engine_combo.findData(engine_type)
            self._ob_trans_engine_combo.blockSignals(True)
            self._ob_trans_engine_combo.setCurrentIndex(index if index >= 0 else 0)
            self._ob_trans_engine_combo.blockSignals(False)
            size = str(data.get("whisper_model_size") or "small")
            size_index = self._ob_trans_size_combo.findData(size)
            self._ob_trans_size_combo.blockSignals(True)
            self._ob_trans_size_combo.setCurrentIndex(size_index if size_index >= 0 else 0)
            self._ob_trans_size_combo.blockSignals(False)
            self._on_trans_engine_changed(self._ob_trans_engine_combo.currentIndex())
            self._ob_trans_msg.clear()
            self._refresh_trans_status()

        def on_error(message: str) -> None:
            self._ob_trans_msg.set_message(
                f"读取转写配置失败：{message}（可以跳过此步稍后设置）", "warning"
            )

        self.controller.run_async(api.transcriber_config, on_result=on_config, on_error=on_error)
        self._start_trans_poll()

    def _start_trans_poll(self) -> None:
        if not hasattr(self, "_trans_poll_timer"):
            self._trans_poll_timer = QTimer(self)
            self._trans_poll_timer.setInterval(2500)
            self._trans_poll_timer.timeout.connect(self._refresh_trans_status)
        self._trans_poll_timer.start()

    def _stop_trans_poll(self) -> None:
        timer = getattr(self, "_trans_poll_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _on_trans_engine_changed(self, _index: int) -> None:
        is_local = str(self._ob_trans_engine_combo.currentData() or "fast-whisper") == "fast-whisper"
        self._ob_trans_size_combo.setEnabled(is_local)
        self._ob_trans_download_button.setEnabled(is_local and self._engine_running())
        if not is_local:
            self._ob_trans_dot.set_state(style.WARN, busy=False)
            self._ob_trans_badge.set_state("warn", "在线引擎")
            self._ob_trans_status_label.setText("在线引擎无需下载本地模型")
            self._ob_trans_msg.set_message(
                "使用 Groq 前，请先在「AI 模型」页选择 Groq 供应商并填写 Key。", "info"
            )

    def _refresh_trans_status(self) -> None:
        if not self._engine_running():
            return
        try:
            api = self.controller.engine_api()
        except Exception:  # noqa: BLE001
            return

        def on_result(data) -> None:
            self._trans_status = data if isinstance(data, dict) else {}
            self._render_trans_status()

        def on_error(message: str) -> None:
            self._ob_trans_msg.set_message(f"读取模型状态失败：{message}", "warning")

        self.controller.run_async(api.transcriber_status, on_result=on_result, on_error=on_error)

    def _render_trans_status(self) -> None:
        engine_type = str(self._ob_trans_engine_combo.currentData() or "fast-whisper")
        if engine_type != "fast-whisper":
            return
        size = str(self._ob_trans_size_combo.currentData() or "small")
        rows = self._trans_status.get("whisper")
        if not isinstance(rows, list):
            return
        row = next(
            (item for item in rows if isinstance(item, dict) and str(item.get("model_size")) == size),
            None,
        )
        if row is None:
            self._ob_trans_dot.set_state(style.IDLE)
            self._ob_trans_badge.set_state("neutral", "未知")
            self._ob_trans_status_label.setText(f"未找到模型 {size} 的状态")
            return
        downloaded = bool(row.get("downloaded"))
        downloading = bool(row.get("downloading"))
        failed = bool(row.get("failed"))
        if downloaded:
            self._ob_trans_dot.set_state(style.OK)
            self._ob_trans_badge.set_state("ok", "已下载")
            self._ob_trans_status_label.setText(f"{size} 模型已就绪，可以开始生成笔记。")
            self._ob_trans_download_button.setText("重新下载")
            self._ob_trans_download_button.setEnabled(self._engine_running())
        elif downloading:
            self._ob_trans_dot.set_state(style.WARN, busy=True)
            self._ob_trans_badge.set_state("busy", "下载中")
            self._ob_trans_status_label.setText(f"正在下载 {size} 模型，请稍候…")
            self._ob_trans_download_button.setEnabled(False)
        elif failed:
            self._ob_trans_dot.set_state(style.DANGER)
            self._ob_trans_badge.set_state("error", "失败")
            self._ob_trans_status_label.setText(
                f"下载失败：{utils.clip_text(row.get('error') or '未知原因', 120)}"
            )
            self._ob_trans_download_button.setText("重试下载")
            self._ob_trans_download_button.setEnabled(self._engine_running())
        else:
            self._ob_trans_dot.set_state(style.WARN)
            self._ob_trans_badge.set_state("warn", "未下载")
            self._ob_trans_status_label.setText(
                f"{size} 模型尚未下载，点击「保存并下载模型」开始下载。"
            )
            self._ob_trans_download_button.setText("保存并下载模型")
            self._ob_trans_download_button.setEnabled(self._engine_running())

    def _download_trans_model(self) -> None:
        if not self._engine_running():
            self._ob_trans_msg.set_message("引擎未运行，无法下载模型。", "warning")
            return
        engine_type = str(self._ob_trans_engine_combo.currentData() or "fast-whisper")
        size = str(self._ob_trans_size_combo.currentData() or "small")
        try:
            api = self.controller.engine_api()
        except Exception as exc:  # noqa: BLE001
            self._ob_trans_msg.set_message(f"无法访问引擎：{exc}", "error")
            return
        self._ob_trans_download_button.setEnabled(False)
        self._ob_trans_msg.set_message("正在保存转写设置…", "info")

        def saved(_data) -> None:
            self._trans_saved = True
            self.controller.config.quality = str(self.controller.config.quality or "fast")
            self._ob_trans_msg.set_message("设置已保存，正在开始下载模型…", "info")
            if engine_type == "fast-whisper":
                self.controller.run_async(
                    api.download_whisper,
                    size,
                    "fast-whisper",
                    on_result=download_started,
                    on_error=download_error,
                )
            else:
                self._ob_trans_msg.set_message(
                    "已保存为 Groq 在线转写。请记得在「AI 模型」页配置 Groq 的 Key。",
                    "info",
                )
                self._ob_trans_download_button.setEnabled(True)

        def save_error(message: str) -> None:
            self._ob_trans_msg.set_message(f"保存转写设置失败：{message}", "error")
            self._ob_trans_download_button.setEnabled(True)

        self.controller.run_async(
            api.update_transcriber_config,
            engine_type,
            size,
            on_result=saved,
            on_error=save_error,
        )

        def download_started(_data) -> None:
            self._ob_trans_msg.set_message(
                f"已开始下载 {size} 模型，状态每 2.5 秒刷新一次。", "success"
            )
            self._trans_saved = True
            self._refresh_trans_status()

        def download_error(message: str) -> None:
            self._ob_trans_msg.set_message(f"开始下载失败：{message}", "error")
            self._ob_trans_download_button.setEnabled(True)

    # ------------------------------------------------------------ 配对
    def _enter_pair_step(self) -> None:
        """配对步骤始终可用：即使引擎/Agent 没起来也能生成配对码。"""
        self._pairing_panel.update_status(self._status)
        self._pairing_panel.ensure_pairing_code()

    def _paired_device_name(self) -> str:
        status = self._status or {}
        devices = ((status.get("relay") or {}).get("devices")) or []
        for device in devices:
            if isinstance(device, dict) and device.get("name"):
                return str(device.get("name"))
        agent = status.get("agent") or {}
        return str(agent.get("device_name") or "")

    # ------------------------------------------------------------ 完成
    def _refresh_summary(self) -> None:
        from .widgets import clear_layout

        clear_layout(self._ob_summary_layout)
        engine = (self._status or {}).get("engine") or {}
        paired = bool(getattr(self.controller.agent, "is_paired", False))
        ffmpeg_ok = bool(self._ffmpeg_ok)
        rows = [
            (
                "ffmpeg",
                "已安装" if ffmpeg_ok else "未安装（可在引擎页安装）",
                "ok" if ffmpeg_ok else "warn",
            ),
            (
                "BiliNote 引擎",
                "运行中" if engine.get("state") == "running" else "未运行（可在状态页启动）",
                "ok" if engine.get("state") == "running" else "warn",
            ),
            (
                "AI 模型",
                "已保存 API Key" if self._ai_saved else "稍后配置（不配置无法生成笔记）",
                "ok" if self._ai_saved else "warn",
            ),
            (
                "语音转写",
                "已设置" if self._trans_saved else "稍后设置",
                "ok" if self._trans_saved else "info",
            ),
            (
                "手机配对",
                "已配对" if paired else "稍后配对",
                "ok" if paired else "warn",
            ),
        ]
        for title, text, level in rows:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 3, 0, 3)
            row_layout.setSpacing(9)
            dot = StatusDot(
                {"ok": style.OK, "warn": style.WARN, "info": style.MUTED}.get(level, style.MUTED),
                8,
            )
            row_layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            label = QLabel(title)
            label.setFixedWidth(110)
            label.setStyleSheet(
                f"QLabel {{ color: {style.INK_SOFT}; font-size: 12px; font-weight: bold; }}"
            )
            row_layout.addWidget(label, 0)
            value = QLabel(text)
            value.setWordWrap(True)
            color = {
                "ok": style.OK,
                "warn": "#92400E",
                "info": style.MUTED,
            }.get(level, style.INK_SOFT)
            value.setStyleSheet(f"QLabel {{ color: {color}; font-size: 12px; }}")
            row_layout.addWidget(value, 1)
            self._ob_summary_layout.addWidget(row_widget)
        if not ffmpeg_ok:
            self._ob_finish_note.setText(
                "提示：ffmpeg 尚未安装，下载与转写会受影响；可在「引擎与 ffmpeg」页继续安装。"
            )
        elif engine.get("state") != "running":
            self._ob_finish_note.setText(
                "提示：引擎当前未运行；点击「完成」后 LinkNote 会尝试自动启动，也可以在状态页手动启动。"
            )
        else:
            self._ob_finish_note.setText(
                "点击「完成，开始使用」后，LinkNote 会在后台运行；关闭窗口会最小化到托盘。"
            )

    # ============================================================ 控制器信号
    def _connect_controller(self) -> None:
        try:
            self.controller.statusChanged.connect(self._on_status)
            self.controller.engineChanged.connect(self._on_engine_changed)
            self.controller.ffmpegProgress.connect(self._on_ffmpeg_progress)
            self.controller.notification.connect(self._on_notification)
            self.controller.pairingChanged.connect(self._on_pairing_changed)
        except Exception:  # noqa: BLE001
            pass

    def _disconnect_controller(self) -> None:
        for signal, slot in (
            (self.controller.statusChanged, self._on_status),
            (self.controller.engineChanged, self._on_engine_changed),
            (self.controller.ffmpegProgress, self._on_ffmpeg_progress),
            (self.controller.notification, self._on_notification),
            (self.controller.pairingChanged, self._on_pairing_changed),
        ):
            try:
                signal.disconnect(slot)
            except Exception:  # noqa: BLE001
                pass
        self._stop_engine_wait()
        self._stop_trans_poll()

    def _on_status(self, status: dict) -> None:
        self._status = status or {}
        if self._step == self.ENGINE:
            self._apply_engine_state()
        if self._step == self.FINISH:
            self._refresh_summary()
        if self._step == self.PAIR:
            self._pairing_panel.update_status(self._status)

    def _on_engine_changed(self, data: dict) -> None:
        state = str((data or {}).get("state") or "")
        if state == "ffmpeg_ready":
            self._on_ffmpeg_ready_event()
        if self._step == self.ENGINE:
            self._apply_engine_state()
            if state in ("running", "error"):
                self._engine_start_requested = False

    def _on_ffmpeg_ready_event(self) -> None:
        self._ffmpeg_downloading = False
        self._ffmpeg_progress.hide()
        self._ffmpeg_progress_label.hide()
        self._ffmpeg_download_button.setEnabled(True)
        self._apply_ffmpeg_ready(True)

    def _on_notification(self, title: str, message: str, level: str) -> None:
        combined = f"{title} {message}".lower()
        if "ffmpeg" in combined:
            if level == "success":
                self._on_ffmpeg_ready_event()
            elif level in ("error", "warning"):
                self._ffmpeg_downloading = False
                self._ffmpeg_progress.hide()
                self._ffmpeg_progress_label.hide()
                self._ffmpeg_download_button.setEnabled(True)
                self._ffmpeg_msg.set_message(f"{message}", "error")
            return
        if "引擎" in combined and self._step == self.ENGINE:
            if level == "success":
                self._apply_engine_state()
            elif level == "error":
                self._engine_msg.set_message(message, "error")
                self._engine_retry_button.setEnabled(True)
        if "配对" in combined and self._step == self.PAIR:
            self._pairing_panel.refresh()

    def _on_pairing_changed(self, state: dict) -> None:
        self._pairing = state or {}
        if self._step == self.PAIR:
            self._pairing_panel.update_pairing(self._pairing)

    # ============================================================ 关闭
    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._finished:
            self._finished = True
            try:
                self.controller.config.onboarding_done = True
                self.controller.save_config()
            except Exception:  # noqa: BLE001
                pass
            self._disconnect_controller()
            try:
                self.reject()
            except Exception:  # noqa: BLE001
                pass
        event.accept()


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



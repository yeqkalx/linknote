"""AI 模型页：供应商 / API Key / 可用模型勾选与手机端默认模型。

用户反馈的关键点：
- 保存或测试 API Key 成功后必须**立刻**自动拉取该供应商可用模型，不需要手动刷新/重启；
- 模型列表用可勾选列表（BiliNote 只支持一个默认模型，因此 UI 为单选勾选）；
- 保存后立即写回配置并同步 Agent，成功提示「已生效」。
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import style, utils
from ..widgets import (
    Card,
    CheckRow,
    InlineMessage,
    button,
    clear_layout,
    field_label,
    hint_label,
)
from .base import BasePage


class AiPage(BasePage):
    PAGE_KEY = "ai"
    PAGE_TITLE = "AI 模型"
    PAGE_SUBTITLE = (
        "选择生成笔记用的大模型。API Key 只保存在本机引擎数据库，不经链记中继；"
        "但生成笔记时，必要的转写文本会发送给你选择的模型服务商。"
    )
    NEEDS_ENGINE = True

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._providers: list[dict[str, Any]] = []
        self._providers_loaded = False
        self._loading_providers = False
        self._available_models: list[str] = []
        self._db_models: set[str] = set()
        self._selected_model = ""
        self._model_rows: dict[str, CheckRow] = {}
        self._catalog_provider_id = ""

        self._build_provider_card()
        self._build_model_card()
        self.add(
            hint_label(
                "提示：手机端提交任务时会使用这里的「手机默认模型」；"
                "如果测试连接失败，请检查 API Key 与 Base URL 是否正确。"
            )
        )

    # ------------------------------------------------------------ 供应商
    def _build_provider_card(self) -> None:
        card = Card()
        self._provider_card = card
        self._refresh_provider_button = button("刷新", "", self._load_providers)
        card.add_header(
            "模型供应商",
            "从 BiliNote 引擎读取已配置的供应商；填好 Key 后保存或测试，就会自动拉取可用模型。",
            trailing=self._refresh_provider_button,
        )

        combo_row = QHBoxLayout()
        combo_row.setContentsMargins(0, 0, 0, 0)
        combo_row.setSpacing(10)
        combo_row.addWidget(field_label("供应商"), 0)
        self._provider_combo = QComboBox()
        self._provider_combo.setMinimumWidth(240)
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        combo_row.addWidget(self._provider_combo, 1)
        card.body.addLayout(combo_row)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("sk-…（粘贴后点「保存并获取模型」）")
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(8)
        key_row.addWidget(self._api_key_edit, 1)
        self._key_toggle_button = QPushButton("显示")
        self._key_toggle_button.setCheckable(True)
        self._key_toggle_button.setFixedWidth(62)
        self._key_toggle_button.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self._key_toggle_button, 0)
        key_container = QWidget()
        key_container.setLayout(key_row)
        card.body.addWidget(_field("API Key", key_container))

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        card.body.addWidget(
            _field(
                "Base URL",
                self._base_url_edit,
                "OpenAI 兼容接口地址，末尾不要带 /chat/completions。",
            )
        )

        note = InlineMessage(
            "API Key 保存在本机 BiliNote 引擎的数据库；LinkNote 不会把它上传到 Relay。",
            "info",
        )
        card.body.addWidget(note)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self._save_provider_button = button("保存并获取模型", "Primary", self._save_provider)
        self._test_provider_button = button("测试连接", "", self._test_provider)
        action_row.addWidget(self._save_provider_button, 0)
        action_row.addWidget(self._test_provider_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._provider_msg = InlineMessage()
        card.body.addWidget(self._provider_msg)
        self.add(card)

    # ------------------------------------------------------------ 模型
    def _build_model_card(self) -> None:
        card = Card()
        self._model_card = card
        self._refresh_model_button = button("刷新列表", "", self._load_models)
        card.add_header(
            "可用模型",
            "勾选一个模型作为手机端默认。保存后立即写入 Agent 配置并生效。",
            trailing=self._refresh_model_button,
        )

        self._model_rows_container = QWidget()
        self._model_rows_layout = QVBoxLayout(self._model_rows_container)
        self._model_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._model_rows_layout.setSpacing(6)
        card.body.addWidget(self._model_rows_container)

        self._model_empty = QLabel("正在获取可用模型…")
        self._model_empty.setObjectName("Help")
        self._model_empty.setWordWrap(True)
        card.body.addWidget(self._model_empty)

        self._model_msg = InlineMessage()
        card.body.addWidget(self._model_msg)

        self._default_label = QLabel("")
        self._default_label.setObjectName("Muted")
        self._default_label.setWordWrap(True)
        card.body.addWidget(self._default_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self._save_default_button = button("保存为手机默认", "Primary", self._save_default_model)
        action_row.addWidget(self._save_default_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 2, 0, 0)
        add_row.setSpacing(8)
        self._new_model_edit = QLineEdit()
        self._new_model_edit.setPlaceholderText("列表里没有？手动输入模型名后添加")
        self._new_model_edit.returnPressed.connect(self._add_model)
        add_row.addWidget(self._new_model_edit, 1)
        self._add_model_button = button("添加模型", "", self._add_model)
        add_row.addWidget(self._add_model_button, 0)
        card.body.addLayout(add_row)
        self.add(card)

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        if self.engine_running() and not self._providers_loaded:
            self._load_providers()
        else:
            self._update_default_label()

    def set_engine_available(self, running: bool) -> None:
        controls = [
            self._provider_combo,
            self._refresh_provider_button,
            self._key_toggle_button,
            self._save_provider_button,
            self._test_provider_button,
            self._refresh_model_button,
            self._new_model_edit,
            self._add_model_button,
            self._save_default_button,
        ]
        for control in controls:
            control.setEnabled(running)
        self._api_key_edit.setEnabled(running)
        self._base_url_edit.setEnabled(running)
        for row in self._model_rows.values():
            row.setEnabled(running)
        self._update_save_button()
        if running and not self._providers_loaded and not self._loading_providers:
            # 引擎可用后预加载供应商，页面打开时直接有内容
            self._load_providers()

    # ------------------------------------------------------------ 供应商
    def _current_provider(self) -> Optional[dict[str, Any]]:
        index = self._provider_combo.currentIndex()
        if 0 <= index < len(self._providers):
            return self._providers[index]
        return None

    def _load_providers(self) -> None:
        if self._loading_providers:
            return
        self._loading_providers = True
        self._refresh_provider_button.setEnabled(False)
        self._provider_msg.set_message("正在加载供应商列表…", "info")

        def on_result(data) -> None:
            self._loading_providers = False
            self._refresh_provider_button.setEnabled(self.engine_running())
            self._providers = [item for item in (data or []) if isinstance(item, dict)]
            self._providers_loaded = True
            self._provider_combo.blockSignals(True)
            self._provider_combo.clear()
            for provider in self._providers:
                name = provider.get("name") or provider.get("id") or "未命名供应商"
                type_name = provider.get("type") or "custom"
                self._provider_combo.addItem(
                    f"{name}（{type_name}）", str(provider.get("id") or "")
                )
            self._provider_combo.blockSignals(False)
            if not self._providers:
                self._provider_msg.set_message(
                    "引擎里还没有配置任何供应商。请先在引擎侧添加供应商，再回到这里填写 API Key。",
                    "warning",
                )
                self._provider_combo.setCurrentIndex(-1)
                self._clear_models("还没有供应商，无法获取模型。")
                return
            target = str(self.controller.config.provider_id or "")
            index = 0
            for position, provider in enumerate(self._providers):
                if str(provider.get("id") or "") == target:
                    index = position
                    break
            self._provider_combo.setCurrentIndex(index)
            self._on_provider_changed(index)
            self._provider_msg.clear()

        def on_error(message: str) -> None:
            self._loading_providers = False
            self._refresh_provider_button.setEnabled(self.engine_running())
            self._provider_msg.set_message(f"加载供应商失败：{message}", "error")

        self.run_api("providers", on_result=on_result, on_error=on_error)

    def _on_provider_changed(self, index: int) -> None:
        provider = self._current_provider()
        if provider is None:
            self._clear_models("请先选择供应商。")
            return
        self._api_key_edit.setText(str(provider.get("api_key") or ""))
        self._base_url_edit.setText(str(provider.get("base_url") or ""))
        self._catalog_provider_id = str(provider.get("id") or "")
        self._clear_models("正在获取可用模型…")
        self._load_models()

    def _toggle_key_visibility(self) -> None:
        visible = self._key_toggle_button.isChecked()
        self._api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self._key_toggle_button.setText("隐藏" if visible else "显示")

    def _save_provider(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._provider_msg.set_message("请先选择一个供应商。", "warning")
            return
        provider_id = str(provider.get("id") or "")
        api_key = self._api_key_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        fields: dict[str, Any] = {}
        if api_key and "*" not in api_key:
            fields["api_key"] = api_key
        if base_url:
            fields["base_url"] = base_url
        if not fields:
            self._provider_msg.set_message(
                "没有需要保存的修改（API Key 显示为掩码时不会被覆盖）。", "warning"
            )
            return

        self._save_provider_button.setEnabled(False)
        self._provider_msg.set_message("正在保存，随后会自动获取可用模型…", "info")

        def on_result(_data) -> None:
            self._save_provider_button.setEnabled(self.engine_running())
            if "api_key" in fields:
                provider["api_key"] = fields["api_key"]
            if "base_url" in fields:
                provider["base_url"] = fields["base_url"]
            self._provider_msg.set_message(
                "供应商配置已保存，正在自动获取可用模型…", "success"
            )
            self.toast("供应商配置已保存，正在获取模型", "success")
            self._clear_models("正在获取可用模型…")
            self._load_models()

        def on_error(message: str) -> None:
            self._save_provider_button.setEnabled(self.engine_running())
            self._provider_msg.set_message(f"保存失败：{message}", "error")

        self.run_api("update_provider", provider_id, on_result=on_result, on_error=on_error, **fields)

    def _test_provider(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._provider_msg.set_message("请先选择一个供应商。", "warning")
            return
        provider_id = str(provider.get("id") or "")
        model = self._selected_model or None
        self._provider_msg.set_message("正在测试连接…", "info")
        self._test_provider_button.setEnabled(False)

        def on_result(_data) -> None:
            self._test_provider_button.setEnabled(self.engine_running())
            self._provider_msg.set_message(
                "连接成功，正在自动获取可用模型…", "success"
            )
            self._clear_models("正在获取可用模型…")
            self._load_models()

        def on_error(message: str) -> None:
            self._test_provider_button.setEnabled(self.engine_running())
            self._provider_msg.set_message(f"连接失败：{message}", "error")

        self.run_api(
            "connect_test",
            provider_id,
            model,
            on_result=on_result,
            on_error=on_error,
        )

    # ------------------------------------------------------------ 模型列表
    def _clear_models(self, message: str = "") -> None:
        self._available_models = []
        self._db_models = set()
        self._selected_model = ""
        self._model_rows = {}
        clear_layout(self._model_rows_layout)
        self._model_empty.setText(message or "暂无可用模型。")
        self._model_empty.show()
        self._update_save_button()

    def _load_models(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._clear_models("请先选择供应商。")
            return
        provider_id = str(provider.get("id") or "")
        self._catalog_provider_id = provider_id
        self._refresh_model_button.setEnabled(False)
        self._model_msg.set_message("正在获取该供应商的可用模型…", "info")
        self._model_empty.setText("正在获取可用模型…")
        self._model_empty.show()

        def on_result(data) -> None:
            self._refresh_model_button.setEnabled(self.engine_running())
            if self._catalog_provider_id != provider_id:
                return  # 用户已经切到别的供应商，丢弃旧结果
            catalog = data if isinstance(data, dict) else {}
            provider_models = [
                str(name)
                for name in (catalog.get("provider_models") or [])
                if str(name).strip()
            ]
            db_models = [str(name) for name in (catalog.get("db_models") or []) if str(name).strip()]
            names: list[str] = []
            for name in provider_models + db_models:
                if name not in names:
                    names.append(name)
            self._available_models = names
            self._db_models = set(db_models)
            error = str(catalog.get("error") or "")
            if error:
                self._model_msg.set_message(
                    f"动态获取模型失败：{utils.clip_text(error, 160)}。"
                    "可以点「刷新列表」重试，或在下方手动添加模型名。",
                    "warning",
                )
            else:
                self._model_msg.clear()
            config = self.controller.config
            same_provider = str(config.provider_id or "") == provider_id
            configured = str(config.model_name or "")
            if same_provider and configured in names:
                self._selected_model = configured
            elif names:
                self._selected_model = names[0]
            else:
                self._selected_model = ""
            self._render_model_rows()
            self._update_default_label()
            if not error:
                current_provider_msg = self._provider_msg._label.text()
                if "自动获取" in current_provider_msg or "连接成功" in current_provider_msg:
                    self._provider_msg.set_message("连接成功，可用模型列表已更新。", "success")

        def on_error(message: str) -> None:
            self._refresh_model_button.setEnabled(self.engine_running())
            if self._catalog_provider_id != provider_id:
                return
            self._clear_models("获取模型列表失败。")
            self._model_msg.set_message(
                f"获取模型列表失败：{message}。可点「刷新列表」重试，或手动添加模型名。",
                "error",
            )

        self.run_bg(
            self.controller.fetch_provider_model_catalog,
            provider_id,
            on_result=on_result,
            on_error=on_error,
        )

    def _render_model_rows(self) -> None:
        clear_layout(self._model_rows_layout)
        self._model_rows = {}
        names = self._available_models
        if not names:
            self._model_empty.setText(
                "暂无可用模型：请确认 API Key 正确后点「测试连接」，"
                "或在下方手动输入模型名添加。"
            )
            self._model_empty.show()
            self._update_save_button()
            return
        self._model_empty.hide()
        config = self.controller.config
        current_default = str(config.model_name or "")
        same_provider = str(config.provider_id or "") == self._catalog_provider_id
        for name in names:
            in_db = name in self._db_models
            subtitle = "已添加到 BiliNote 模型库" if in_db else "供应商可用模型"
            row = CheckRow(
                name,
                subtitle,
                badge_text="当前默认" if name == current_default else "",
                badge_state="ok" if name == current_default else "neutral",
            )
            row.setChecked(name == self._selected_model)
            row.setEnabled(self.engine_running())
            row.clicked.connect(lambda n=name: self._select_model(n))
            self._model_rows[name] = row
            self._model_rows_layout.addWidget(row)
        self._update_save_button()

    def _select_model(self, name: str) -> None:
        self._selected_model = str(name or "")
        for model_name, row in self._model_rows.items():
            row.setChecked(model_name == self._selected_model)
        self._update_save_button()

    def _add_model(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._model_msg.set_message("请先选择一个供应商。", "warning")
            return
        name = self._new_model_edit.text().strip()
        if not name:
            self._model_msg.set_message("请先输入模型名称。", "warning")
            return
        provider_id = str(provider.get("id") or "")
        self._add_model_button.setEnabled(False)

        def on_result(_data) -> None:
            self._add_model_button.setEnabled(self.engine_running())
            self._new_model_edit.clear()
            self._model_msg.set_message(f"已添加模型 {name}。", "success")
            self._selected_model = name
            self._load_models()

        def on_error(message: str) -> None:
            self._add_model_button.setEnabled(self.engine_running())
            self._model_msg.set_message(f"添加模型失败：{message}", "error")

        self.run_api("add_model", provider_id, name, on_result=on_result, on_error=on_error)

    def _update_save_button(self) -> None:
        config = self.controller.config
        provider = self._current_provider()
        selected = self._selected_model
        same = (
            provider is not None
            and str(provider.get("id") or "") == str(config.provider_id or "")
            and selected == str(config.model_name or "")
        )
        self._save_default_button.setEnabled(
            bool(selected) and not same and self.engine_running()
        )
        self._save_default_button.setText("已生效" if (same and selected) else "保存为手机默认")

    def _save_default_model(self) -> None:
        provider = self._current_provider()
        model = self._selected_model
        if provider is None or not model:
            self._model_msg.set_message("请先选择供应商和模型。", "warning")
            return
        provider_id = str(provider.get("id") or "")
        self._save_default_button.setEnabled(False)
        self._model_msg.set_message("正在保存模型设置并同步 Agent…", "info")

        def apply_config(extra_level: str = "success") -> None:
            config = self.controller.config
            config.provider_id = provider_id
            config.model_name = model
            restarted = False
            try:
                self.controller.save_config()
                if getattr(self.controller.agent, "running", False):
                    self.controller.restart_agent()
                    restarted = True
            except Exception as exc:  # noqa: BLE001
                self._model_msg.set_message(f"保存配置失败：{exc}", "error")
                self._update_save_button()
                return
            suffix = "（Agent 已自动重启生效）" if restarted else "（Agent 启动后生效）"
            self._model_msg.set_message(
                f"已保存并生效：手机新任务将使用 {model}{suffix}。", extra_level
            )
            self.toast(f"已设为手机默认模型：{model}", "success")
            self._update_default_label()
            self._render_model_rows()

        def on_result(_data) -> None:
            apply_config()

        def on_error(message: str) -> None:
            # 模型库写入失败不阻塞：配置仍然生效，提示用户可稍后重试
            apply_config("warning")
            self._model_msg.set_message(
                f"已设为手机默认模型 {model}，但写入引擎模型库失败：{message}。"
                "如果生成笔记时报模型不存在，请稍后重试或换一个模型。",
                "warning",
            )

        self.run_api(
            "add_model",
            provider_id,
            model,
            on_result=on_result,
            on_error=on_error,
            inline=None,
        )

    def _update_default_label(self) -> None:
        config = self.controller.config
        provider_name = ""
        for provider in self._providers:
            if str(provider.get("id") or "") == str(config.provider_id or ""):
                provider_name = str(provider.get("name") or "")
                break
        provider_text = provider_name or str(config.provider_id or "未设置")
        model_text = str(config.model_name or "未设置")
        self._default_label.setText(f"手机端默认：{provider_text} / {model_text}")


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

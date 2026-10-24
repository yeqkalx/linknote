"""AI 模型页：供应商 / API Key / 模型与手机端默认模型。"""
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

from .. import style
from ..widgets import Card, InlineMessage, StatusBadge, button, field_label, hint_label
from .base import BasePage


class AiPage(BasePage):
    PAGE_KEY = "ai"
    PAGE_TITLE = "AI 模型"
    PAGE_SUBTITLE = (
        "选择生成笔记用的大模型。API Key 只保存在本机 BiliNote 引擎的数据库，"
        "不会上传到 Relay。"
    )
    NEEDS_ENGINE = True

    # ------------------------------------------------------------ 构建
    def build(self) -> None:
        self._providers: list[dict[str, Any]] = []
        self._models: list[dict[str, Any]] = []
        self._providers_loaded = False
        self._loading_providers = False

        self._build_provider_card()
        self._build_model_card()
        self.add(
            hint_label(
                "提示：手机端提交任务时会带上「手机端默认」模型；"
                "如果这里测试连接失败，请检查 API Key 与 Base URL 是否正确。"
            )
        )

    def _build_provider_card(self) -> None:
        card = Card()
        self._provider_card = card
        self._refresh_provider_button = button("刷新", "", self._load_providers)
        card.add_header(
            "模型供应商",
            "从 BiliNote 引擎读取已配置的供应商。",
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
        self._api_key_edit.setPlaceholderText("sk-…（留空表示不修改）")
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
            _field("Base URL", self._base_url_edit, "OpenAI 兼容接口地址，末尾不要带 /chat/completions。")
        )

        note = InlineMessage(
            "API Key 保存在本机 BiliNote 引擎的数据库；LinkNote 不会把它上传到 Relay。",
            "info",
        )
        card.body.addWidget(note)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self._save_provider_button = button("保存", "Primary", self._save_provider)
        self._test_provider_button = button("测试连接", "", self._test_provider)
        action_row.addWidget(self._save_provider_button, 0)
        action_row.addWidget(self._test_provider_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._provider_msg = InlineMessage()
        card.body.addWidget(self._provider_msg)
        self.add(card)

    def _build_model_card(self) -> None:
        card = Card()
        self._model_card = card
        self._refresh_model_button = button("刷新", "", self._load_models)
        card.add_header(
            "模型与手机端默认",
            "从引擎模型库按供应商过滤；设为默认后会写入桌面端设置并同步给 Agent。",
            trailing=self._refresh_model_button,
        )

        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(10)
        model_row.addWidget(field_label("默认模型"), 0)
        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(240)
        model_row.addWidget(self._model_combo, 1)
        card.body.addLayout(model_row)

        self._default_label = QLabel("")
        self._default_label.setObjectName("Muted")
        self._default_label.setWordWrap(True)
        card.body.addWidget(self._default_label)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(8)
        self._new_model_edit = QLineEdit()
        self._new_model_edit.setPlaceholderText("输入模型名，例如 deepseek-v4-flash")
        self._new_model_edit.returnPressed.connect(self._add_model)
        add_row.addWidget(self._new_model_edit, 1)
        self._add_model_button = button("添加模型", "", self._add_model)
        add_row.addWidget(self._add_model_button, 0)
        card.body.addLayout(add_row)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self._set_default_button = button("设为手机默认", "Primary", self._set_default_model)
        action_row.addWidget(self._set_default_button, 0)
        action_row.addStretch(1)
        card.body.addLayout(action_row)

        self._model_msg = InlineMessage()
        card.body.addWidget(self._model_msg)
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
            self._model_combo,
            self._refresh_model_button,
            self._new_model_edit,
            self._add_model_button,
            self._set_default_button,
        ]
        for control in controls:
            control.setEnabled(running)
        self._api_key_edit.setEnabled(running)
        self._base_url_edit.setEnabled(running)
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
                self._provider_combo.addItem(f"{name}（{type_name}）", str(provider.get("id") or ""))
            self._provider_combo.blockSignals(False)
            if not self._providers:
                self._provider_msg.set_message(
                    "引擎里还没有配置任何供应商。请先在引擎侧添加供应商，再回到这里填写 API Key。",
                    "warning",
                )
                self._provider_combo.setCurrentIndex(-1)
                self._update_default_label()
                return
            target = str(self.controller.config.provider_id or "")
            index = 0
            for i, provider in enumerate(self._providers):
                if str(provider.get("id") or "") == target:
                    index = i
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
            return
        api_key = str(provider.get("api_key") or "")
        base_url = str(provider.get("base_url") or "")
        self._api_key_edit.setText(api_key)
        self._base_url_edit.setText(base_url)
        self._model_combo.clear()
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

        self._provider_msg.set_message("正在保存…", "info")

        def on_result(_data) -> None:
            if "api_key" in fields:
                provider["api_key"] = fields["api_key"]
            if "base_url" in fields:
                provider["base_url"] = fields["base_url"]
            self._provider_msg.set_message("供应商配置已保存。", "success")
            self.toast("供应商配置已保存", "success")

        self.run_api("update_provider", provider_id, on_result=on_result, **fields)

    def _test_provider(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._provider_msg.set_message("请先选择一个供应商。", "warning")
            return
        provider_id = str(provider.get("id") or "")
        model = self._model_combo.currentText().strip() or None
        self._provider_msg.set_message("正在测试连接…", "info")
        self._test_provider_button.setEnabled(False)

        def on_result(_data) -> None:
            self._test_provider_button.setEnabled(self.engine_running())
            self._provider_msg.set_message("连接成功，模型可以正常调用。", "success")

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

    # ------------------------------------------------------------ 模型
    def _load_models(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self._model_combo.clear()
            self._update_default_label()
            return
        provider_id = str(provider.get("id") or "")
        self._model_msg.set_message("正在加载模型列表…", "info")
        self._refresh_model_button.setEnabled(False)

        def on_result(data) -> None:
            self._refresh_model_button.setEnabled(self.engine_running())
            models = [
                item
                for item in (data or [])
                if isinstance(item, dict) and str(item.get("provider_id")) == provider_id
            ]
            self._models = models
            names = [str(item.get("model_name") or "") for item in models]
            names = [name for name in names if name]
            self._model_combo.blockSignals(True)
            self._model_combo.clear()
            self._model_combo.addItems(names)
            self._model_combo.blockSignals(False)
            if not names:
                self._model_combo.setCurrentIndex(-1)
                self._model_msg.set_message(
                    "这个供应商还没有可用模型，可在下方输入模型名添加。", "warning"
                )
            else:
                config = self.controller.config
                if str(config.provider_id or "") == provider_id:
                    target = str(config.model_name or "")
                    if target in names:
                        self._model_combo.setCurrentIndex(names.index(target))
                self._model_msg.clear()
            self._update_default_label()

        def on_error(message: str) -> None:
            self._refresh_model_button.setEnabled(self.engine_running())
            self._model_combo.clear()
            self._model_msg.set_message(f"加载模型列表失败：{message}", "error")
            self._update_default_label()

        self.run_api("models", on_result=on_result, on_error=on_error)

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
            self._load_models()

        def on_error(message: str) -> None:
            self._add_model_button.setEnabled(self.engine_running())
            self._model_msg.set_message(f"添加模型失败：{message}", "error")

        self.run_api("add_model", provider_id, name, on_result=on_result, on_error=on_error)

    def _set_default_model(self) -> None:
        provider = self._current_provider()
        model = self._model_combo.currentText().strip()
        if provider is None or not model:
            self._model_msg.set_message("请先选择供应商和模型。", "warning")
            return
        self.controller.config.provider_id = str(provider.get("id") or "")
        self.controller.config.model_name = model
        self.controller.save_config()
        self._update_default_label()
        provider_name = provider.get("name") or provider.get("id")
        self._model_msg.set_message(
            f"已设为手机默认：{provider_name} / {model}。", "success"
        )
        self.toast("已设为手机端默认模型", "success")

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

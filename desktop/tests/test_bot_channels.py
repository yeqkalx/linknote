"""消息入口（Telegram Bot）单元测试。

覆盖：DesktopConfig roundtrip、ensure_agent_config 写 agent.json 且保留既有字段、
mask_secret、以及 controller 的 Telegram getMe 连通性测试（mock httpx，不访问网络，
并验证 token 不会出现在错误信息里）。QQ 入口已在 v0.5 删除。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

import linknote_desktop.controller as controller_module
from linknote_desktop.config import DesktopConfig, ensure_agent_config, mask_secret
from linknote_desktop.controller import DesktopController


class _Dummy:
    """test_telegram_bot 不依赖实例状态，用空对象调用即可。"""


def test_config_roundtrip_telegram_fields(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    config = DesktopConfig(
        relay_base_url="https://relay.example.com",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
        telegram_bot_username="my_linknote_bot",
        telegram_allow_groups=True,
        archive_enabled=True,
        archive_dir=str(tmp_path / "vault"),
    )
    config.save(path)
    loaded = DesktopConfig.load(path)
    assert loaded.relay_base_url == "https://relay.example.com"
    assert loaded.telegram_enabled is True
    assert loaded.telegram_bot_token == "123456:AAH-token"
    assert loaded.telegram_bot_username == "my_linknote_bot"
    assert loaded.telegram_allow_groups is True
    assert loaded.archive_enabled is True
    assert loaded.archive_dir == str(tmp_path / "vault")


def test_config_roundtrip_telegram_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    DesktopConfig().save(path)
    loaded = DesktopConfig.load(path)
    assert loaded.relay_base_url == ""
    assert loaded.telegram_enabled is False
    assert loaded.telegram_bot_token == ""
    assert loaded.telegram_allow_groups is False
    # v0.5 删除 QQ：不再有 qq_* 字段
    assert not hasattr(loaded, "qq_enabled")
    assert not hasattr(loaded, "qq_app_id")
    assert not hasattr(loaded, "qq_app_secret")


def test_ensure_agent_config_writes_telegram_fields(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    agent_path = tmp_path / "LinkNote" / "agent.json"
    agent_path.parent.mkdir(parents=True)
    # 模拟已配对 Agent：既有 token / bot device token 必须保留
    agent_path.write_text(
        json.dumps(
            {
                "agent_token": "agt_existing",
                "agent_id": "agent-1",
                "telegram_device_token": "dev_telegram_existing",
            }
        ),
        encoding="utf-8",
    )
    config = DesktopConfig(
        relay_base_url="https://relay.example.com",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
        telegram_bot_username="my_linknote_bot",
        telegram_allow_groups=True,
    )
    data = ensure_agent_config(config)
    written = json.loads(agent_path.read_text(encoding="utf-8"))
    for value in (data, written):
        assert value["relay_base_url"] == "https://relay.example.com"
        assert value["telegram_enabled"] is True
        assert value["telegram_bot_token"] == "123456:AAH-token"
        assert value["telegram_bot_username"] == "my_linknote_bot"
        assert value["telegram_allow_groups"] is True
        assert value["agent_token"] == "agt_existing"
        assert value["telegram_device_token"] == "dev_telegram_existing"
        assert value["version"] == "0.5.0"
        assert not any(str(key).startswith("qq_") for key in value)


def test_mask_secret() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == "•••••"
    masked = mask_secret("1234567890:AAH-token")
    assert masked == "1234…oken"
    assert "AAH" not in masked


# ---------------------------------------------------------------- Telegram 连通性


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """替换 httpx.Client：记录请求并返回预设响应/异常。"""

    response: _FakeResponse | None = None
    error: Exception | None = None
    last_url = ""

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def get(self, url: str) -> _FakeResponse:
        _FakeClient.last_url = url
        if _FakeClient.error is not None:
            raise _FakeClient.error
        assert _FakeClient.response is not None
        return _FakeClient.response


def _patch_httpx(monkeypatch, *, response=None, error=None) -> None:
    _FakeClient.response = response
    _FakeClient.error = error
    monkeypatch.setattr(controller_module.httpx, "Client", _FakeClient)


def test_telegram_getme_success(monkeypatch) -> None:
    _patch_httpx(
        monkeypatch,
        response=_FakeResponse(
            200,
            {
                "ok": True,
                "result": {
                    "id": 1,
                    "username": "my_linknote_bot",
                    "first_name": "LinkNote",
                },
            },
        ),
    )
    result = DesktopController.test_telegram_bot(_Dummy(), "123456:AAH-token")
    assert result["ok"] is True
    assert result["username"] == "my_linknote_bot"
    assert result["name"] == "LinkNote"
    assert "123456:AAH-token" in _FakeClient.last_url


def test_telegram_getme_error_does_not_leak_token(monkeypatch) -> None:
    token = "123456:AAH-super-secret"
    _patch_httpx(
        monkeypatch,
        response=_FakeResponse(401, {"ok": False, "description": "Unauthorized"}),
    )
    try:
        DesktopController.test_telegram_bot(_Dummy(), token)
    except RuntimeError as exc:
        message = str(exc)
        assert "Unauthorized" in message
        assert token not in message
    else:
        raise AssertionError("expected RuntimeError")


def test_telegram_network_error_does_not_leak_token(monkeypatch) -> None:
    token = "123456:AAH-super-secret"
    _patch_httpx(monkeypatch, error=httpx.ConnectError("boom"))
    try:
        DesktopController.test_telegram_bot(_Dummy(), token)
    except RuntimeError as exc:
        message = str(exc)
        assert "无法连接 Telegram" in message
        assert token not in message
    else:
        raise AssertionError("expected RuntimeError")


def test_bot_credentials_required() -> None:
    try:
        DesktopController.test_telegram_bot(_Dummy(), "")
    except ValueError as exc:
        assert "Bot Token" in str(exc)
    else:
        raise AssertionError("expected ValueError")

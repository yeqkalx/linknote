"""消息入口（Telegram / QQ Bot）单元测试。

覆盖：DesktopConfig roundtrip、ensure_agent_config 写 agent.json 且保留既有字段、
mask_secret、以及 controller 的两个 Bot 连通性测试（mock httpx，不访问网络，
并验证 token / secret 不会出现在错误信息里）。
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
    """test_telegram_bot / test_qq_bot 不依赖实例状态，用空对象调用即可。"""


def test_config_roundtrip_bot_fields(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    config = DesktopConfig(
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
        telegram_bot_username="my_linknote_bot",
        telegram_allow_groups=True,
        qq_enabled=True,
        qq_app_id="1020304050",
        qq_app_secret="app-secret-value",
        archive_enabled=True,
        archive_dir=str(tmp_path / "vault"),
    )
    config.save(path)
    loaded = DesktopConfig.load(path)
    assert loaded.telegram_enabled is True
    assert loaded.telegram_bot_token == "123456:AAH-token"
    assert loaded.telegram_bot_username == "my_linknote_bot"
    assert loaded.telegram_allow_groups is True
    assert loaded.qq_enabled is True
    assert loaded.qq_app_id == "1020304050"
    assert loaded.qq_app_secret == "app-secret-value"
    assert loaded.archive_enabled is True
    assert loaded.archive_dir == str(tmp_path / "vault")


def test_config_roundtrip_bot_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    DesktopConfig().save(path)
    loaded = DesktopConfig.load(path)
    assert loaded.telegram_enabled is False
    assert loaded.telegram_bot_token == ""
    assert loaded.telegram_allow_groups is False
    assert loaded.qq_enabled is False
    assert loaded.qq_app_id == ""
    assert loaded.qq_app_secret == ""


def test_ensure_agent_config_writes_bot_fields(tmp_path: Path, monkeypatch) -> None:
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
                "qq_device_token": "dev_qq_existing",
            }
        ),
        encoding="utf-8",
    )
    config = DesktopConfig(
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
        telegram_bot_username="my_linknote_bot",
        telegram_allow_groups=True,
        qq_enabled=True,
        qq_app_id="1020304050",
        qq_app_secret="app-secret-value",
    )
    data = ensure_agent_config(config)
    written = json.loads(agent_path.read_text(encoding="utf-8"))
    for value in (data, written):
        assert value["telegram_enabled"] is True
        assert value["telegram_bot_token"] == "123456:AAH-token"
        assert value["telegram_bot_username"] == "my_linknote_bot"
        assert value["telegram_allow_groups"] is True
        assert value["qq_enabled"] is True
        assert value["qq_app_id"] == "1020304050"
        assert value["qq_app_secret"] == "app-secret-value"
        # 既有身份/运行时字段保留
        assert value["agent_token"] == "agt_existing"
        assert value["telegram_device_token"] == "dev_telegram_existing"
        assert value["qq_device_token"] == "dev_qq_existing"
        assert value["version"] == "0.4.0"


def test_mask_secret() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == "•••••"
    masked = mask_secret("1234567890:AAH-token")
    assert masked == "1234…oken"
    assert "AAH" not in masked


# ---------------------------------------------------------------- Bot 连通性测试


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
    last_body: Any = None

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

    def post(self, url: str, json: Any = None) -> _FakeResponse:
        _FakeClient.last_url = url
        _FakeClient.last_body = json
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
            200, {"ok": True, "result": {"id": 1, "username": "my_linknote_bot", "first_name": "LinkNote"}}
        ),
    )
    result = DesktopController.test_telegram_bot(_Dummy(), "123456:AAH-token")
    assert result["ok"] is True
    assert result["username"] == "my_linknote_bot"
    assert result["name"] == "LinkNote"
    assert "123456:AAH-token" in _FakeClient.last_url


def test_telegram_getme_error_does_not_leak_token(monkeypatch) -> None:
    token = "123456:AAH-super-secret"
    _patch_httpx(monkeypatch, response=_FakeResponse(401, {"ok": False, "description": "Unauthorized"}))
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


def test_qq_token_success(monkeypatch) -> None:
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"access_token": "qq-access", "expires_in": 7200}))
    result = DesktopController.test_qq_bot(_Dummy(), "1020304050", "app-secret-value")
    assert result == {"ok": True, "expires_in": 7200}
    assert _FakeClient.last_body == {
        "appId": "1020304050",
        "clientSecret": "app-secret-value",
    }


def test_qq_error_does_not_leak_secret(monkeypatch) -> None:
    secret = "qq-super-secret"
    _patch_httpx(monkeypatch, response=_FakeResponse(400, {"message": "invalid appid"}))
    try:
        DesktopController.test_qq_bot(_Dummy(), "bad-id", secret)
    except RuntimeError as exc:
        message = str(exc)
        assert "invalid appid" in message
        assert secret not in message
    else:
        raise AssertionError("expected RuntimeError")


def test_qq_network_error_does_not_leak_secret(monkeypatch) -> None:
    secret = "qq-super-secret"
    _patch_httpx(monkeypatch, error=httpx.ConnectError("boom"))
    try:
        DesktopController.test_qq_bot(_Dummy(), "1020304050", secret)
    except RuntimeError as exc:
        message = str(exc)
        assert "无法连接 QQ" in message
        assert secret not in message
    else:
        raise AssertionError("expected RuntimeError")


def test_bot_credentials_required() -> None:
    for func, args in (
        (DesktopController.test_telegram_bot, ("",)),
        (DesktopController.test_qq_bot, ("", "secret")),
        (DesktopController.test_qq_bot, ("appid", "")),
    ):
        try:
            func(_Dummy(), *args)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {func.__name__}{args}")

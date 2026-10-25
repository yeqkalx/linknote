"""Relay 配置与连通性测试（v0.5：默认 Relay 为空，必须用户自托管）。

覆盖：DEFAULT_RELAY_URL / DesktopConfig 默认为空、空 Relay 写入 agent.json、
controller.test_relay 校验 ok + realtime（mock httpx，不访问网络）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

import linknote_desktop.config as config_module
import linknote_desktop.controller as controller_module
from linknote_desktop.config import (
    DEFAULT_RELAY_URL,
    DesktopConfig,
    ensure_agent_config,
    normalize_relay_url,
)
from linknote_desktop.controller import DesktopController


class _Dummy:
    def __init__(self, relay_url: str = "") -> None:
        self.config = SimpleNamespace(relay_base_url=relay_url)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
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


def test_default_relay_is_empty() -> None:
    assert DEFAULT_RELAY_URL == ""
    assert DesktopConfig().relay_base_url == ""


def _legacy_author_host() -> str:
    # 不写作者域名字面量：用 config 里维护的迁移黑名单
    for host in sorted(config_module._LEGACY_RELAY_HOSTS):
        if host.startswith("relay."):
            return host
    raise AssertionError("legacy relay host not configured")


def test_legacy_relay_url_cleared_on_load_and_save(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    legacy = f"https://{_legacy_author_host().upper()}/"
    path.write_text(json.dumps({"relay_base_url": legacy}), encoding="utf-8")
    loaded = DesktopConfig.load(path)
    assert loaded.relay_base_url == ""
    assert getattr(loaded, "_legacy_relay_cleared", False) is True
    loaded.save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["relay_base_url"] == ""


def test_legacy_workers_dev_relay_cleared(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    legacy = (
        f"https://{config_module._LEGACY_WORKERS_DEV_PREFIX}.demo-account"
        f"{config_module._LEGACY_WORKERS_DEV_SUFFIX}"
    )
    path.write_text(json.dumps({"relay_base_url": legacy}), encoding="utf-8")
    loaded = DesktopConfig.load(path)
    assert loaded.relay_base_url == ""
    assert getattr(loaded, "_legacy_relay_cleared", False) is True


def test_custom_relay_url_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    custom = "https://relay.my-domain.example"
    path.write_text(json.dumps({"relay_base_url": custom}), encoding="utf-8")
    loaded = DesktopConfig.load(path)
    assert loaded.relay_base_url == custom
    assert getattr(loaded, "_legacy_relay_cleared", False) is False
    assert normalize_relay_url(custom + "/") == custom


def test_ensure_agent_config_cleans_legacy_relay(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    agent_path = tmp_path / "LinkNote" / "agent.json"
    agent_path.parent.mkdir(parents=True)
    legacy = f"https://{_legacy_author_host()}"
    agent_path.write_text(
        json.dumps({"agent_token": "agt_existing", "relay_base_url": legacy}),
        encoding="utf-8",
    )
    # 配置对象即使还持有旧地址，ensure_agent_config 也必须写空，不能让 Agent 继续连
    cfg = DesktopConfig(relay_base_url=legacy)
    ensure_agent_config(cfg)
    written = json.loads(agent_path.read_text(encoding="utf-8"))
    assert written["relay_base_url"] == ""
    assert written["agent_token"] == "agt_existing"
    assert legacy not in json.dumps(written)



def test_empty_relay_is_written_to_agent_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    agent_path = tmp_path / "LinkNote" / "agent.json"
    agent_path.parent.mkdir(parents=True)
    agent_path.write_text(json.dumps({"agent_token": "agt_existing"}), encoding="utf-8")
    ensure_agent_config(DesktopConfig())
    written = json.loads(agent_path.read_text(encoding="utf-8"))
    # 空 Relay 也必须写入，pc-agent 启动时会给出“未配置 Relay”的清晰错误
    assert written["relay_base_url"] == ""
    assert written["agent_token"] == "agt_existing"


def test_relay_health_ok_and_realtime(monkeypatch) -> None:
    _patch_httpx(
        monkeypatch,
        response=_FakeResponse(
            200,
            {
                "ok": True,
                "data": {"status": "ok", "version": "0.5.0", "realtime": True},
            },
        ),
    )
    result = DesktopController.test_relay(_Dummy(), "https://relay.example.com/")
    assert result == {"ok": True, "version": "0.5.0", "realtime": True, "status": "ok"}
    assert _FakeClient.last_url == "https://relay.example.com/api/v1/health"


def test_relay_health_requires_realtime(monkeypatch) -> None:
    _patch_httpx(
        monkeypatch,
        response=_FakeResponse(
            200,
            {"ok": True, "data": {"status": "ok", "version": "0.4.0"}},
        ),
    )
    try:
        DesktopController.test_relay(_Dummy(), "https://relay.example.com")
    except RuntimeError as exc:
        message = str(exc)
        assert "realtime" in message
        assert "0.4.0" in message
    else:
        raise AssertionError("expected RuntimeError")


def test_relay_health_http_error(monkeypatch) -> None:
    _patch_httpx(
        monkeypatch,
        response=_FakeResponse(404, {"ok": False}),
    )
    try:
        DesktopController.test_relay(_Dummy(), "https://relay.example.com")
    except RuntimeError as exc:
        assert "健康检查失败" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_relay_network_error(monkeypatch) -> None:
    _patch_httpx(monkeypatch, error=httpx.ConnectError("boom"))
    try:
        DesktopController.test_relay(_Dummy(), "https://relay.example.com")
    except RuntimeError as exc:
        assert "无法连接 Relay" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_relay_url_validation() -> None:
    for url in ("", "   ", "ftp://relay.example.com", "relay.example.com"):
        try:
            DesktopController.test_relay(_Dummy(), url)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {url!r}")


def test_relay_config_fallback(monkeypatch) -> None:
    """不传 url 时使用当前配置里的 Relay。"""
    _patch_httpx(
        monkeypatch,
        response=_FakeResponse(
            200, {"ok": True, "data": {"version": "0.5.0", "realtime": True}}
        ),
    )
    result = DesktopController.test_relay(_Dummy("https://relay.example.com"))
    assert result["ok"] is True
    try:
        DesktopController.test_relay(_Dummy(""))
    except ValueError as exc:
        assert "Relay" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty config relay")

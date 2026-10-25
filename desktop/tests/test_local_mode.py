"""Telegram 本地模式（无 Relay）测试：配置判定 + Agent 启动门禁。

v0.5.1 起：没有 Relay、但启用了 Telegram 且填了 Bot Token 时，Agent 允许以
Telegram 本地模式运行（无需手机配对）；没有 Relay 且没启用 Telegram 时仍阻塞。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from linknote_desktop.config import DesktopConfig
from linknote_desktop.controller import DesktopController
from linknote_desktop.services import agent_process as agent_process_module
from linknote_desktop.services.agent_process import AgentProcess


def _cfg(**kwargs: Any) -> DesktopConfig:
    return DesktopConfig(**kwargs)


def test_telegram_local_mode_true_without_relay() -> None:
    cfg = _cfg(
        relay_base_url="",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
    )
    assert cfg.relay_configured() is False
    assert cfg.telegram_configured() is True
    assert cfg.telegram_local_mode() is True


def test_telegram_local_mode_false_when_disabled_or_no_token() -> None:
    assert _cfg(relay_base_url="", telegram_enabled=False).telegram_local_mode() is False
    assert (
        _cfg(
            relay_base_url="",
            telegram_enabled=True,
            telegram_bot_token="   ",
        ).telegram_local_mode()
        is False
    )
    assert (
        _cfg(relay_base_url="", telegram_enabled=False, telegram_bot_token="x")
        .telegram_local_mode()
        is False
    )


def test_telegram_local_mode_false_with_relay() -> None:
    cfg = _cfg(
        relay_base_url="https://relay.example.com",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
    )
    assert cfg.relay_configured() is True
    assert cfg.telegram_local_mode() is False


def test_telegram_local_mode_legacy_relay_counts_as_empty() -> None:
    legacy = "https://" + "relay." + "xykong" + ".xyz"
    cfg = _cfg(
        relay_base_url=legacy,
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
    )
    assert cfg.relay_configured() is False
    assert cfg.telegram_local_mode() is True


def test_controller_local_mode_helpers() -> None:
    cfg = _cfg(
        relay_base_url="",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
    )
    controller = SimpleNamespace(config=cfg)
    assert DesktopController.telegram_local_mode(controller) is True
    assert DesktopController.relay_configured(controller) is False
    cfg.relay_base_url = "https://relay.example.com"
    assert DesktopController.telegram_local_mode(controller) is False


class _FakeProc:
    pid = 4242

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _prepare_agent_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    data_dir = tmp_path / "LinkNote"
    data_dir.mkdir(parents=True)
    # 未配对（agent.json 没有 agent_token）
    (data_dir / "agent.json").write_text(
        json.dumps({"relay_base_url": "", "telegram_enabled": True}),
        encoding="utf-8",
    )


def test_agent_process_allows_local_mode_start(tmp_path: Path, monkeypatch) -> None:
    _prepare_agent_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_process_module.subprocess, "Popen", lambda *a, **k: _FakeProc()
    )
    # ffmpeg 探测会调用 subprocess.run，避免被上面的 fake Popen 干扰
    monkeypatch.setattr(
        agent_process_module,
        "find_ffmpeg",
        lambda config: SimpleNamespace(bin_dir="", available=False, path="", version=""),
    )
    cfg = _cfg(
        relay_base_url="",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
    )
    process = AgentProcess(cfg)
    assert process.is_paired is False
    assert process.telegram_local_mode is True
    assert process.start() is True
    assert process.last_error == ""
    process.stop()


def test_agent_process_blocks_without_relay_and_telegram(
    tmp_path: Path, monkeypatch
) -> None:
    _prepare_agent_env(tmp_path, monkeypatch)
    cfg = _cfg(relay_base_url="", telegram_enabled=False)
    process = AgentProcess(cfg)
    assert process.telegram_local_mode is False
    assert process.start() is False
    assert "Telegram" in process.last_error


def test_pairing_guard_message_for_local_mode() -> None:
    """本地模式调用 create_pairing 时给出明确提示、不发起网络请求。"""
    cfg = _cfg(
        relay_base_url="",
        telegram_enabled=True,
        telegram_bot_token="123456:AAH-token",
    )
    controller = SimpleNamespace(
        config=cfg,
        relay_configured=lambda: cfg.relay_configured(),
        telegram_local_mode=lambda: cfg.telegram_local_mode(),
    )
    emitted: list[str] = []
    controller.error = SimpleNamespace(emit=lambda message: emitted.append(message))
    DesktopController.create_pairing_async(controller)
    assert emitted
    assert "本地模式" in emitted[0]
    assert "Relay" in emitted[0]

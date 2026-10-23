"""CLI：help/version、run 未配对拦截、status、unpair、install-autostart、--once 端到端。"""

from __future__ import annotations

import json
import sys

import pytest

from linknote_agent.cli import build_parser, cmd_install_autostart, main
from linknote_agent.config import AgentConfig, default_config_path

AGENT_TOKEN = "agt_test_token_0123456789"
RELAY_BASE = "http://relay.test"


def _save_paired_config(**overrides) -> AgentConfig:
    cfg = AgentConfig()
    cfg.relay_base_url = RELAY_BASE
    cfg.agent_token = AGENT_TOKEN
    cfg.agent_id = "11111111-1111-4111-8111-111111111111"
    cfg.agent_name = "我的电脑"
    for key, value in overrides.items():
        setattr(cfg, key, value)
    cfg.save()
    return cfg


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "pair" in out and "run" in out and "status" in out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_run_without_pair_returns_2(capsys):
    rc = main(["run"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "pair" in err and "还没有配对" in err


def test_status_without_pair(isolated_home, capsys, fake_bilinote, fake_relay):
    rc = main(
        ["status", "--json"],
        transport=fake_relay.transport,
        bilinote_transport=fake_bilinote.transport,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["paired"] is False
    assert data["backend_alive"] is True
    assert data["config_path"].endswith("agent.json")


def test_status_paired_shows_relay_info(capsys, fake_relay, fake_bilinote):
    _save_paired_config()
    fake_relay.devices.append({"id": "dev-1", "name": "Pixel 9", "platform": "android"})
    rc = main(
        ["status", "--json"],
        transport=fake_relay.transport,
        bilinote_transport=fake_bilinote.transport,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["paired"] is True
    assert data["agent_name"] == "我的电脑"
    assert data["token"].startswith("agt_test")
    assert data["relay"]["agent"]["id"] == fake_relay.agent["id"]
    assert data["relay"]["devices"][0]["name"] == "Pixel 9"


def test_status_relay_error_is_reported(capsys, fake_relay, fake_bilinote):
    _save_paired_config()
    fake_relay.down = True
    rc = main(
        ["status", "--json"],
        transport=fake_relay.transport,
        bilinote_transport=fake_bilinote.transport,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["relay_error"]
    assert data["relay"] is None


def test_status_human_output(capsys, fake_relay, fake_bilinote):
    _save_paired_config()
    rc = main(
        ["status"],
        transport=fake_relay.transport,
        bilinote_transport=fake_bilinote.transport,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "LinkNote PC Agent 状态" in out
    assert "配对：已配对" in out
    assert "BiliNote 后端" in out


def test_status_invalid_config_returns_2(capsys):
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    rc = main(["status"])
    assert rc == 2
    assert "配置错误" in capsys.readouterr().err


def test_unpair_clears_credentials(capsys, fake_relay):
    _save_paired_config()
    rc = main(["unpair", "--yes"], transport=fake_relay.transport)
    assert rc == 0
    cfg = AgentConfig.load()
    assert cfg.agent_token == ""
    assert cfg.agent_id == ""
    assert cfg.is_paired is False
    assert "已清除" in capsys.readouterr().out


def test_unpair_revokes_devices(capsys, fake_relay):
    _save_paired_config()
    fake_relay.devices = [{"id": "dev-1", "name": "Pixel 9", "platform": "android"}]
    rc = main(
        ["unpair", "--yes", "--revoke-devices"], transport=fake_relay.transport
    )
    assert rc == 0
    assert fake_relay.devices == []
    assert "已撤销设备" in capsys.readouterr().out


def test_unpair_when_not_paired(capsys):
    rc = main(["unpair", "--yes"])
    assert rc == 0
    assert "尚未配对" in capsys.readouterr().out


def test_run_once_end_to_end(fake_relay, fake_bilinote, capsys):
    _save_paired_config()
    task = fake_relay.add_task()
    rc = main(
        ["run", "--once"],
        transport=fake_relay.transport,
        bilinote_transport=fake_bilinote.transport,
    )
    assert rc == 0
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"
    out = capsys.readouterr().out
    assert "已处理一个任务" in out


def test_run_once_without_tasks(fake_relay, fake_bilinote, capsys):
    _save_paired_config()
    rc = main(
        ["run", "--once"],
        transport=fake_relay.transport,
        bilinote_transport=fake_bilinote.transport,
    )
    assert rc == 0
    assert "当前没有待处理任务" in capsys.readouterr().out


def test_install_autostart_builds_command(tmp_path, capsys):
    script = tmp_path / "install_autostart.ps1"
    script.write_text("Write-Host ok", encoding="utf-8")
    startup = tmp_path / "Startup"
    args = build_parser().parse_args(
        [
            "install-autostart",
            "--script",
            str(script),
            "--remove",
            "--startup-folder",
            str(startup),
        ]
    )
    captured: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(command):
        captured.append(list(command))
        return Result()

    rc = cmd_install_autostart(args, run_command=fake_run)
    assert rc == 0
    command = captured[0]
    assert command[0] == "powershell"
    assert "-ExecutionPolicy" in command
    assert "-Remove" in command
    assert str(script) in command
    assert str(startup) in command


def test_install_autostart_missing_script(tmp_path):
    args = build_parser().parse_args(
        ["install-autostart", "--script", str(tmp_path / "missing.ps1")]
    )
    assert cmd_install_autostart(args, run_command=lambda command: None) == 2


def test_install_autostart_non_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    args = build_parser().parse_args(["install-autostart", "--script", str(tmp_path / "x.ps1")])
    assert cmd_install_autostart(args, run_command=lambda command: None) == 2

"""pair 命令：创建配对、保存 token、显示配对码、轮询到 claimed/expired。"""

from __future__ import annotations

from linknote_agent.cli import build_parser, cmd_pair, main
from linknote_agent.config import AgentConfig, default_config_path


def _pair_args(*extra: str):
    return build_parser().parse_args(
        ["pair", "--relay", "http://relay.test", *extra]
    )


async def test_pair_no_wait_saves_token_and_code(fake_relay, capsys):
    rc = await cmd_pair(_pair_args("--name", "测试电脑", "--no-wait"), transport=fake_relay.transport)
    assert rc == 0

    out = capsys.readouterr().out
    assert "配对码" in out
    assert "有效期至" in out
    # 跳过了轮询
    assert fake_relay.pairing_poll_count == 0

    cfg = AgentConfig.load()
    assert cfg.agent_token == fake_relay.token
    assert cfg.agent_id == fake_relay.agent["id"]
    assert cfg.agent_name == "测试电脑"
    assert cfg.platform == "windows"
    assert cfg.relay_base_url == "http://relay.test"
    assert cfg.paired_at == ""
    assert cfg.pairing_id
    assert len(cfg.pairing_code.replace("-", "")) == 8
    assert cfg.pairing_expires_at

    # 原子写不留临时文件
    assert [p for p in default_config_path().parent.iterdir() if p.name.endswith(".tmp")] == []


async def test_pair_waits_until_claimed(fake_relay, capsys):
    fake_relay.pairing_claim_after_polls = 2
    rc = await cmd_pair(
        _pair_args("--wait-timeout", "5", "--poll-interval", "0.005"),
        transport=fake_relay.transport,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "配对成功" in out
    assert "Pixel 9" in out

    cfg = AgentConfig.load()
    assert cfg.device_name == "Pixel 9"
    assert cfg.device_platform == "android"
    assert cfg.paired_at


async def test_pair_expired_returns_3(fake_relay, capsys):
    fake_relay.pairing_expire_immediately = True
    rc = await cmd_pair(
        _pair_args("--wait-timeout", "5", "--poll-interval", "0.005"),
        transport=fake_relay.transport,
    )
    assert rc == 3
    assert "过期" in capsys.readouterr().err


async def test_pair_wait_timeout_returns_3(fake_relay, capsys):
    rc = await cmd_pair(
        _pair_args("--wait-timeout", "0.05", "--poll-interval", "0.005"),
        transport=fake_relay.transport,
    )
    assert rc == 3
    assert "等待配对超时" in capsys.readouterr().err


async def test_pair_relay_down_returns_1(fake_relay, capsys):
    fake_relay.down = True
    rc = await cmd_pair(_pair_args("--no-wait"), transport=fake_relay.transport)
    assert rc == 1
    assert "创建配对失败" in capsys.readouterr().err


async def test_pair_preserves_other_settings(fake_relay):
    cfg = AgentConfig()
    cfg.provider_id = "my-provider"
    cfg.model_name = "my-model"
    cfg.quality = "slow"
    cfg.agent_name = "旧名字"
    cfg.save()

    rc = await cmd_pair(_pair_args("--name", "新名字", "--no-wait"), transport=fake_relay.transport)
    assert rc == 0

    reloaded = AgentConfig.load()
    assert reloaded.provider_id == "my-provider"
    assert reloaded.model_name == "my-model"
    assert reloaded.quality == "slow"
    assert reloaded.agent_name == "新名字"


def test_main_pair_integration(fake_relay):
    rc = main(
        ["pair", "--relay", "http://relay.test", "--no-wait"],
        transport=fake_relay.transport,
    )
    assert rc == 0
    assert AgentConfig.load().agent_token == fake_relay.token


def test_main_pair_with_invalid_relay_returns_1():
    rc = main(["pair", "--relay", "not-a-url"], transport=None)
    assert rc == 1

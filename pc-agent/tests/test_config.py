"""配置读写：默认值、原子写、环境变量、容错。"""

from __future__ import annotations

import json
import os

import pytest

from linknote_agent.config import (
    AgentConfig,
    ConfigError,
    NotPairedError,
    default_config_path,
    default_home,
    load_config,
)


def test_defaults_and_paths(isolated_home):
    cfg = AgentConfig.load()
    assert default_home() == isolated_home
    assert default_config_path() == isolated_home / "agent.json"
    assert cfg.relay_base_url.startswith("http")
    assert cfg.is_paired is False
    assert cfg.provider_id == "deepseek"
    assert cfg.style == "minimal"
    assert cfg.backend_base_url == "http://127.0.0.1:8483/api"


def test_save_and_load_roundtrip(isolated_home):
    cfg = AgentConfig()
    cfg.agent_token = "agt_secret_token"
    cfg.agent_id = "agent-1"
    cfg.relay_base_url = "http://127.0.0.1:8787"
    cfg.provider_id = "my-provider"
    cfg.quality = "medium"
    cfg.screenshot = True
    path = cfg.save()

    assert path == default_config_path()
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["agent_token"] == "agt_secret_token"
    assert "extra" not in raw

    loaded = AgentConfig.load()
    assert loaded.agent_token == "agt_secret_token"
    assert loaded.agent_id == "agent-1"
    assert loaded.provider_id == "my-provider"
    assert loaded.quality == "medium"
    assert loaded.screenshot is True
    assert loaded.is_paired is True


def test_atomic_write_leaves_no_temp_files(isolated_home):
    cfg = AgentConfig()
    for i in range(3):
        cfg.agent_name = f"电脑{i}"
        cfg.save()
    leftovers = [p.name for p in isolated_home.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert [p for p in isolated_home.iterdir() if p.name.startswith(".agent.json")] == []


def test_unknown_fields_are_preserved(isolated_home):
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"agent_token": "agt_x", "future_field": {"a": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg = AgentConfig.load()
    assert cfg.agent_token == "agt_x"
    cfg.agent_name = "新名字"
    cfg.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["future_field"] == {"a": 1}
    assert raw["agent_name"] == "新名字"


def test_invalid_json_raises_config_error(isolated_home):
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        AgentConfig.load()


def test_wrong_type_raises_config_error(isolated_home):
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"screenshot": "definitely"}), encoding="utf-8")
    # bool 的宽松转换会把任意字符串转成 True/False，不报错；int 字段才报错
    path.write_text(json.dumps({"max_status_errors": "not-an-int"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        AgentConfig.load()


def test_type_coercion(isolated_home):
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "screenshot": "true",
                "insert_video_links": "no",
                "task_timeout_seconds": "120",
                "agent_token": None,
            }
        ),
        encoding="utf-8",
    )
    cfg = AgentConfig.load()
    assert cfg.screenshot is True
    assert cfg.insert_video_links is False
    assert cfg.task_timeout_seconds == 120.0
    assert cfg.agent_token == ""


def test_env_overrides(isolated_home, monkeypatch):
    monkeypatch.setenv("LINKNOTE_AGENT_TOKEN", "agt_env_token")
    monkeypatch.setenv("LINKNOTE_RELAY_BASE_URL", "http://env-relay:8787")
    monkeypatch.setenv("LINKNOTE_SCREENSHOT", "1")
    cfg = load_config()
    assert cfg.agent_token == "agt_env_token"
    assert cfg.relay_base_url == "http://env-relay:8787"
    assert cfg.screenshot is True


def test_masked_token(isolated_home):
    cfg = AgentConfig()
    assert cfg.masked_token == "（未配对）"
    cfg.agent_token = "agt_" + "a" * 43
    masked = cfg.masked_token
    assert masked.startswith("agt_aaaa")
    assert masked.endswith("aaaa")
    assert "a" * 43 not in masked
    assert "agt_" + "a" * 43 not in cfg.to_dict(include_token=False)["agent_token"]


def test_require_paired_message():
    cfg = AgentConfig()
    with pytest.raises(NotPairedError) as excinfo:
        cfg.require_paired()
    assert "pair" in str(excinfo.value)
    assert cfg.is_paired is False


def test_config_path_override(isolated_home, tmp_path):
    custom = tmp_path / "custom" / "me.json"
    cfg = AgentConfig()
    cfg.agent_name = "自定义路径"
    cfg.save(custom)
    assert custom.exists()
    loaded = AgentConfig.load(custom)
    assert loaded.agent_name == "自定义路径"


def test_file_is_utf8_without_bom(isolated_home):
    cfg = AgentConfig()
    cfg.agent_name = "中文名字"
    path = cfg.save()
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "中文名字" in raw.decode("utf-8")
    if os.name != "nt":  # POSIX 上应该收紧到 0600
        assert (os.stat(path).st_mode & 0o777) == 0o600

# ================================================================ WebSocket 配置（task-8）


def test_ws_defaults(isolated_home):
    cfg = AgentConfig.load()
    assert cfg.use_websocket is True
    assert cfg.ws_url == ""
    assert cfg.ws_ping_interval_seconds == 25.0
    assert cfg.ws_dead_timeout_seconds == 60.0
    assert cfg.ws_reconnect_delays == [1.0, 2.0, 4.0, 8.0, 15.0, 30.0]
    assert cfg.ws_jitter_ratio == 0.2
    assert cfg.rest_fallback is True
    assert cfg.note_retention_days == 7.0


def test_ws_reconnect_delays_coercion(isolated_home):
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ws_reconnect_delays": "1,2,4,8"}), encoding="utf-8")
    cfg = AgentConfig.load()
    assert cfg.ws_reconnect_delays == [1.0, 2.0, 4.0, 8.0]

    path.write_text(json.dumps({"ws_reconnect_delays": [2, 4, 8]}), encoding="utf-8")
    cfg = AgentConfig.load()
    assert cfg.ws_reconnect_delays == [2.0, 4.0, 8.0]


def test_ws_env_overrides(isolated_home, monkeypatch):
    monkeypatch.setenv("LINKNOTE_WS_RECONNECT_DELAYS", "3,6")
    monkeypatch.setenv("LINKNOTE_USE_WEBSOCKET", "0")
    monkeypatch.setenv("LINKNOTE_WS_PING_INTERVAL_SECONDS", "15")
    cfg = AgentConfig.load()
    assert cfg.ws_reconnect_delays == [3.0, 6.0]
    assert cfg.use_websocket is False
    assert cfg.ws_ping_interval_seconds == 15.0

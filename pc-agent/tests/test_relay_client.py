"""Relay 客户端：认证头、配对、任务、错误映射。"""

from __future__ import annotations

import httpx
import pytest

from linknote_agent.relay_client import (
    MARKDOWN_MAX_BYTES,
    RelayClient,
    RelayError,
    _normalize_base_url,
)
AGENT_TOKEN = "agt_test_token_0123456789"
RELAY_BASE = "http://relay.test"


async def test_health(fake_relay, relay_client):
    data = await relay_client.health()
    assert data["status"] == "ok"


async def test_create_pairing_returns_token_and_code(fake_relay):
    client = RelayClient(RELAY_BASE, "", transport=fake_relay.transport)
    try:
        data = await client.create_pairing("测试电脑", "windows", "0.1.0")
    finally:
        await client.aclose()
    assert data["agent_token"].startswith("agt_")
    assert data["pairing_id"]
    assert data["agent_id"]
    code = data["code"]
    assert len(code.replace("-", "")) == 8
    # 创建配对是公开端点，不应带 Authorization
    create_request = fake_relay.calls("POST", "/api/v1/pairings")[0]
    assert create_request["authorization"] == ""
    assert create_request["json"]["agent_name"] == "测试电脑"


async def test_set_token_adds_bearer_header(fake_relay):
    client = RelayClient(RELAY_BASE, "", transport=fake_relay.transport)
    try:
        pairing = await client.create_pairing()
        client.set_token(pairing["agent_token"])
        me = await client.me()
    finally:
        await client.aclose()
    assert me["agent"]["id"] == fake_relay.agent["id"]
    me_request = fake_relay.calls("GET", "/api/v1/agent/me")[0]
    assert me_request["authorization"] == f"Bearer {pairing['agent_token']}"


async def test_pairing_poll_claimed(fake_relay):
    fake_relay.pairing_claim_after_polls = 1
    client = RelayClient(RELAY_BASE, "", transport=fake_relay.transport)
    try:
        pairing = await client.create_pairing()
        client.set_token(pairing["agent_token"])
        status = await client.get_pairing(pairing["pairing_id"])
    finally:
        await client.aclose()
    assert status["status"] == "claimed"
    assert status["device"]["name"] == "Pixel 9"


async def test_unauthorized_maps_to_actionable_message(fake_relay):
    client = RelayClient(RELAY_BASE, "agt_wrong_token", transport=fake_relay.transport)
    try:
        with pytest.raises(RelayError) as excinfo:
            await client.me()
    finally:
        await client.aclose()
    exc = excinfo.value
    assert exc.status_code == 401
    assert exc.is_auth_error is True
    assert exc.retriable is False
    assert "pair" in exc.message
    assert "配对" in exc.message


async def test_server_error_is_retriable(fake_relay):
    fake_relay.next_task_fail_times = 1
    with pytest.raises(RelayError) as excinfo:
        await relay_client_probe(fake_relay)
    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.retriable is True
    assert "服务端异常" in exc.message


async def relay_client_probe(fake_relay):
    client = RelayClient(RELAY_BASE, AGENT_TOKEN, transport=fake_relay.transport)
    try:
        return await client.next_task()
    finally:
        await client.aclose()


async def test_rate_limited_is_retriable():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429, json={"ok": False, "error": {"code": "rate_limited", "message": ""}}
        )
    )
    client = RelayClient(RELAY_BASE, AGENT_TOKEN, transport=transport)
    try:
        with pytest.raises(RelayError) as excinfo:
            await client.health()
    finally:
        await client.aclose()
    assert excinfo.value.retriable is True
    assert "频繁" in excinfo.value.message


async def test_404_task_not_found_not_retriable(fake_relay):
    with pytest.raises(RelayError) as excinfo:
        await relay_client_get_missing(fake_relay)
    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "task_not_found"
    assert excinfo.value.retriable is False


async def relay_client_get_missing(fake_relay):
    client = RelayClient(RELAY_BASE, AGENT_TOKEN, transport=fake_relay.transport)
    try:
        return await client.get_task("missing-task")
    finally:
        await client.aclose()


async def test_network_error_is_retriable(fake_relay):
    fake_relay.down = True
    with pytest.raises(RelayError) as excinfo:
        await relay_client_next(fake_relay)
    assert excinfo.value.code == "network_error"
    assert excinfo.value.retriable is True
    assert "无法连接 Relay" in excinfo.value.message


async def relay_client_next(fake_relay):
    client = RelayClient(RELAY_BASE, AGENT_TOKEN, transport=fake_relay.transport)
    try:
        return await client.next_task()
    finally:
        await client.aclose()


async def test_non_json_response():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))
    client = RelayClient(RELAY_BASE, AGENT_TOKEN, transport=transport)
    try:
        with pytest.raises(RelayError) as excinfo:
            await client.health()
    finally:
        await client.aclose()
    assert excinfo.value.code == "bad_response"


async def test_next_task_null(fake_relay):
    assert await relay_client_next(fake_relay) is None


async def test_next_task_returns_task(fake_relay):
    task = fake_relay.add_task()
    claimed = await relay_client_next(fake_relay)
    assert claimed is not None
    assert claimed["id"] == task["id"]
    assert fake_relay.tasks[task["id"]]["status"] == "running"


async def test_progress_rejects_illegal_stage(relay_client):
    with pytest.raises(ValueError):
        await relay_client.progress("t1", "queued")


async def test_progress_and_contract_stage(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    await relay_client.progress(task["id"], "transcribing", 45, "正在语音转写")
    assert fake_relay.progress_calls[-1]["stage"] == "transcribing"
    assert fake_relay.progress_calls[-1]["progress"] == 45
    assert fake_relay.tasks[task["id"]]["stage"] == "transcribing"


async def test_progress_message_truncated_to_200(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    await relay_client.progress(task["id"], "parsing", 1, "长" * 500)
    assert len(fake_relay.progress_calls[-1]["message"]) == 200


async def test_heartbeat_and_busy_flag(relay_client, fake_relay):
    await relay_client.heartbeat("0.1.0", busy=True)
    assert fake_relay.heartbeat_calls[-1] == {"version": "0.1.0", "busy": True}


async def test_lease_heartbeat(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    data = await relay_client.task_heartbeat(task["id"])
    assert "lease_expires_at" in data
    assert fake_relay.lease_calls[-1]["task_id"] == task["id"]


async def test_complete_payload(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    await relay_client.complete(
        task["id"],
        title="视频标题",
        markdown="# 笔记",
        duration_seconds=754,
        meta={"platform": "bilibili"},
    )
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"
    assert fake_relay.complete_calls[-1]["title"] == "视频标题"
    assert fake_relay.complete_calls[-1]["duration_seconds"] == 754
    assert fake_relay.complete_calls[-1]["meta"] == {"platform": "bilibili"}


async def test_complete_is_idempotent(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    await relay_client.complete(task["id"], title="t", markdown="m")
    await relay_client.complete(task["id"], title="t", markdown="m")
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"


async def test_complete_truncates_title(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    await relay_client.complete(task["id"], title="标" * 300, markdown="正文")
    assert len(fake_relay.complete_calls[-1]["title"]) == 200


async def test_complete_rejects_empty_markdown(relay_client, fake_relay):
    with pytest.raises(RelayError) as excinfo:
        await relay_client.complete("t1", title="t", markdown="   ")
    assert excinfo.value.code == "empty_markdown"
    assert not fake_relay.complete_calls


async def test_complete_rejects_oversized_markdown(relay_client, fake_relay):
    with pytest.raises(RelayError) as excinfo:
        await relay_client.complete("t1", title="t", markdown="a" * (MARKDOWN_MAX_BYTES + 1))
    assert excinfo.value.code == "markdown_too_large"
    assert excinfo.value.status_code == 413


async def test_fail_payload_truncated(relay_client, fake_relay):
    task = fake_relay.add_task()
    await relay_client.next_task()
    await relay_client.fail(task["id"], "bilinote_error", "错" * 3000)
    call = fake_relay.fail_calls[-1]
    assert len(call["message"]) <= 1000
    assert fake_relay.tasks[task["id"]]["status"] == "failed"


async def test_me_includes_devices(relay_client, fake_relay):
    fake_relay.devices.append(
        {"id": "dev-1", "name": "Pixel 9", "platform": "android", "paired_at": "x"}
    )
    data = await relay_client.me()
    assert data["devices"][0]["name"] == "Pixel 9"
    assert data["queue"] == {"queued": 0, "running": 0}


async def test_revoke_device(relay_client, fake_relay):
    fake_relay.devices.append({"id": "dev-1", "name": "Pixel 9", "platform": "android"})
    await relay_client.revoke_device("dev-1")
    assert fake_relay.devices == []


def test_normalize_base_url():
    assert _normalize_base_url("http://127.0.0.1:8787/") == "http://127.0.0.1:8787"
    assert _normalize_base_url("https://relay.example.com/api/v1") == "https://relay.example.com"
    assert _normalize_base_url("https://relay.example.com/api") == "https://relay.example.com"
    with pytest.raises(RelayError):
        _normalize_base_url("relay.example.com")
    with pytest.raises(RelayError):
        _normalize_base_url("")

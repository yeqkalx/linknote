"""BiliNote 客户端：ResponseWrapper、/generate_note 字段、状态查询。"""

from __future__ import annotations

import httpx
import pytest

from linknote_agent.bilinote_client import BiliNoteClient, BiliNoteError

BILINOTE_BASE = "http://bilinote.test/api"


async def test_is_alive(bilinote_client, fake_bilinote):
    assert await bilinote_client.is_alive() is True
    fake_bilinote.alive = False
    assert await bilinote_client.is_alive() is False


async def test_generate_note_body_matches_wecom_bot(bilinote_client, fake_bilinote):
    task_id = await bilinote_client.generate_note(
        "https://www.bilibili.com/video/BV1xx",
        "bilibili",
        provider_id="deepseek",
        model_name="deepseek-v4-flash",
        quality="fast",
        style="minimal",
        screenshot=False,
        link=True,
    )
    assert task_id == "bilinote-task-1"
    body = fake_bilinote.generate_bodies[0]
    assert body["video_url"] == "https://www.bilibili.com/video/BV1xx"
    assert body["platform"] == "bilibili"
    assert body["provider_id"] == "deepseek"
    assert body["model_name"] == "deepseek-v4-flash"
    assert body["quality"] == "fast"
    assert body["style"] == "minimal"  # 必须是字符串，显式 null 后端会 422
    assert body["screenshot"] is False
    assert body["link"] is True
    assert body["format"] == []
    assert body["video_understanding"] is False
    assert body["video_interval"] == 0
    assert body["grid_size"] == []
    assert "task_id" not in body


async def test_generate_note_reuse_task_id(bilinote_client, fake_bilinote):
    await bilinote_client.generate_note(
        "https://youtu.be/abc",
        "youtube",
        provider_id="p",
        model_name="m",
        reuse_task_id="old-task",
    )
    assert fake_bilinote.generate_bodies[0]["task_id"] == "old-task"


async def test_generate_note_backend_error(bilinote_client, fake_bilinote):
    fake_bilinote.generate_error = ("401 invalid api key", 500)
    with pytest.raises(BiliNoteError) as excinfo:
        await bilinote_client.generate_note(
            "https://www.bilibili.com/video/BV1xx",
            "bilibili",
            provider_id="p",
            model_name="m",
        )
    assert "invalid api key" in excinfo.value.message


async def test_generate_note_missing_task_id(bilinote_client, fake_bilinote):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})
    )
    client = BiliNoteClient(BILINOTE_BASE, transport=transport)
    try:
        with pytest.raises(BiliNoteError) as excinfo:
            await client.generate_note(
                "https://www.bilibili.com/video/BV1xx",
                "bilibili",
                provider_id="p",
                model_name="m",
            )
    finally:
        await client.aclose()
    assert "task_id" in excinfo.value.message


async def test_task_status_pending(bilinote_client, fake_bilinote):
    task_id = await bilinote_client.generate_note(
        "https://www.bilibili.com/video/BV1xx", "bilibili", provider_id="p", model_name="m"
    )
    status = await bilinote_client.get_task_status(task_id)
    assert status["status"] == "PENDING"
    assert status["result"] is None


async def test_task_status_success_with_result(bilinote_client, fake_bilinote):
    fake_bilinote.next_script = ["SUCCESS"]
    task_id = await bilinote_client.generate_note(
        "https://www.bilibili.com/video/BV1xx", "bilibili", provider_id="p", model_name="m"
    )
    status = await bilinote_client.get_task_status(task_id)
    assert status["status"] == "SUCCESS"
    assert status["result"]["markdown"].startswith("# 测试笔记")
    assert status["result"]["audio_meta"]["title"] == "测试视频标题"


async def test_task_status_failed_code_500_becomes_structured(bilinote_client, fake_bilinote):
    fake_bilinote.failure_message = "模型余额不足"
    fake_bilinote.next_script = ["FAILED"]
    task_id = await bilinote_client.generate_note(
        "https://www.bilibili.com/video/BV1xx", "bilibili", provider_id="p", model_name="m"
    )
    status = await bilinote_client.get_task_status(task_id)
    assert status["status"] == "FAILED"
    assert status["message"] == "模型余额不足"
    assert status["result"] is None


async def test_task_status_http_500_raises(bilinote_client, fake_bilinote):
    task_id = await bilinote_client.generate_note(
        "https://www.bilibili.com/video/BV1xx", "bilibili", provider_id="p", model_name="m"
    )
    fake_bilinote.status_500_remaining = 1
    with pytest.raises(BiliNoteError) as excinfo:
        await bilinote_client.get_task_status(task_id)
    assert "500" in str(excinfo.value)


async def test_non_json_response_raises():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>not json</html>")
    )
    client = BiliNoteClient(BILINOTE_BASE, transport=transport)
    try:
        with pytest.raises(BiliNoteError) as excinfo:
            await client.generate_note(
                "https://www.bilibili.com/video/BV1xx", "bilibili", provider_id="p", model_name="m"
            )
    finally:
        await client.aclose()
    assert "非 JSON" in excinfo.value.message


async def test_unreachable_backend_raises():
    def handler(request):
        raise httpx.ConnectError("拒绝连接", request=request)

    client = BiliNoteClient(BILINOTE_BASE, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BiliNoteError) as excinfo:
            await client.generate_note(
                "https://www.bilibili.com/video/BV1xx", "bilibili", provider_id="p", model_name="m"
            )
    finally:
        await client.aclose()
    assert "无法连接 BiliNote 后端" in excinfo.value.message

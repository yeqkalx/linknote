"""Runner：任务生命周期、进度映射/节流、租约心跳、失败翻译、outbox 补传。"""

from __future__ import annotations

import asyncio

CONTRACT_STAGES = {
    "parsing",
    "downloading",
    "transcribing",
    "summarizing",
    "formatting",
    "saving",
}


async def test_success_lifecycle(make_runner, fake_relay, fake_bilinote):
    task = fake_relay.add_task()
    runner = make_runner()
    assert await runner.poll_once() is True

    state = fake_relay.tasks[task["id"]]
    assert state["status"] == "succeeded"
    assert state["progress"] == 100
    assert state["stage"] == "done"

    assert len(fake_relay.complete_calls) == 1
    complete = fake_relay.complete_calls[0]
    assert complete["title"] == "测试视频标题"
    assert "# 测试笔记" in complete["markdown"]
    assert complete["duration_seconds"] == 754
    assert complete["meta"]["platform"] == "bilibili"
    assert complete["meta"]["url"] == task["url"]
    assert fake_relay.fail_calls == []

    # BiliNote 提交参数与配置一致
    body = fake_bilinote.generate_bodies[0]
    assert body["video_url"] == task["url"]
    assert body["platform"] == "bilibili"
    assert body["style"] == "minimal"
    assert body["screenshot"] is False
    assert body["link"] is True


async def test_progress_stages_follow_backend_status(make_runner, fake_relay):
    fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()

    stages = [call["stage"] for call in fake_relay.progress_calls]
    assert set(stages) <= CONTRACT_STAGES
    # 状态变化时立刻回传：初始 2 条 + PENDING/PARSING/DOWNLOADING/TRANSCRIBING/SUMMARIZING
    assert stages == [
        "parsing",
        "parsing",
        "parsing",
        "parsing",
        "downloading",
        "transcribing",
        "summarizing",
    ]
    assert all(0 <= call["progress"] <= 100 for call in fake_relay.progress_calls)
    assert all(len(call["message"]) <= 200 for call in fake_relay.progress_calls)


async def test_progress_throttled_for_repeated_status(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["TRANSCRIBING"] * 20 + ["SUCCESS"]
    fake_relay.add_task()
    runner = make_runner(
        task_status_poll_interval_seconds=0.01, progress_min_interval_seconds=0.03
    )
    await runner.poll_once()

    transcribing = [c for c in fake_relay.progress_calls if c["stage"] == "transcribing"]
    # 状态没变化时不能每条都发：20 次轮询至少会合并掉一部分
    assert 1 <= len(transcribing) < 20
    # 同状态两次回传间隔不小于配置的 30ms（留 50% 容差应对事件循环调度抖动）
    gaps = [
        later["time"] - earlier["time"]
        for earlier, later in zip(transcribing, transcribing[1:])
    ]
    assert all(gap >= 0.03 * 0.5 for gap in gaps), gaps


async def test_lease_heartbeat_during_long_task(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["TRANSCRIBING"] * 25 + ["SUCCESS"]
    task = fake_relay.add_task()
    runner = make_runner(
        task_status_poll_interval_seconds=0.005, lease_heartbeat_interval_seconds=0.02
    )
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"
    assert len(fake_relay.lease_calls) >= 1
    assert all(call["task_id"] == task["id"] for call in fake_relay.lease_calls)


async def test_agent_heartbeat_busy_flag(make_runner, fake_relay):
    runner = make_runner(heartbeat_interval_seconds=0.02)
    stop = asyncio.Event()
    task_handle = asyncio.create_task(runner.run_forever(stop))
    await asyncio.sleep(0.12)
    stop.set()
    await asyncio.wait_for(task_handle, timeout=3)
    assert len(fake_relay.heartbeat_calls) >= 1
    assert fake_relay.heartbeat_calls[0]["version"] == runner.config.version
    assert fake_relay.heartbeat_calls[0]["busy"] is False


async def test_run_forever_stops_cleanly(make_runner, fake_relay):
    runner = make_runner(poll_interval_seconds=0.01)
    stop = asyncio.Event()
    handle = asyncio.create_task(runner.run_forever(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(handle, timeout=3)
    assert handle.done()


async def test_failed_task_translated_to_chinese(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["PENDING", "FAILED"]
    fake_bilinote.failure_message = "401 invalid api key"
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()

    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    fail = fake_relay.fail_calls[0]
    assert fail["code"] == "model_auth_error"
    assert "API Key" in fail["message"]
    assert "401 invalid api key" in fail["message"]
    assert len(fail["message"]) <= 1000


async def test_quota_failure_translated(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["FAILED"]
    fake_bilinote.failure_message = "模型账户余额不足"
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    assert fake_relay.fail_calls[0]["code"] == "quota_exceeded"
    assert "余额" in fake_relay.fail_calls[0]["message"]
    assert fake_relay.tasks[task["id"]]["status"] == "failed"


async def test_generate_note_error_translated(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.generate_error = ("模型账户余额不足", 500)
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    assert fake_relay.fail_calls[0]["code"] == "quota_exceeded"
    assert "余额" in fake_relay.fail_calls[0]["message"]


async def test_complete_goes_to_outbox_when_relay_glitch(make_runner, fake_relay):
    fake_relay.complete_fail_times = 1
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()

    # complete 500 → 笔记落 outbox，任务在 Relay 侧仍 running
    entries = runner.outbox.pending()
    assert len(entries) == 1
    assert entries[0].task_id == task["id"]
    assert entries[0].kind == "complete"
    assert fake_relay.tasks[task["id"]]["status"] == "running"

    # 下一轮循环先补传 outbox，再领任务
    assert await runner.poll_once() is False
    assert runner.outbox.pending() == []
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"
    assert "# 测试笔记" in fake_relay.tasks[task["id"]]["markdown"]


async def test_outbox_survives_process_restart(make_runner, fake_relay):
    fake_relay.complete_fail_times = 1
    task = fake_relay.add_task()
    first = make_runner()
    await first.poll_once()
    assert len(first.outbox.pending()) == 1

    # 新 Runner（模拟进程重启）先补传再领任务
    second = make_runner()
    assert await second.poll_once() is False
    assert second.outbox.pending() == []
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"


async def test_fail_goes_to_outbox_when_relay_glitch(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["FAILED"]
    fake_bilinote.failure_message = "暂不支持该平台"
    fake_relay.fail_fail_times = 1
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    entries = runner.outbox.pending()
    assert len(entries) == 1
    assert entries[0].kind == "fail"
    assert fake_relay.tasks[task["id"]]["status"] == "running"

    await runner.poll_once()
    assert runner.outbox.pending() == []
    assert fake_relay.tasks[task["id"]]["status"] == "failed"


async def test_backend_unavailable_fails_task(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.alive = False
    task = fake_relay.add_task()
    runner = make_runner(auto_start_backend=False)
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    fail = fake_relay.fail_calls[0]
    assert fail["code"] == "backend_unavailable"
    assert "BiliNote 后端" in fail["message"]


async def test_unsupported_url_rejected(make_runner, fake_relay):
    task = fake_relay.add_task(url="https://example.com/video/1", platform="bilibili")
    runner = make_runner()
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    assert fake_relay.fail_calls[0]["code"] == "unsupported_platform"
    assert "只支持" in fake_relay.fail_calls[0]["message"]


async def test_task_lost_when_progress_404(make_runner, fake_relay):
    fake_relay.progress_fail_times = 50
    fake_relay.progress_fail_status = 404
    fake_relay.progress_fail_code = "task_not_found"
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    # 任务被 Relay 回收：不再继续处理，也不能误回 fail
    assert fake_relay.fail_calls == []
    assert fake_relay.complete_calls == []
    assert fake_relay.tasks[task["id"]]["status"] == "running"


async def test_progress_retriable_error_does_not_abort(make_runner, fake_relay):
    fake_relay.progress_fail_times = 1
    fake_relay.progress_fail_status = 500
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"
    assert fake_relay.fail_calls == []


async def test_status_errors_eventually_fail_backend_unavailable(
    make_runner, fake_relay, fake_bilinote
):
    fake_bilinote.status_500_remaining = 10
    task = fake_relay.add_task()
    runner = make_runner(max_status_errors=3, task_status_poll_interval_seconds=0.005)
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    fail = fake_relay.fail_calls[0]
    assert fail["code"] == "backend_unavailable"
    assert "连续失败" in fail["message"]


async def test_task_timeout(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["PENDING"] * 100
    task = fake_relay.add_task()
    runner = make_runner(task_timeout_seconds=0.02, task_status_poll_interval_seconds=0.01)
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    assert fake_relay.fail_calls[0]["code"] == "task_timeout"
    assert "超时" in fake_relay.fail_calls[0]["message"]


async def test_empty_markdown_fails_task(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["SUCCESS"]
    fake_bilinote.next_result = {"markdown": "  ", "audio_meta": {"title": "空"}}
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    assert fake_relay.fail_calls[0]["code"] == "empty_note"


async def test_missing_title_falls_back(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["SUCCESS"]
    fake_bilinote.next_result = {"markdown": "# 只有正文"}
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    complete = fake_relay.complete_calls[0]
    assert complete["title"] == "未命名笔记"
    assert "duration_seconds" not in complete


async def test_unknown_status_does_not_break_task(make_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["PENDING", "SOMETHING_NEW", "TRANSCRIBING", "SUCCESS"]
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"


async def test_wrong_relay_platform_uses_detected( make_runner, fake_relay, fake_bilinote):
    fake_relay.add_task(url="https://youtu.be/abc", platform="bilibili")
    runner = make_runner()
    await runner.poll_once()
    assert fake_bilinote.generate_bodies[0]["platform"] == "youtube"


async def test_oversized_markdown_saved_locally_and_failed(
    make_runner, fake_relay, fake_bilinote
):
    from linknote_agent.relay_client import MARKDOWN_MAX_BYTES

    fake_bilinote.next_script = ["SUCCESS"]
    big_markdown = "a" * (MARKDOWN_MAX_BYTES + 10)
    fake_bilinote.next_result = {"markdown": big_markdown, "audio_meta": {"title": "超大笔记"}}
    task = fake_relay.add_task()
    runner = make_runner()
    await runner.poll_once()

    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    assert fake_relay.fail_calls[0]["code"] == "note_too_large"
    assert "1.5 MB" in fake_relay.fail_calls[0]["message"]
    saved = runner.outbox.notes_dir / f"{task['id']}.md"
    assert saved.exists()
    assert saved.stat().st_size > MARKDOWN_MAX_BYTES

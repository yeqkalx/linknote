"""WS 推送版 Runner 的单测：消息分发、任务更新、task_note、note_request/ack、重连补发、
REST 兜底、旧 outbox 迁移。用 fake WS client + fake Relay/BiliNote（都不连真实网络）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from linknote_agent.backend import BackendSupervisor
from linknote_agent.outbox import Outbox
from linknote_agent.ws_runner import RealtimeAgentRunner

CONTRACT_STAGES = {
    "parsing",
    "downloading",
    "transcribing",
    "summarizing",
    "formatting",
    "saving",
}


class FakeWSClient:
    """最小 RealtimeClient 替身：手工 push 事件、记录 send。"""

    def __init__(self) -> None:
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.connected = False
        self.stop_called = False
        self.url = "ws://fake-relay/api/v1/agent/ws"

    async def run(self) -> None:
        self.connected = True
        try:
            await asyncio.Event().wait()
        finally:
            self.connected = False

    async def stop(self) -> None:
        self.stop_called = True
        self.connected = False

    async def send(self, payload: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        self.sent.append(payload)
        return True

    # 测试辅助
    def push(self, message: dict[str, Any]) -> None:
        self.events.put_nowait(message)

    def typed(self, msg_type: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == msg_type]


def _task(url: str = "https://www.bilibili.com/video/BV1xx411c7mD") -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "url": url,
        "status": "queued",
        "platform": "bilibili",
        "title": None,
        "created_at": "2026-10-24T08:00:00.000Z",
    }


async def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("等待条件超时")


@pytest.fixture
def make_ws_runner(make_config, relay_client, bilinote_client):
    def _make(**overrides):
        cfg = make_config(**overrides)
        ws = FakeWSClient()
        backend = BackendSupervisor(cfg, bilinote=bilinote_client)
        outbox = Outbox(cfg.resolved_outbox_dir(), notes_dir=cfg.resolved_notes_dir())
        runner = RealtimeAgentRunner(
            cfg, relay_client, bilinote_client, backend=backend, outbox=outbox, ws_client=ws
        )
        return runner, ws

    return _make


# ================================================================ 消息分发


async def test_hello_and_task_message_enqueue(make_ws_runner, fake_relay):
    runner, ws = make_ws_runner()
    ws.connected = True
    await runner.handle_ws_message({"type": "hello", "agent_id": "a1", "server_time": "t"})
    assert runner._hello_seen is True

    task = _task()
    await runner.handle_ws_message({"type": "task", "task": task})
    assert runner._queue.qsize() == 1

    # 重复推送同一任务不重复入队
    await runner.handle_ws_message({"type": "task", "task": task})
    assert runner._queue.qsize() == 1


async def test_unknown_message_type_is_ignored(make_ws_runner):
    runner, ws = make_ws_runner()
    ws.connected = True
    await runner.handle_ws_message({"type": "something_new"})
    await runner.handle_ws_message({"type": "ping", "ts": 1})
    assert runner._queue.qsize() == 0


async def test_auth_error_event_is_logged(make_ws_runner, caplog):
    runner, ws = make_ws_runner()
    import logging

    with caplog.at_level(logging.ERROR, logger="linknote_agent.ws_runner"):
        await runner.handle_ws_message(
            {"type": "_auth_error", "message": "配对已失效或未配对（HTTP 401）"}
        )
    assert any("重新运行 pair" in record.message for record in caplog.records)


# ================================================================ 任务生命周期


async def test_process_task_success_sends_updates_and_note(make_ws_runner, fake_relay, fake_bilinote):
    runner, ws = make_ws_runner()
    ws.connected = True
    task = _task()
    await runner.process_task(task)

    # 1) 进度/task_update：running + 契约 stage
    updates = ws.typed("task_update")
    assert updates, "应发送 task_update"
    assert updates[0]["status"] == "running"
    stages = {u.get("stage") for u in updates if u.get("stage")}
    assert stages <= CONTRACT_STAGES
    assert "transcribing" in stages and "summarizing" in stages
    assert all(0 <= u.get("progress", 0) <= 100 for u in updates)

    # 2) 完成更新 + task_note（先 completed 再 note）
    completed = [u for u in updates if u.get("status") == "completed"]
    assert completed and completed[0]["progress"] == 100
    notes = ws.typed("task_note")
    assert len(notes) == 1
    assert notes[0]["task_id"] == task["id"]
    assert "# 测试笔记" in notes[0]["markdown"]
    assert notes[0]["title"] == "测试视频标题"
    completed_index = ws.sent.index(completed[0])
    note_index = ws.sent.index(notes[0])
    assert completed_index < note_index

    # 3) 笔记只落本地，不走 REST /complete（不写 D1）
    assert fake_relay.complete_calls == []
    assert fake_relay.progress_calls == []
    assert fake_relay.fail_calls == []
    pending = runner.outbox.pending_notes()
    assert len(pending) == 1
    assert pending[0].task_id == task["id"]
    assert pending[0].payload["markdown"].startswith("# 测试笔记")
    assert (runner.outbox.notes_dir / f"{task['id']}.md").exists()

    # 4) BiliNote 提交参数
    body = fake_bilinote.generate_bodies[0]
    assert body["video_url"] == task["url"]
    assert body["platform"] == "bilibili"


async def test_process_task_failure_sends_failed_update(make_ws_runner, fake_relay, fake_bilinote):
    fake_bilinote.next_script = ["PENDING", "FAILED"]
    fake_bilinote.failure_message = "401 invalid api key"
    runner, ws = make_ws_runner()
    ws.connected = True
    await runner.process_task(_task())

    failed = [u for u in ws.typed("task_update") if u.get("status") == "failed"]
    assert failed
    assert "API Key" in failed[0]["error"]
    # 契约 §11.2 增量字段 error_code
    assert failed[0]["error_code"] == "model_auth_error"
    assert ws.typed("task_note") == []
    assert runner.outbox.pending_notes() == []
    assert fake_relay.fail_calls == []  # 不走 REST /fail


async def test_process_task_skips_generation_if_note_already_local(
    make_ws_runner, fake_bilinote
):
    runner, ws = make_ws_runner()
    ws.connected = True
    task = _task()
    runner.outbox.add_note(task["id"], "旧标题", "# 本地已存在的笔记")

    await runner.process_task(task)

    assert fake_bilinote.generate_bodies == []  # 不重复调用 BiliNote
    assert [u.get("status") for u in ws.typed("task_update")] == ["completed"]
    assert ws.typed("task_note")[0]["markdown"] == "# 本地已存在的笔记"


async def test_oversized_markdown_fails_without_pending_note(
    make_ws_runner, fake_bilinote
):
    from linknote_agent.relay_client import MARKDOWN_MAX_BYTES

    fake_bilinote.next_script = ["SUCCESS"]
    fake_bilinote.next_result = {
        "markdown": "a" * (MARKDOWN_MAX_BYTES + 10),
        "audio_meta": {"title": "超大笔记"},
    }
    runner, ws = make_ws_runner()
    ws.connected = True
    task = _task()
    await runner.process_task(task)

    failed = [u for u in ws.typed("task_update") if u.get("status") == "failed"]
    assert failed and "1.5 MiB" in failed[0]["error"]
    assert runner.outbox.pending_notes() == []  # 不进入会无限重发的队列
    assert (runner.outbox.notes_dir / f"{task['id']}.md").exists()  # 本地留档


# ================================================================ note_request / ack


async def test_note_request_resends_from_outbox(make_ws_runner):
    runner, ws = make_ws_runner()
    ws.connected = True
    runner.outbox.add_note("task-1", "标题", "# 正文") 

    await runner.handle_ws_message({"type": "note_request", "task_id": "task-1"})
    notes = ws.typed("task_note")
    assert len(notes) == 1
    assert notes[0]["markdown"] == "# 正文"
    assert notes[0]["title"] == "标题"


async def test_ack_note_clears_outbox_but_keeps_local_note(make_ws_runner):
    runner, ws = make_ws_runner()
    ws.connected = True
    runner.outbox.add_note("task-1", "标题", "# 正文")
    note_path = runner.outbox.notes_dir / "task-1.md"
    assert note_path.exists()

    await runner.handle_ws_message({"type": "ack_note", "task_id": "task-1"})
    assert runner.outbox.pending_notes() == []
    assert note_path.exists()
    assert note_path.read_text(encoding="utf-8") == "# 正文"


async def test_note_request_after_ack_still_served_from_notes_file(make_ws_runner):
    runner, ws = make_ws_runner()
    ws.connected = True
    runner.outbox.add_note("task-1", "标题", "# 正文")
    await runner.handle_ws_message({"type": "ack_note", "task_id": "task-1"})

    await runner.handle_ws_message({"type": "note_request", "task_id": "task-1"})
    assert ws.typed("task_note")[0]["markdown"] == "# 正文"


async def test_note_request_missing_note_does_not_send(make_ws_runner):
    runner, ws = make_ws_runner()
    ws.connected = True
    await runner.handle_ws_message({"type": "note_request", "task_id": "missing"})
    assert ws.typed("task_note") == []


async def test_reconnect_ready_resends_pending_notes_and_active_update(make_ws_runner):
    runner, ws = make_ws_runner()
    ws.connected = True
    runner.outbox.add_note("task-1", "标题1", "# 正文1")
    runner.outbox.add_note("task-2", "标题2", "# 正文2")
    runner._active_task_id = "task-active"
    runner._active_progress = 45
    runner._active_stage = "transcribing"

    await runner.on_ws_ready()

    notes = ws.typed("task_note")
    assert {n["task_id"] for n in notes} == {"task-1", "task-2"}
    running = [
        u for u in ws.typed("task_update") if u.get("status") == "running"
    ]
    assert running and running[0]["task_id"] == "task-active"
    assert running[0]["progress"] == 45
    assert running[0]["stage"] == "transcribing"


async def test_failed_update_resent_after_reconnect(make_ws_runner, fake_bilinote):
    fake_bilinote.next_script = ["FAILED"]
    fake_bilinote.failure_message = "模型余额不足"
    runner, ws = make_ws_runner()
    ws.connected = False  # 发送失败
    await runner.process_task(_task())
    assert runner._unsent_failures, "WS 断开时失败状态应暂存"

    ws.connected = True
    await runner.on_ws_ready()
    failed = [u for u in ws.typed("task_update") if u.get("status") == "failed"]
    assert failed and "余额" in failed[0]["error"]
    assert failed[0]["error_code"] == "quota_exceeded"
    assert runner._unsent_failures == {}


async def test_hello_pulls_remaining_pending_via_rest(make_ws_runner, fake_relay):
    """契约 §11.3：hello 每次最多补发 50 条，其余由 Agent 用 REST 补领。"""
    first = fake_relay.add_task()
    second = fake_relay.add_task()
    runner, ws = make_ws_runner()
    ws.connected = True

    await runner.on_ws_ready()

    assert runner._queue.qsize() == 2
    assert {first["id"], second["id"]} <= runner._known_task_ids
    assert fake_relay.tasks[first["id"]]["status"] == "running"
    assert fake_relay.tasks[second["id"]]["status"] == "running"
    assert fake_relay.calls("GET", "/api/v1/agent/tasks/next")


async def test_hello_rest_pending_pull_disabled_by_config(
    make_config, relay_client, bilinote_client, fake_relay
):
    fake_relay.add_task()
    cfg = make_config(rest_fallback=False)
    ws = FakeWSClient()
    runner = RealtimeAgentRunner(
        cfg,
        relay_client,
        bilinote_client,
        backend=BackendSupervisor(cfg, bilinote=bilinote_client),
        outbox=Outbox(cfg.resolved_outbox_dir(), notes_dir=cfg.resolved_notes_dir()),
        ws_client=ws,
    )
    ws.connected = True
    await runner.on_ws_ready()
    assert runner._queue.qsize() == 0
    assert fake_relay.calls("GET", "/api/v1/agent/tasks/next") == []


# ================================================================ REST 兜底


async def test_rest_fallback_only_when_ws_disconnected(make_ws_runner, fake_relay):
    first = fake_relay.add_task()
    second = fake_relay.add_task()
    runner, ws = make_ws_runner()

    ws.connected = False
    claimed = await runner.rest_fallback_poll_once()
    assert claimed is not None
    assert claimed["id"] == first["id"]
    assert runner._queue.qsize() == 1

    # WS 已连接时不再轮询 REST（hello 后一次性补领由 pull_pending_via_rest 负责）
    ws.connected = True
    assert await runner.rest_fallback_poll_once() is None
    assert fake_relay.tasks[second["id"]]["status"] == "queued"


async def test_rest_fallback_respects_config(make_config, relay_client, bilinote_client):
    cfg = make_config(rest_fallback=False)
    ws = FakeWSClient()
    runner = RealtimeAgentRunner(
        cfg,
        relay_client,
        bilinote_client,
        backend=BackendSupervisor(cfg, bilinote=bilinote_client),
        outbox=Outbox(cfg.resolved_outbox_dir(), notes_dir=cfg.resolved_notes_dir()),
        ws_client=ws,
    )
    stop = asyncio.Event()
    task_handle = asyncio.create_task(runner.run_forever(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task_handle, timeout=3)
    assert ws.stop_called


# ================================================================ run_forever 集成


async def test_run_forever_processes_pushed_task_end_to_end(
    make_ws_runner, fake_relay, fake_bilinote
):
    runner, ws = make_ws_runner()
    stop = asyncio.Event()
    run_task = asyncio.create_task(runner.run_forever(stop))
    await wait_until(lambda: ws.connected)

    # 协议顺序：hello → task
    ws.push({"type": "hello", "agent_id": "a1", "server_time": "t"})
    task = _task()
    ws.push({"type": "task", "task": task})

    await wait_until(lambda: len(ws.typed("task_note")) == 1)
    assert fake_relay.complete_calls == []
    assert runner.outbox.pending_notes()

    # 手机 ack → outbox 清理
    ws.push({"type": "ack_note", "task_id": task["id"]})
    await wait_until(lambda: runner.outbox.pending_notes() == [])

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)
    assert ws.stop_called


async def test_run_forever_migrates_legacy_rest_outbox(make_ws_runner):
    runner, ws = make_ws_runner()
    runner.outbox.add_complete("legacy-task", "旧标题", "# 旧笔记")
    stop = asyncio.Event()
    run_task = asyncio.create_task(runner.run_forever(stop))

    await wait_until(lambda: len(runner.outbox.pending_notes()) == 1)
    pending = runner.outbox.pending_notes()
    assert pending[0].task_id == "legacy-task"
    assert pending[0].payload["markdown"] == "# 旧笔记"
    assert not [e for e in runner.outbox.entries() if e.kind == "complete"]
    assert (runner.outbox.notes_dir / "legacy-task.md").exists()

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


async def test_run_forever_sends_rest_heartbeat(make_ws_runner, fake_relay):
    runner, ws = make_ws_runner(heartbeat_interval_seconds=0.02)
    stop = asyncio.Event()
    run_task = asyncio.create_task(runner.run_forever(stop))
    await wait_until(lambda: len(fake_relay.heartbeat_calls) >= 1)
    stop.set()
    await asyncio.wait_for(run_task, timeout=5)
    assert fake_relay.heartbeat_calls[0]["busy"] is False

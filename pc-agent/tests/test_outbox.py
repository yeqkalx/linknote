"""outbox：持久化、补传、重启恢复、被回收时本地留档。"""

from __future__ import annotations

from linknote_agent.outbox import KIND_COMPLETE, KIND_FAIL, Outbox

from conftest import iso_now  # noqa: E402  (pytest 会把 tests/ 加入 sys.path)


def _outbox(tmp_path, name="outbox"):
    return Outbox(tmp_path / name, notes_dir=tmp_path / "notes")


async def test_add_complete_persists_across_instances(tmp_path):
    box = _outbox(tmp_path)
    entry = box.add_complete("task-1", "标题", "# 正文", duration_seconds=10, meta={"a": 1})
    assert entry.path is not None and entry.path.exists()

    # 模拟进程重启：新的 Outbox 实例仍能读到
    reopened = _outbox(tmp_path)
    entries = reopened.pending()
    assert len(entries) == 1
    assert entries[0].task_id == "task-1"
    assert entries[0].kind == KIND_COMPLETE
    assert entries[0].payload["markdown"] == "# 正文"
    assert entries[0].payload["title"] == "标题"
    assert entries[0].payload["duration_seconds"] == 10


async def test_flush_delivered(relay_client, fake_relay, tmp_path):
    task = fake_relay.add_task()
    await relay_client.next_task()
    box = _outbox(tmp_path)
    box.add_complete(task["id"], "标题", "# 正文")
    stats = await box.flush(relay_client)
    assert stats.delivered == 1
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"
    assert box.pending() == []


async def test_flush_deferred_when_relay_down(relay_client, fake_relay, tmp_path):
    task = fake_relay.add_task()
    await relay_client.next_task()
    box = _outbox(tmp_path)
    box.add_complete(task["id"], "标题", "# 正文")
    fake_relay.down = True
    stats = await box.flush(relay_client)
    assert stats.deferred == 1
    entries = box.pending()
    assert len(entries) == 1
    assert entries[0].attempts == 1
    assert "Relay" in entries[0].last_error

    # Relay 恢复后补传成功
    fake_relay.down = False
    stats = await box.flush(relay_client)
    assert stats.delivered == 1
    assert box.pending() == []
    assert fake_relay.tasks[task["id"]]["status"] == "succeeded"


async def test_flush_non_retriable_saves_note_locally(relay_client, fake_relay, tmp_path):
    # 任务还是 queued（没被本 Agent 领取）→ complete 会 409；笔记应落本地后丢弃
    task = fake_relay.add_task()
    box = _outbox(tmp_path)
    box.add_complete(task["id"], "标题", "# 幸存笔记")
    stats = await box.flush(relay_client)
    assert stats.dropped == 1
    assert stats.notes_saved == 1
    assert box.pending() == []
    saved = (tmp_path / "notes" / f"{task['id']}.md").read_text(encoding="utf-8")
    assert saved == "# 幸存笔记"


async def test_flush_auth_error_saves_note_locally(fake_relay, tmp_path):
    from linknote_agent.relay_client import RelayClient

    good = RelayClient("http://relay.test", fake_relay.token, transport=fake_relay.transport)
    task = fake_relay.add_task()
    await good.next_task()
    await good.aclose()

    box = _outbox(tmp_path)
    box.add_complete(task["id"], "标题", "# 配对失效后的幸存笔记")
    revoked = RelayClient("http://relay.test", "agt_revoked", transport=fake_relay.transport)
    try:
        stats = await box.flush(revoked)
    finally:
        await revoked.aclose()
    assert stats.dropped == 1
    assert stats.notes_saved == 1
    saved = (tmp_path / "notes" / f"{task['id']}.md").read_text(encoding="utf-8")
    assert "幸存笔记" in saved


async def test_flush_fail_entry(relay_client, fake_relay, tmp_path):
    task = fake_relay.add_task()
    await relay_client.next_task()
    box = _outbox(tmp_path)
    box.add_fail(task["id"], "bilinote_error", "生成失败")
    stats = await box.flush(relay_client)
    assert stats.delivered == 1
    assert fake_relay.tasks[task["id"]]["status"] == "failed"
    assert fake_relay.fail_calls[-1]["message"] == "生成失败"
    assert box.pending() == []


async def test_corrupted_entry_is_skipped(tmp_path):
    box = _outbox(tmp_path)
    box.directory.mkdir(parents=True, exist_ok=True)
    (box.directory / "complete-bad.json").write_text("{broken", encoding="utf-8")
    assert box.pending() == []


async def test_entries_sorted_by_created_at(tmp_path):
    box = _outbox(tmp_path)
    first = box.add_complete("task-b", "B", "b")
    second = box.add_complete("task-a", "A", "a")
    first.created_at = "2026-01-01T00:00:00Z"
    first.last_attempt_at = iso_now()
    # 直接改写文件时间字段
    from linknote_agent._jsonio import atomic_write_json

    atomic_write_json(first.path, first.to_json())
    entries = box.pending()
    assert [e.task_id for e in entries][0] == "task-b"


async def test_save_note_locally(tmp_path):
    box = _outbox(tmp_path)
    path = box.save_note_locally("task-x", "# 内容")
    assert path is not None
    assert path.read_text(encoding="utf-8") == "# 内容"

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

# ================================================================ WS 笔记（task-8）

async def test_add_note_saves_outbox_and_notes_file(tmp_path):
    box = _outbox(tmp_path)
    entry = box.add_note("task-note-1", "标题", "# 正文", duration_seconds=12)
    assert entry.kind == "note"
    assert entry.payload["markdown"] == "# 正文"
    assert entry.payload["title"] == "标题"
    assert entry.payload["duration_seconds"] == 12
    assert (tmp_path / "notes" / "task-note-1.md").read_text(encoding="utf-8") == "# 正文"

    reopened = _outbox(tmp_path)
    notes = reopened.pending_notes()
    assert len(notes) == 1
    assert notes[0].task_id == "task-note-1"
    assert reopened.has_note("task-note-1") is True


async def test_ack_note_removes_outbox_entry_keeps_notes_file(tmp_path):
    box = _outbox(tmp_path)
    box.add_note("task-note-1", "标题", "# 正文")
    assert box.ack_note("task-note-1") is True
    assert box.pending_notes() == []
    assert (tmp_path / "notes" / "task-note-1.md").exists()
    # 重复 ack 幂等
    assert box.ack_note("task-note-1") is False


async def test_note_for_falls_back_to_notes_file_after_ack(tmp_path):
    box = _outbox(tmp_path)
    box.add_note("task-note-1", "标题", "# 正文")
    box.ack_note("task-note-1")
    found = box.note_for("task-note-1")
    assert found is not None
    assert found[1] == "# 正文"
    assert box.note_for("missing-task") is None


async def test_prune_notes_removes_expired_entry_keeps_file(tmp_path):
    from datetime import datetime, timedelta, timezone

    from linknote_agent._jsonio import atomic_write_json

    box = _outbox(tmp_path)
    box.add_note("task-old", "旧", "# 旧笔记")
    box.add_note("task-new", "新", "# 新笔记")
    entry = [e for e in box.pending_notes() if e.task_id == "task-old"][0]
    entry.created_at = (
        datetime.now(timezone.utc) - timedelta(days=10)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    atomic_write_json(entry.path, entry.to_json())

    removed = box.prune_notes(7 * 86400)
    assert removed == 1
    remaining = {e.task_id for e in box.pending_notes()}
    assert remaining == {"task-new"}
    assert (tmp_path / "notes" / "task-old.md").exists()


async def test_flush_ignores_note_entries(relay_client, fake_relay, tmp_path):
    task = fake_relay.add_task()
    box = _outbox(tmp_path)
    box.add_note(task["id"], "标题", "# 正文")
    stats = await box.flush(relay_client)
    assert stats.total == 0
    assert fake_relay.complete_calls == []
    assert len(box.pending_notes()) == 1


async def test_migrate_legacy_complete_entries(tmp_path):
    box = _outbox(tmp_path)
    box.add_complete("legacy-1", "旧标题", "# 旧正文", duration_seconds=9)
    assert [e.kind for e in box.entries()] == ["complete"]

    migrated = box.migrate_legacy_complete_entries()
    assert migrated == 1
    assert [e.kind for e in box.entries()] == ["note"]
    note = box.pending_notes()[0]
    assert note.task_id == "legacy-1"
    assert note.payload["markdown"] == "# 旧正文"
    assert (tmp_path / "notes" / "legacy-1.md").exists()

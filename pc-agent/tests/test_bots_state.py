"""bot-state.json：原子读写、offset/seq/pending/限流持久化。"""

from __future__ import annotations

import json
import time

from linknote_agent.bots.state import (
    STATUS_SUBMITTED,
    STATUS_WINDOW_CLOSED,
    BotState,
    PendingTask,
)
from linknote_agent.config import AgentConfig, mask_secret


def _state(tmp_path) -> BotState:
    return BotState.load(tmp_path / "bot-state.json")


def test_state_roundtrip_is_atomic(tmp_path):
    state = _state(tmp_path)
    state.telegram_offset = 42
    state.telegram_username = "my_bot"
    state.qq_session_id = "session-1"
    state.qq_event_seq = 7
    state.next_qq_seq("qq:private:u1")
    state.add_pending(
        PendingTask(
            task_id="task-1",
            channel="telegram",
            chat_id="c1",
            reply_msg_id="m1",
            status=STATUS_SUBMITTED,
        )
    )
    state.set_last_note("telegram:private:c1", "task-1")
    now = time.time()
    state.allow_task("telegram:private:c1", now=now)
    state.save()

    loaded = BotState.load(tmp_path / "bot-state.json")
    assert loaded.telegram_offset == 42
    assert loaded.telegram_username == "my_bot"
    assert loaded.qq_session_id == "session-1"
    assert loaded.qq_event_seq == 7
    assert loaded.qq_seq["qq:private:u1"] == 1
    assert loaded.pending[0].task_id == "task-1"
    assert loaded.last_note_for("telegram:private:c1") == "task-1"
    assert loaded.task_times["telegram:private:c1"] == [now]

    raw = json.loads((tmp_path / "bot-state.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    # 原子写不留临时文件
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_update_offset_monotonic_and_persisted(tmp_path):
    state = _state(tmp_path)
    state.update_offset(5)
    assert state.telegram_offset == 6
    state.update_offset(3)  # 旧 update 不回退
    assert state.telegram_offset == 6
    assert BotState.load(tmp_path / "bot-state.json").telegram_offset == 6


def test_pending_crud(tmp_path):
    state = _state(tmp_path)
    state.add_pending(PendingTask(task_id="t1", channel="qq", chat_id="u1"))
    state.add_pending(PendingTask(task_id="t2", channel="qq", chat_id="u1"))
    assert [p.task_id for p in state.pending_for_chat("qq:private:u1")] == ["t1", "t2"]

    state.update_pending("t1", status=STATUS_WINDOW_CLOSED)
    assert state.find("t1").status == STATUS_WINDOW_CLOSED
    assert state.pendings(STATUS_WINDOW_CLOSED)[0].task_id == "t1"

    state.remove_pending("t1")
    assert state.find("t1") is None
    assert len(state.pending) == 1


def test_qq_seq_increments(tmp_path):
    state = _state(tmp_path)
    assert state.next_qq_seq("qq:private:u1") == 1
    assert state.next_qq_seq("qq:private:u1") == 2
    assert state.next_qq_seq("qq:group:g1") == 1
    reloaded = BotState.load(tmp_path / "bot-state.json")
    assert reloaded.qq_seq["qq:private:u1"] == 2


def test_rate_limit_10_tasks_per_hour(tmp_path):
    state = _state(tmp_path)
    now = 10_000.0
    for _ in range(10):
        assert state.allow_task("telegram:private:c1", now=now)
    assert not state.allow_task("telegram:private:c1", now=now + 1)
    # 另一个聊天不受影响
    assert state.allow_task("telegram:private:c2", now=now + 2)
    # 窗口滑过后恢复
    assert state.allow_task("telegram:private:c1", now=now + 3601)
    reloaded = BotState.load(tmp_path / "bot-state.json")
    assert reloaded.allow_task("telegram:private:c2", now=now + 3602)


def test_secret_masking_in_export_and_repr(tmp_path):
    cfg = AgentConfig(
        telegram_bot_token="123456:ABCDEF-secret-token",
        telegram_device_token="dev_telegram_secret",
        qq_app_secret="qq-super-secret",
        qq_device_token="dev_qq_secret",
    )
    exported = cfg.to_dict(include_token=False)
    assert "ABCDEF-secret-token" not in json.dumps(exported, ensure_ascii=False)
    for key in ("telegram_bot_token", "telegram_device_token", "qq_app_secret", "qq_device_token"):
        assert exported[key] == mask_secret(getattr(cfg, key))
    # dataclass repr 也不出现明文
    shown = repr(cfg)
    assert "ABCDEF-secret-token" not in shown
    assert "qq-super-secret" not in shown

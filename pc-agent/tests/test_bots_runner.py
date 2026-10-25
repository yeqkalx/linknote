"""Bot Runner：链接→任务→本机笔记→投递、重启恢复、device token、限流、命令。"""

from __future__ import annotations

import asyncio
import json
import time

from bot_helpers import build_env, make_message, wait_until
from linknote_agent.bots.state import PendingTask
from linknote_agent.cli import build_parser, cmd_run
from linknote_agent.config import AgentConfig, default_config_path


async def _start(env):
    await env.runner.start()
    assert env.runner.started
    return env.adapters["telegram"]


async def test_help_command(tmp_path):
    env = build_env(tmp_path)
    adapter = await _start(env)
    try:
        await adapter.emit(make_message(text="/help"))
        await wait_until(lambda: len(adapter.sent_text) == 1)
        assert "支持的平台" in adapter.sent_text[0]["text"]
        assert "/status" in adapter.sent_text[0]["text"]
    finally:
        await env.runner.close()


async def test_link_creates_task_and_replies(tmp_path):
    env = build_env(tmp_path)
    adapter = await _start(env)
    try:
        message = make_message(
            text="看看这个 https://www.bilibili.com/video/BV1xx411c7mD 不错", message_id="msg-9"
        )
        await adapter.emit(message)
        await wait_until(lambda: len(env.bot_relay.created) == 1)
        created = env.bot_relay.created[0]
        assert created["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert created["client_key"] == "telegram-msg-9"
        await wait_until(lambda: len(adapter.sent_text) == 1)
        assert "已提交" in adapter.sent_text[0]["text"]
        pending = env.state.pendings()
        assert len(pending) == 1 and pending[0].task_id == created["task_id"]
    finally:
        await env.runner.close()


async def test_delivery_success_reads_local_note(tmp_path):
    env = build_env(tmp_path)
    env.bot_relay.add_task("task-0001", status="succeeded", title="视频标题")
    env.seed_note("task-0001", title="本地笔记", markdown="# 本地笔记\n\n内容")
    env.state.add_pending(
        PendingTask(task_id="task-0001", channel="telegram", chat_id="chat-1")
    )
    adapter = await _start(env)
    try:
        await wait_until(lambda: len(adapter.sent_notes) == 1, timeout=5)
        sent = adapter.sent_notes[0]
        assert sent["title"] == "本地笔记"
        assert sent["markdown"].startswith("# 本地笔记")
        assert sent["path"].exists()
        assert env.state.find("task-0001") is None
        assert env.state.last_note_for("telegram:private:chat-1") == "task-0001"
    finally:
        await env.runner.close()


async def test_failed_task_notifies_error(tmp_path):
    env = build_env(tmp_path)
    env.bot_relay.add_task("task-fail", status="failed", error_message="模型余额不足")
    env.state.add_pending(PendingTask(task_id="task-fail", channel="telegram", chat_id="chat-1"))
    adapter = await _start(env)
    try:
        await wait_until(lambda: len(adapter.sent_text) == 1, timeout=5)
        assert "生成失败" in adapter.sent_text[0]["text"]
        assert "余额不足" in adapter.sent_text[0]["text"]
        assert env.state.find("task-fail") is None
    finally:
        await env.runner.close()


async def test_restart_recovery_delivers_pending(tmp_path):
    env1 = build_env(tmp_path)
    env1.bot_relay.add_task("task-restart", status="succeeded", title="t")
    env1.seed_note("task-restart", title="重启笔记", markdown="# 重启笔记")
    env1.state.add_pending(
        PendingTask(task_id="task-restart", channel="telegram", chat_id="chat-1")
    )
    await env1.runner.close()  # 没启动过；模拟上一次进程留下的 state

    env2 = build_env(tmp_path)
    env2.runner.state = env2.new_state()  # 从磁盘恢复 pending
    env2.bot_relay.add_task("task-restart", status="succeeded", title="t")
    adapter = await _start(env2)
    try:
        await wait_until(lambda: len(adapter.sent_notes) == 1, timeout=5)
        assert adapter.sent_notes[0]["title"] == "重启笔记"
        assert env2.new_state().find("task-restart") is None
    finally:
        await env2.runner.close()


async def test_device_token_minted_saved_and_reused(tmp_path):
    env = build_env(tmp_path)
    await env.runner.start()
    try:
        assert [call["platform"] for call in env.agent_relay.device_calls] == ["telegram"]
        assert env.cfg.telegram_device_token == "dev_bot_0001"
        saved = json.loads(default_config_path().read_text(encoding="utf-8"))
        assert saved["telegram_device_token"] == "dev_bot_0001"
        # 已存在 token 时不重复签发
        assert await env.runner.ensure_device_token("telegram") == "dev_bot_0001"
        assert len(env.agent_relay.device_calls) == 1
    finally:
        await env.runner.close()


async def test_rate_limit_rejects_11th_task(tmp_path):
    env = build_env(tmp_path)
    chat_key = "telegram:private:chat-1"
    env.state.task_times[chat_key] = [time.time()] * 10
    adapter = await _start(env)
    try:
        await adapter.emit(make_message(text="https://www.bilibili.com/video/BV1xx"))
        await wait_until(lambda: len(adapter.sent_text) == 1)
        assert "频率限制" in adapter.sent_text[0]["text"]
        assert env.bot_relay.created == []
    finally:
        await env.runner.close()


async def test_last_command_resends_note(tmp_path):
    env = build_env(tmp_path)
    env.seed_note("task-last", title="最近笔记", markdown="# 最近笔记")
    env.state.set_last_note("telegram:private:chat-1", "task-last")
    adapter = await _start(env)
    try:
        await adapter.emit(make_message(text="/last"))
        await wait_until(lambda: len(adapter.sent_notes) == 1)
        assert adapter.sent_notes[0]["title"] == "最近笔记"
    finally:
        await env.runner.close()


async def test_retry_command_creates_new_pending(tmp_path):
    env = build_env(tmp_path)
    env.bot_relay.add_task("task-old", status="failed", error_message="boom")
    adapter = await _start(env)
    try:
        await adapter.emit(make_message(text="/retry task-old"))
        await wait_until(lambda: len(env.bot_relay.retried) == 1)
        assert "已重新提交" in adapter.sent_text[0]["text"]
        assert env.state.pendings()[0].task_id == "task-retry-0001"
    finally:
        await env.runner.close()


# ---------------------------------------------------------------- CLI 集成


class _FakeWSClient:
    def __init__(self) -> None:
        self.events = asyncio.Queue()
        self.sent = []
        self.connected = False
        self.stop_called = False
        self.url = "ws://fake/api/v1/agent/ws"

    async def run(self) -> None:
        self.connected = True
        try:
            await asyncio.Event().wait()
        finally:
            self.connected = False

    async def stop(self) -> None:
        self.stop_called = True
        self.connected = False

    async def send(self, payload) -> bool:
        return self.connected


class _FakeBotRunner:
    def __init__(self) -> None:
        self.run_called = False
        self.closed = False

    async def run(self, stop_event=None) -> None:
        self.run_called = True
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


async def test_cmd_run_starts_and_stops_bot_runner(tmp_path, monkeypatch, fake_relay, fake_bilinote):
    cfg = AgentConfig()
    cfg.relay_base_url = "http://relay.test"
    cfg.agent_token = "agt_test_token_0123456789"
    cfg.telegram_enabled = True
    cfg.telegram_bot_token = "123456:TEST-token"
    cfg.save()

    fake_bot = _FakeBotRunner()
    fake_ws = _FakeWSClient()
    stop = asyncio.Event()
    args = build_parser().parse_args(["run"])

    async def stopper():
        await asyncio.sleep(0.15)
        stop.set()

    await asyncio.gather(
        cmd_run(
            args,
            relay_transport=fake_relay.transport,
            bilinote_transport=fake_bilinote.transport,
            ws_client=fake_ws,
            stop_event=stop,
            bot_runner=fake_bot,
        ),
        stopper(),
    )
    assert fake_bot.run_called is True
    assert fake_bot.closed is True
    assert fake_ws.stop_called is True

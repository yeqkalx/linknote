"""Telegram 无 Relay 本地模式：链接→本地 TaskPipeline→本机笔记→send_note。"""

from __future__ import annotations

import asyncio
import time

from bot_helpers import FakeAdapter, make_message, wait_until
from linknote_agent.bots.runner import BotRunner
from linknote_agent.bots.state import (
    LOCAL_FAILED,
    LOCAL_RESTART_ERROR,
    LOCAL_RUNNING,
    LOCAL_SUCCEEDED,
    BotState,
    LocalTask,
)
from linknote_agent.cli import build_parser, cmd_run
from linknote_agent.config import AgentConfig
from linknote_agent.outbox import Outbox
from linknote_agent.task_pipeline import FailureInfo, ProgressEvent, TaskOutcome


def _completed(title: str = "视频标题", markdown: str = "# 笔记\n\n正文内容") -> TaskOutcome:
    return TaskOutcome(
        kind="completed", result={"markdown": markdown, "audio_meta": {"title": title}}
    )


def _failed(message: str = "模型 Key 失效") -> TaskOutcome:
    return TaskOutcome(kind="failed", failure=FailureInfo("model_auth_error", message))


class FakePipeline:
    def __init__(self, outcomes: list[TaskOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    async def execute(self, task_id, raw_url, *, on_progress, on_lease=None, platform_hint=""):
        self.calls.append((task_id, raw_url))
        await on_progress(ProgressEvent("parsing", 5, "本地处理中"))
        if self.outcomes:
            return self.outcomes.pop(0)
        return TaskOutcome(kind="failed", failure=FailureInfo("unknown", "fake 没有更多结果"))


class LocalEnv:
    def __init__(self, tmp_path, outcomes: list[TaskOutcome], *, state: BotState | None = None):
        self.tmp_path = tmp_path
        self.cfg = AgentConfig()
        self.cfg.relay_base_url = ""
        self.cfg.telegram_enabled = True
        self.cfg.telegram_bot_token = "123456:TEST-local-token"
        self.cfg.bot_task_poll_seconds = 0.01
        self.outbox = Outbox(tmp_path / "outbox", notes_dir=tmp_path / "notes")
        self.state = state or BotState(tmp_path / "bot-state.json")
        self.adapter = FakeAdapter("telegram")
        self.pipeline = FakePipeline(outcomes)
        self.runner = BotRunner(
            self.cfg,
            None,
            local_mode=True,
            outbox=self.outbox,
            state=self.state,
            adapters={"telegram": self.adapter},
            pipeline_factory=lambda: self.pipeline,
        )

    async def start(self):
        await self.runner.start()
        assert self.runner.started
        return self.adapter


async def test_local_link_runs_pipeline_saves_note_and_replies(tmp_path):
    env = LocalEnv(tmp_path, [_completed()])
    adapter = await env.start()
    try:
        await adapter.emit(make_message(text="看这个 https://www.bilibili.com/video/BV1xx411c7mD"))
        await wait_until(lambda: len(env.pipeline.calls) == 1)
        await wait_until(lambda: len(adapter.sent_notes) == 1, timeout=5)
        await wait_until(lambda: env.state.local_tasks_recent()[0].status == LOCAL_SUCCEEDED)

        # 1) 先回复“已提交”，再回笔记；不出现 Relay 字样要求（仅本地提示）
        assert any("无需 Relay" in item["text"] for item in adapter.sent_text)
        sent = adapter.sent_notes[0]
        assert sent["title"] == "视频标题"
        assert sent["markdown"].startswith("# 笔记")
        assert sent["path"].exists()
        assert sent["path"].read_text(encoding="utf-8") == sent["markdown"]

        task = env.state.local_tasks_recent()[0]
        assert task.task_id.startswith("local-")
        assert task.status == LOCAL_SUCCEEDED
        assert env.state.last_note_for("telegram:private:chat-1") == task.task_id
        # 2) 本地模式不创建 Relay device token / bot relay client
        assert env.runner._bot_relays == {}
        assert env.runner.relay is None
    finally:
        await env.runner.close()


async def test_local_failed_task_replies_error(tmp_path):
    env = LocalEnv(tmp_path, [_failed("模型 Key 失效")])
    adapter = await env.start()
    try:
        await adapter.emit(make_message())
        await wait_until(lambda: len(adapter.sent_text) >= 2)
        texts = [item["text"] for item in adapter.sent_text]
        assert any("生成失败" in text and "模型 Key 失效" in text for text in texts)
        task = env.state.local_tasks_recent()[0]
        assert task.status == LOCAL_FAILED
        assert "模型 Key 失效" in task.error
        assert adapter.sent_notes == []
    finally:
        await env.runner.close()


async def test_local_status_lists_tasks(tmp_path):
    env = LocalEnv(tmp_path, [])
    env.state.add_local_task(
        LocalTask(
            task_id="local-aaaa11112222",
            chat_key="telegram:private:chat-1",
            chat_id="chat-1",
            url="https://www.bilibili.com/video/BV1xx",
            status=LOCAL_SUCCEEDED,
            title="本地标题",
        )
    )
    adapter = await env.start()
    try:
        await adapter.emit(make_message(text="/status"))
        await wait_until(lambda: len(adapter.sent_text) == 1)
        text = adapter.sent_text[0]["text"]
        assert "最近本地任务" in text
        assert "aaaa1111" in text and "本地标题" in text
    finally:
        await env.runner.close()


async def test_local_last_resends_note(tmp_path):
    env = LocalEnv(tmp_path, [])
    note_path = env.outbox.save_note_locally("local-bbbb11112222", "# 本地笔记\n\n正文")
    assert note_path is not None
    env.state.add_local_task(
        LocalTask(
            task_id="local-bbbb11112222",
            chat_key="telegram:private:chat-1",
            chat_id="chat-1",
            status=LOCAL_SUCCEEDED,
            title="最近本地笔记",
        )
    )
    env.state.set_last_note("telegram:private:chat-1", "local-bbbb11112222")
    adapter = await env.start()
    try:
        await adapter.emit(make_message(text="/last"))
        await wait_until(lambda: len(adapter.sent_notes) == 1)
        assert adapter.sent_notes[0]["title"] == "最近本地笔记"
        assert adapter.sent_notes[0]["markdown"].startswith("# 本地笔记")
    finally:
        await env.runner.close()


async def test_local_rate_limit_rejects_11th_task(tmp_path):
    env = LocalEnv(tmp_path, [_completed()])
    env.state.task_times["telegram:private:chat-1"] = [time.time()] * 10
    adapter = await env.start()
    try:
        await adapter.emit(make_message())
        await wait_until(lambda: len(adapter.sent_text) == 1)
        assert "频率限制" in adapter.sent_text[0]["text"]
        assert env.pipeline.calls == []
    finally:
        await env.runner.close()


async def test_local_retry_reruns_failed_task(tmp_path):
    env = LocalEnv(tmp_path, [_completed(title="重试后笔记", markdown="# 重试成功")])
    env.state.add_local_task(
        LocalTask(
            task_id="local-cccc11112222",
            chat_key="telegram:private:chat-1",
            chat_id="chat-1",
            url="https://www.bilibili.com/video/BV1xx",
            status=LOCAL_FAILED,
            error="上次失败",
        )
    )
    adapter = await env.start()
    try:
        await adapter.emit(make_message(text="/retry local-cccc11112222"))
        await wait_until(lambda: len(env.pipeline.calls) == 1)
        await wait_until(lambda: len(adapter.sent_notes) == 1, timeout=5)
        assert any("重新提交" in item["text"] for item in adapter.sent_text)
        assert env.pipeline.calls[0][1] == "https://www.bilibili.com/video/BV1xx"
        assert env.state.local_tasks_recent()[0].status == LOCAL_SUCCEEDED
    finally:
        await env.runner.close()


def test_state_load_marks_interrupted_local_task_failed(tmp_path):
    state = BotState(tmp_path / "bot-state.json")
    state.add_local_task(
        LocalTask(
            task_id="local-dddd11112222",
            chat_key="telegram:private:chat-1",
            chat_id="chat-1",
            url="https://www.bilibili.com/video/BV1xx",
            status=LOCAL_RUNNING,
        )
    )
    loaded = BotState.load(tmp_path / "bot-state.json")
    assert loaded.local_tasks[0].status == LOCAL_FAILED
    assert loaded.local_tasks[0].error == LOCAL_RESTART_ERROR


# ---------------------------------------------------------------- CLI 本地模式


class _FakeLocalBot:
    instances: list["_FakeLocalBot"] = []

    def __init__(self, cfg, relay, **kwargs):
        self.cfg = cfg
        self.relay = relay
        self.kwargs = kwargs
        self.run_called = False
        self.closed = False
        _FakeLocalBot.instances.append(self)

    async def run(self, stop_event=None):
        self.run_called = True
        assert stop_event is not None
        await stop_event.wait()

    async def close(self):
        self.closed = True


async def test_cli_run_enters_local_mode_without_relay(
    tmp_path, monkeypatch, capsys, fake_bilinote
):
    cfg = AgentConfig()
    cfg.relay_base_url = ""
    cfg.telegram_enabled = True
    cfg.telegram_bot_token = "123456:TEST-cli-token"
    cfg.save()

    monkeypatch.setattr("linknote_agent.cli.BotRunner", _FakeLocalBot)
    # 本地模式绝不能构造 RelayClient / 连接 Relay
    def _no_relay(*args, **kwargs):
        raise AssertionError("本地模式不应创建 RelayClient")

    monkeypatch.setattr("linknote_agent.cli.RelayClient", _no_relay)

    stop = asyncio.Event()
    args = build_parser().parse_args(["run"])

    async def stopper():
        await asyncio.sleep(0.15)
        stop.set()

    rc = await asyncio.gather(
        cmd_run(
            args,
            bilinote_transport=fake_bilinote.transport,
            stop_event=stop,
        ),
        stopper(),
    )
    assert rc[0] == 0
    out = capsys.readouterr().out
    assert "Telegram 本地模式（无 Relay）" in out
    assert len(_FakeLocalBot.instances) == 1
    bot = _FakeLocalBot.instances[0]
    assert bot.relay is None
    assert bot.kwargs.get("local_mode") is True
    assert bot.kwargs.get("bilinote") is not None
    assert bot.kwargs.get("backend") is not None
    assert bot.run_called is True
    assert bot.closed is True


async def test_cli_run_without_relay_and_without_telegram_still_exit_2(
    tmp_path, capsys
):
    cfg = AgentConfig()
    cfg.relay_base_url = ""
    cfg.telegram_enabled = False
    cfg.telegram_bot_token = ""
    cfg.save()
    args = build_parser().parse_args(["run"])
    rc = await cmd_run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "还没有配置 Relay" in err

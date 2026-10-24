"""Telegram Bot 适配器：getMe、getUpdates 长轮询、私聊/群聊过滤、预览+文档、错误处理。"""

from __future__ import annotations

import json

import pytest

from bot_helpers import FakeTelegram, telegram_update, wait_until
from linknote_agent.bots.base import BotError
from linknote_agent.bots.state import BotState
from linknote_agent.bots.telegram import TelegramBot

TOKEN = "123456:TEST-secret-token"


async def _start_bot(tmp_path, fake: FakeTelegram, **kwargs):
    state = BotState(tmp_path / "bot-state.json")
    bot = TelegramBot(
        TOKEN,
        state=state,
        transport=fake.transport,
        poll_timeout=1,
        sleep=__import__("asyncio").sleep,
        **kwargs,
    )
    received = []

    async def on_message(message):
        received.append(message)

    await bot.start(on_message)
    return bot, state, received


async def test_get_me_and_private_message(tmp_path):
    fake = FakeTelegram()
    fake.updates = [[telegram_update(update_id=1, text="https://www.bilibili.com/video/BV1xx")]]
    bot, state, received = await _start_bot(tmp_path, fake)
    try:
        assert bot.username == "my_test_bot"
        await wait_until(lambda: len(received) == 1)
        assert received[0].channel == "telegram"
        assert received[0].chat_type == "private"
        assert "bilibili.com" in received[0].text
        await wait_until(lambda: state.telegram_offset == 2)
    finally:
        await bot.stop()


async def test_group_filtered_by_default(tmp_path):
    fake = FakeTelegram()
    fake.updates = [[
        telegram_update(update_id=1, text="https://www.bilibili.com/video/BV1xx", chat_id=-100, chat_type="group"),
        telegram_update(update_id=2, text="私聊链接 https://youtu.be/abc"),
    ]]
    bot, state, received = await _start_bot(tmp_path, fake)
    try:
        await wait_until(lambda: state.telegram_offset >= 3)
        assert len(received) == 1
        assert received[0].chat_type == "private"
    finally:
        await bot.stop()


async def test_group_allowed_with_mention_or_command(tmp_path):
    fake = FakeTelegram()
    fake.updates = [[
        telegram_update(
            update_id=1,
            text="@my_test_bot https://www.bilibili.com/video/BV1xx",
            chat_id=-100,
            chat_type="group",
        ),
        telegram_update(update_id=2, text="/status", chat_id=-100, chat_type="group"),
    ]]
    bot, _, received = await _start_bot(tmp_path, fake, allow_groups=True)
    try:
        await wait_until(lambda: len(received) == 2)
        assert received[0].chat_type == "group"
        assert "bilibili.com" in received[0].text
        assert not received[0].text.startswith("@my_test_bot")
        assert received[1].text == "/status"
    finally:
        await bot.stop()


async def test_bot_messages_and_empty_text_ignored(tmp_path):
    fake = FakeTelegram()
    fake.updates = [[
        telegram_update(update_id=1, text="hello", is_bot=True),
        {"update_id": 2, "message": {"message_id": 2, "chat": {"id": 1, "type": "private"}}},
    ]]
    bot, state, received = await _start_bot(tmp_path, fake)
    try:
        await wait_until(lambda: state.telegram_offset >= 3)
        assert received == []
    finally:
        await bot.stop()


async def test_send_note_preview_and_document(tmp_path):
    fake = FakeTelegram()
    bot, state, _ = await _start_bot(tmp_path, fake, preview_chars=120)
    try:
        note = tmp_path / "notes" / "task-1.md"
        note.parent.mkdir(parents=True)
        note.write_text("# 完整笔记\n\n" + "正文" * 200, encoding="utf-8")
        from bot_helpers import make_message

        message = make_message(channel="telegram", chat_id="100")
        await bot.send_note(message, "测试标题", "# 标题\n\n" + "内容" * 200, note)
        assert len(fake.sent_messages) == 1
        text = fake.sent_messages[0]["text"]
        assert "测试标题" in text and "（完整笔记见附件）" in text
        assert len(text) <= bot.preview_chars + 80
        assert len(fake.sent_documents) == 1
        assert b"task-1.md" in fake.sent_documents[0]["content"]
    finally:
        await bot.stop()


async def test_invalid_token_error_does_not_leak_token(tmp_path):
    fake = FakeTelegram()
    fake.me_error = (401, {"ok": False, "error_code": 401, "description": "Unauthorized token 123456:TEST-secret-token"})
    state = BotState(tmp_path / "bot-state.json")
    bot = TelegramBot(TOKEN, state=state, transport=fake.transport, poll_timeout=1)
    with pytest.raises(BotError) as excinfo:
        await bot.start(lambda message: None)
    assert TOKEN not in excinfo.value.message
    assert "401" in excinfo.value.message
    await bot.stop()


async def test_429_retry_then_continue(tmp_path):
    fake = FakeTelegram()
    fake.poll_errors = [(429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 0}})]
    fake.updates = [[telegram_update(update_id=1, text="/help")]]
    bot, state, received = await _start_bot(tmp_path, fake)
    try:
        await wait_until(lambda: len(received) == 1, timeout=5)
        assert received[0].text == "/help"
    finally:
        await bot.stop()

"""桌面端快照解析单元测试（不需要 QApplication / 真实引擎）。

覆盖新功能的纯数据层：
- Agent 日志 → 任务事件解析；
- 当前任务 / 队列 / 最近完成失败快照；
- 本地 notes + outbox 历史笔记扫描（标题 / 字数 / 状态 / 去重）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from linknote_desktop.controller import (
    _note_title_from_markdown,
    build_task_snapshot,
    count_words,
    parse_agent_task_events,
    scan_history_entries,
)

LOG_SAMPLE = """\
2026-10-24 16:00:00,001 [INFO] linknote_agent.runner: 领到任务 task_id=task-1 url=https://www.bilibili.com/video/BV1xx platform=bilibili
2026-10-24 16:00:02,000 [INFO] linknote_agent.runner: 已提交 BiliNote 任务 relay_task=task-1 bilinote_task=bili-1001 platform=bilibili
2026-10-24 16:05:30,001 [INFO] linknote_agent.runner: 任务完成并已回传 task_id=task-1 title=旧视频笔记 markdown=1200 字 duration=754 秒
2026-10-24 16:06:00,000 [ERROR] linknote_agent.runner: 任务失败已回传 task_id=task-failed code=quota_exceeded message=模型账户余额不足
2026-10-24 16:07:00,001 [INFO] linknote_agent.runner: 领到任务 task_id=task-2 url=https://www.youtube.com/watch?v=demo platform=youtube
2026-10-24 16:07:02,000 [INFO] linknote_agent.runner: 已提交 BiliNote 任务 relay_task=task-2 bilinote_task=bili-2002 platform=youtube
2026-10-24 16:08:00,000 [INFO] linknote_agent.runner: 心跳正常
2026-10-24 16:08:30,000 [WARNING] linknote_agent.runner: 任务 task-3 已生成笔记但 Relay 暂时不可达：模拟网络错误
"""


def test_parse_agent_task_events_kinds_and_fields() -> None:
    events = parse_agent_task_events(LOG_SAMPLE)
    kinds = [event["kind"] for event in events]
    assert kinds == ["start", "submitted", "completed", "failed", "start", "submitted", "pending_note"]
    assert events[0]["task_id"] == "task-1"
    assert events[0]["platform"] == "bilibili"
    assert events[1]["bilinote_task"] == "bili-1001"
    assert events[2]["title"] == "旧视频笔记"
    assert events[2]["markdown_chars"] == "1200"
    assert events[3]["code"] == "quota_exceeded"
    assert events[4]["task_id"] == "task-2"
    assert events[0]["timestamp"] is not None
    assert events[0]["ts"].startswith("2026-10-24T16:00:00")


def test_parse_agent_task_events_ignores_unrelated_lines() -> None:
    events = parse_agent_task_events(LOG_SAMPLE)
    messages = [event.get("message", "") for event in events]
    assert all("心跳正常" not in message for message in messages)
    # 没有日志行格式的文本也不应崩溃
    assert parse_agent_task_events("随便写的一行\n") == []
    assert parse_agent_task_events("") == []


def test_build_task_snapshot_current_recent_and_queue() -> None:
    events = parse_agent_task_events(LOG_SAMPLE)
    snapshot = build_task_snapshot(
        events,
        relay_me={
            "online": True,
            "agent": {"online": True},
            "queue": {"queued": 2, "running": 1},
            "devices": [{"id": "d1"}, {"id": "d2"}],
        },
        agent_running=True,
        agent_pid=5150,
        agent_paired=True,
        outbox_pending=3,
        now=datetime(2026, 10, 24, 16, 10, 0),
    )
    current = snapshot["current"]
    assert current is not None
    assert current["task_id"] == "task-2"
    assert current["platform"] == "youtube"
    assert current["bilinote_task"] == "bili-2002"
    assert current["stage"] == "parsing"
    assert current["progress"] >= 2
    assert snapshot["recent"][0]["task_id"] in {"task-failed", "task-3"}
    recent_ids = {item["task_id"] for item in snapshot["recent"]}
    assert {"task-1", "task-failed"} <= recent_ids
    assert snapshot["queue"] == {"queued": 2, "running": 1}
    assert snapshot["devices"] == 2
    assert snapshot["outbox_pending"] == 3
    assert snapshot["relay_online"] is True
    assert snapshot["agent"] == {"running": True, "pid": 5150, "paired": True}
    assert snapshot["generated_label"] == "16:10:00"
    # 已完成任务保留标题 / 字数，便于「最近完成」展示
    completed = next(item for item in snapshot["recent"] if item["task_id"] == "task-1")
    assert completed["title"] == "旧视频笔记"
    assert completed["markdown_chars"] == 1200
    assert completed["status"] == "completed"


def test_build_task_snapshot_without_tasks() -> None:
    snapshot = build_task_snapshot([], now=datetime(2026, 10, 24, 16, 0, 0))
    assert snapshot["current"] is None
    assert snapshot["recent"] == []
    assert snapshot["queue"] == {"queued": 0, "running": 0}
    assert snapshot["relay_online"] is False


def test_count_words_and_note_title() -> None:
    assert count_words("你好 world 123") == 4  # 2 个中文字 + 2 个英文/数字词
    assert count_words("") == 0
    assert _note_title_from_markdown("# 会议纪要\n正文", "fallback") == "会议纪要"
    assert _note_title_from_markdown("没有标题", "task-demo") == "task-demo"
    assert _note_title_from_markdown("", "") == "未命名笔记"


def test_scan_history_entries_merges_notes_and_outbox(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    outbox_dir = tmp_path / "outbox"
    notes_dir.mkdir()
    outbox_dir.mkdir()

    # notes 文件名在真实环境里是任务 ID（hex/uuid），这样 outbox 副本可以按 task_id 去重
    (notes_dir / "a1b2c3d4e5f60001.md").write_text(
        "# 第一条本地笔记\n\n## 摘要\n\n内容内容 content。\n", encoding="utf-8"
    )
    (notes_dir / "b1b2c3d4e5f60002.md").write_text(
        "# 第二条本地笔记\n\n- 一条要点\n", encoding="utf-8"
    )
    (outbox_dir / "complete-task-gamma-003.json").write_text(
        json.dumps(
            {
                "task_id": "task-gamma-003",
                "kind": "complete",
                "payload": {
                    "title": "待回传笔记",
                    "markdown": "# 待回传笔记\n\n正文正文。",
                    "meta": {"platform": "douyin", "url": "https://example.com/v/3"},
                },
                "created_at": "2026-10-24T08:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (outbox_dir / "fail-task-delta-004.json").write_text(
        json.dumps(
            {"task_id": "task-delta-004", "kind": "fail", "payload": {"code": "x"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 与 notes 里同一个 task_id 的 outbox 副本应被去重（不重复展示）
    (outbox_dir / "complete-a1b2c3d4e5f60001.json").write_text(
        json.dumps(
            {
                "task_id": "a1b2c3d4e5f60001",
                "kind": "complete",
                "payload": {"title": "重复副本", "markdown": "# 重复副本"},
                "created_at": "2026-10-24T09:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = scan_history_entries(notes_dir, outbox_dir)
    counts = result["counts"]
    assert counts["notes"] == 2
    assert counts["pending"] == 2  # gamma + alpha 副本
    assert counts["failed_pending"] == 1
    assert counts["total"] == 3
    assert counts["words"] > 0
    titles = {item["title"] for item in result["items"]}
    assert {"第一条本地笔记", "第二条本地笔记", "待回传笔记"} <= titles
    assert "重复副本" not in titles
    pending = [item for item in result["items"] if item["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["platform"] == "douyin"
    assert all(item["path"] and Path(item["path"]).exists() for item in result["items"])
    assert result["notes_dir"] == str(notes_dir)


def test_scan_history_entries_empty_dirs(tmp_path: Path) -> None:
    result = scan_history_entries(tmp_path / "missing-notes", tmp_path / "missing-outbox")
    assert result["items"] == []
    assert result["counts"] == {
        "notes": 0,
        "pending": 0,
        "failed_pending": 0,
        "total": 0,
        "words": 0,
    }


def test_scan_history_entries_skips_broken_json(tmp_path: Path) -> None:
    outbox_dir = tmp_path / "outbox"
    outbox_dir.mkdir()
    (outbox_dir / "complete-broken.json").write_text("{not json", encoding="utf-8")
    result = scan_history_entries(tmp_path / "notes", outbox_dir)
    assert result["items"] == []
    assert result["counts"]["pending"] == 0

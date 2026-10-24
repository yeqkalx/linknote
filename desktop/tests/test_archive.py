"""笔记自动归档单元测试（frontmatter / 幂等 / 目录不存在 / 非法路径）。

不依赖 QApplication，也不访问网络。
"""
from __future__ import annotations

import json
from pathlib import Path

from linknote_desktop.controller import (
    archive_notes_to_dir,
    build_archive_frontmatter,
    build_archived_note,
)


def _write_note(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def test_build_archive_frontmatter_fields_and_escaping() -> None:
    text = build_archive_frontmatter(
        {
            "title": '会议 "纪要"',
            "source_url": "https://example.com/v/1",
            "platform": "bilibili",
            "created_at": "2026-10-24T10:00:00Z",
            "model": "deepseek/deepseek-v4-flash",
            "app_version": "0.3.0",
        }
    )
    assert text.startswith("---\n")
    assert text.rstrip().endswith("---")
    for key in ("title", "source_url", "platform", "created_at", "model", "app_version"):
        assert f"\n{key}: " in text or text.startswith(f"{key}: ") or f"{key}: " in text
    assert '"会议 \\"纪要\\""' in text
    assert 'model: "deepseek/deepseek-v4-flash"' in text
    assert 'app_version: "0.3.0"' in text


def test_build_archive_frontmatter_missing_fields_are_empty() -> None:
    text = build_archive_frontmatter({"title": "只有标题"})
    assert 'title: "只有标题"' in text
    assert 'source_url: ""' in text
    assert 'platform: ""' in text
    assert 'created_at: ""' in text


def test_archived_note_keeps_existing_frontmatter() -> None:
    original = '---\ntitle: "已有"\n---\n\n# 正文\n'
    assert build_archived_note(original, {"title": "忽略"}) == original
    result = build_archived_note("# 新笔记\n\n正文", {"title": "新笔记"})
    assert result.startswith("---\n")
    assert "\n# 新笔记" in result
    assert build_archived_note("", {"title": "x"}) == ""


def test_archive_notes_exports_and_fills_metadata(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    outbox_dir = tmp_path / "outbox"
    archive_dir = tmp_path / "vault"
    archive_dir.mkdir()
    _write_note(
        notes_dir,
        "a1b2c3d4e5f60001.md",
        "# 会议纪要：桌面端评审\n\n- 要点一\n",
    )
    _write_note(
        notes_dir,
        "a1b2c3d4e5f60002.md",
        "# 从 heading 取标题\n\n正文。\n",
    )
    outbox_dir.mkdir()
    (outbox_dir / "complete-a1b2c3d4e5f60001.json").write_text(
        json.dumps(
            {
                "task_id": "a1b2c3d4e5f60001",
                "kind": "complete",
                "payload": {
                    "title": "outbox 里的标题",
                    "markdown": "# outbox\n",
                    "meta": {
                        "platform": "bilibili",
                        "url": "https://www.bilibili.com/video/BV1xx",
                    },
                },
                "created_at": "2026-10-24T10:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = archive_notes_to_dir(
        notes_dir,
        outbox_dir,
        archive_dir,
        model="deepseek/deepseek-v4-flash",
        app_version="0.3.0",
    )
    assert result["error"] == ""
    assert result["exported"] == 2
    assert result["failed"] == 0
    files = sorted(archive_dir.glob("*.md"))
    assert len(files) == 2
    first = (archive_dir / "a1b2c3d4e5f60001.md").read_text(encoding="utf-8")
    assert first.startswith("---\n")
    assert 'title: "outbox 里的标题"' in first
    assert 'platform: "bilibili"' in first
    assert 'source_url: "https://www.bilibili.com/video/BV1xx"' in first
    assert 'created_at: "2026-10-24T10:00:00Z"' in first
    assert 'model: "deepseek/deepseek-v4-flash"' in first
    assert 'app_version: "0.3.0"' in first
    assert "# 会议纪要：桌面端评审" in first
    second = (archive_dir / "a1b2c3d4e5f60002.md").read_text(encoding="utf-8")
    assert 'title: "从 heading 取标题"' in second


def test_archive_notes_is_idempotent(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    outbox_dir = tmp_path / "outbox"
    archive_dir = tmp_path / "vault"
    archive_dir.mkdir()
    _write_note(notes_dir, "a1b2c3d4e5f60001.md", "# 笔记\n")
    first = archive_notes_to_dir(notes_dir, outbox_dir, archive_dir)
    assert first["exported"] == 1
    second = archive_notes_to_dir(notes_dir, outbox_dir, archive_dir)
    assert second["exported"] == 0
    assert second["skipped"] == 1
    content = (archive_dir / "a1b2c3d4e5f60001.md").read_text(encoding="utf-8")
    assert content.count("app_version:") == 1


def test_archive_metadata_from_task_events(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    archive_dir = tmp_path / "vault"
    archive_dir.mkdir()
    _write_note(notes_dir, "feedface00000001.md", "# 任务事件元数据\n")
    events = [
        {
            "kind": "start",
            "task_id": "feedface00000001",
            "url": "https://example.com/v/9",
            "platform": "youtube",
            "ts": "2026-10-24T09:00:00",
        },
        {
            "kind": "completed",
            "task_id": "feedface00000001",
            "title": "任务事件标题",
            "ts": "2026-10-24T09:10:00",
        },
    ]
    result = archive_notes_to_dir(
        notes_dir,
        tmp_path / "outbox",
        archive_dir,
        task_events=events,
        app_version="0.3.0",
    )
    assert result["exported"] == 1
    content = (archive_dir / "feedface00000001.md").read_text(encoding="utf-8")
    assert 'title: "任务事件标题"' in content
    assert 'platform: "youtube"' in content
    assert 'source_url: "https://example.com/v/9"' in content


def test_archive_custom_template_for_filename(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    archive_dir = tmp_path / "vault"
    archive_dir.mkdir()
    _write_note(notes_dir, "a1b2c3d4e5f60001.md", "# 我的标题\n")
    result = archive_notes_to_dir(
        notes_dir,
        tmp_path / "outbox",
        archive_dir,
        template="{date}-{title}",
    )
    assert result["exported"] == 1
    files = list(archive_dir.glob("*.md"))
    assert len(files) == 1
    assert "我的标题" in files[0].name


def test_archive_errors_for_missing_or_invalid_dir(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_note(notes_dir, "a1b2c3d4e5f60001.md", "# 笔记\n")
    outbox_dir = tmp_path / "outbox"

    empty = archive_notes_to_dir(notes_dir, outbox_dir, "")
    assert "没有选择归档目录" in empty["error"]
    assert empty["exported"] == 0

    missing = archive_notes_to_dir(notes_dir, outbox_dir, tmp_path / "no-such-dir")
    assert "不存在" in missing["error"]

    not_dir = tmp_path / "file.txt"
    not_dir.write_text("x", encoding="utf-8")
    bad = archive_notes_to_dir(notes_dir, outbox_dir, not_dir)
    assert "不是文件夹" in bad["error"]

    relative = archive_notes_to_dir(notes_dir, outbox_dir, "relative-dir")
    assert "绝对路径" in relative["error"]

    invalid = archive_notes_to_dir(notes_dir, outbox_dir, "C:\\\\bad<>dir")
    assert "非法字符" in invalid["error"]


def test_archive_empty_notes_dir_returns_zero(tmp_path: Path) -> None:
    archive_dir = tmp_path / "vault"
    archive_dir.mkdir()
    result = archive_notes_to_dir(
        tmp_path / "empty-notes", tmp_path / "empty-outbox", archive_dir
    )
    assert result["exported"] == 0
    assert result["error"] == ""
    assert result["total"] == 0

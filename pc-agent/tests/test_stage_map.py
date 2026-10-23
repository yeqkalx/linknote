"""BiliNote 状态 → 契约 stage 映射，以及失败原因中文化。"""

from __future__ import annotations

import pytest

from linknote_agent.stage_map import (
    FAIL_MESSAGE_MAX_CHARS,
    MESSAGE_MAX_CHARS,
    format_elapsed,
    is_terminal_status,
    map_status,
    normalize_status,
    translate_failure,
)


@pytest.mark.parametrize(
    ("backend_status", "stage", "progress"),
    [
        ("PENDING", "parsing", 2),
        ("PARSING", "parsing", 5),
        ("DOWNLOADING", "downloading", 20),
        ("TRANSCRIBING", "transcribing", 45),
        ("SUMMARIZING", "summarizing", 70),
        ("FORMATTING", "formatting", 85),
        ("SAVING", "saving", 95),
    ],
)
def test_status_mapping(backend_status, stage, progress):
    update = map_status(backend_status, elapsed_seconds=65)
    assert update is not None
    assert update.stage == stage
    assert update.progress == progress
    assert normalize_status(backend_status) == backend_status
    assert "已用 1 分 5 秒" in update.message
    assert len(update.message) <= MESSAGE_MAX_CHARS


def test_mapping_is_lowercase_tolerant():
    update = map_status("transcribing", elapsed_seconds=0)
    assert update is not None
    assert update.stage == "transcribing"


def test_progress_monotonic_across_stages():
    statuses = [
        "PENDING",
        "PARSING",
        "DOWNLOADING",
        "TRANSCRIBING",
        "SUMMARIZING",
        "FORMATTING",
        "SAVING",
    ]
    progresses = [map_status(s, 10).progress for s in statuses]
    assert progresses == sorted(progresses)
    assert len(set(progresses)) == len(progresses)
    stages = {map_status(s, 10).stage for s in statuses}
    assert stages <= {
        "parsing",
        "downloading",
        "transcribing",
        "summarizing",
        "formatting",
        "saving",
    }


def test_terminal_and_unknown_statuses():
    assert is_terminal_status("SUCCESS") is True
    assert is_terminal_status("failed") is True
    assert is_terminal_status("TRANSCRIBING") is False
    assert map_status("SUCCESS") is None
    assert map_status("FAILED") is None
    assert map_status("SOMETHING_NEW") is None


@pytest.mark.parametrize(
    ("reason", "expected_code", "hint_keyword"),
    [
        ("401 invalid api key", "model_auth_error", "API Key"),
        ("模型账户余额不足", "quota_exceeded", "余额"),
        ("quota exceeded", "quota_exceeded", "余额"),
        ("转写模型尚未下载就绪", "transcriber_not_ready", "转写"),
        ("暂不支持该平台", "unsupported_platform", "暂不支持"),
        ("磁盘空间不足", "disk_full", "磁盘"),
        ("some random backend error", "bilinote_error", "BiliNote 生成失败"),
        ("", "bilinote_error", "没有返回失败原因"),
    ],
)
def test_translate_failure(reason, expected_code, hint_keyword):
    failure = translate_failure(reason)
    assert failure.code == expected_code
    assert hint_keyword in failure.message
    if reason:
        assert reason in failure.message
    assert len(failure.message) <= FAIL_MESSAGE_MAX_CHARS


def test_translate_failure_truncates_long_reason():
    failure = translate_failure("x" * 5000)
    assert len(failure.message) <= FAIL_MESSAGE_MAX_CHARS


def test_format_elapsed():
    assert format_elapsed(0) == "0 秒"
    assert format_elapsed(59) == "59 秒"
    assert format_elapsed(65) == "1 分 5 秒"
    assert format_elapsed(3665) == "1 小时 1 分"

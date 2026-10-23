"""BiliNote 状态 → Relay 契约 stage/progress 的映射，以及失败原因中文化。

契约（docs/api-contract.md §7.2）里 Agent 能回传的 stage 只有：
``parsing | downloading | transcribing | summarizing | formatting | saving``；
而 BiliNote 后端的任务状态是 ``PENDING/PARSING/DOWNLOADING/TRANSCRIBING/
SUMMARIZING/FORMATTING/SAVING``（成功/失败是终态，通过 complete/fail 回传）。

BiliNote 的 ``PENDING`` 表示任务已进入后端串行队列但还没开始处理。
契约里 Agent 没有对应 stage，这里选择最接近的 ``parsing`` 并给出「等待 BiliNote
开始处理」的 message，语义上不丢信息，也不违反契约的 stage 枚举。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: 契约 image/App 文案里的 stage 中文名（docs/api-contract.md §8）
STAGE_LABELS = {
    "pending": "排队中",
    "parsing": "正在解析链接",
    "downloading": "正在下载音视频",
    "transcribing": "正在语音转写",
    "summarizing": "AI 正在总结",
    "formatting": "正在整理格式",
    "saving": "正在保存",
    "done": "已完成",
    "failed": "失败",
}

#: BiliNote 后端状态 → (契约 stage, 默认 progress)
_STATUS_TO_STAGE: dict[str, tuple[str, int]] = {
    "PENDING": ("parsing", 2),
    "PARSING": ("parsing", 5),
    "DOWNLOADING": ("downloading", 20),
    "TRANSCRIBING": ("transcribing", 45),
    "SUMMARIZING": ("summarizing", 70),
    "FORMATTING": ("formatting", 85),
    "SAVING": ("saving", 95),
}

#: 每种 BiliNote 状态在合同 message 里的中文描述（不含耗时）
_STATUS_VERB: dict[str, str] = {
    "PENDING": "已领取任务，等待 BiliNote 开始处理",
    "PARSING": "正在解析链接",
    "DOWNLOADING": "正在下载音视频",
    "TRANSCRIBING": "正在语音转写",
    "SUMMARIZING": "AI 正在总结内容",
    "FORMATTING": "正在整理格式",
    "SAVING": "正在保存笔记",
}

TERMINAL_BACKEND_STATUSES = frozenset({"SUCCESS", "FAILED"})

#: 契约 message 上限 200 字符（docs/api-contract.md §7.2）
MESSAGE_MAX_CHARS = 200
#: 契约 fail message 上限 1000 字符（§7.4）
FAIL_MESSAGE_MAX_CHARS = 1000


@dataclass(frozen=True)
class ProgressUpdate:
    """一次待回传的进度。"""

    backend_status: str
    stage: str
    progress: int
    message: str


def normalize_status(status: object) -> str:
    return str(status or "").strip().upper()


def is_terminal_status(status: object) -> bool:
    return normalize_status(status) in TERMINAL_BACKEND_STATUSES


def map_status(backend_status: object, elapsed_seconds: float = 0.0) -> Optional[ProgressUpdate]:
    """BiliNote 状态 → 契约进度；终态（SUCCESS/FAILED）或未知状态返回 None。

    未知状态不当作失败（BiliNote 可能新增状态），Runner 会按「正在处理」继续等，
    这里返回 None 表示不需要回传进度。
    """
    status = normalize_status(backend_status)
    mapped = _STATUS_TO_STAGE.get(status)
    if mapped is None:
        return None
    stage, progress = mapped
    message = _build_message(status, elapsed_seconds)
    return ProgressUpdate(backend_status=status, stage=stage, progress=progress, message=message)


def _build_message(status: str, elapsed_seconds: float) -> str:
    verb = _STATUS_VERB.get(status, STAGE_LABELS.get(status.lower(), status))
    if elapsed_seconds > 0:
        message = f"{verb}（已用 {format_elapsed(elapsed_seconds)}）"
    else:
        message = verb
    return truncate_message(message)


def format_elapsed(seconds: float) -> str:
    total = int(max(0, seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def truncate_message(message: str, limit: int = MESSAGE_MAX_CHARS) -> str:
    text = (message or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


# ---------------------------------------------------------------- 失败原因


@dataclass(frozen=True)
class FailureInfo:
    """回传 Relay ``/fail`` 的 code + 给用户看的中文 message。"""

    code: str
    message: str


# (匹配关键字（小写）, error_code, 给用户的修复建议)
_FAILURE_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("401", "invalid_api_key", "api key", "authentication", "unauthorized", "未配置模型"),
        "model_auth_error",
        "模型 API Key 失效或未配置：请在 BiliNote → 设置 → 模型服务商里填入有效 Key。",
    ),
    (
        ("余额", "额度", "quota", "insufficient", "balance", "billing", "payment"),
        "quota_exceeded",
        "模型账户余额或额度不足：请充值或更换一个服务商/模型。",
    ),
    (
        ("尚未下载就绪", "transcriber_model_not_ready", "whisper", "模型未就绪", "转写模型"),
        "transcriber_not_ready",
        "语音转写模型尚未就绪：请打开 BiliNote → 设置 → 音频转写配置，"
        "下载对应的 Whisper 模型，或改用在线转写引擎。",
    ),
    (
        ("暂不支持", "unsupported", "不支持的平台", "invalid_url", "无法解析链接", "链接无效"),
        "unsupported_platform",
        "BiliNote 暂不支持这个链接/平台：请确认链接来自 B站、YouTube、抖音或快手且视频公开可访问。",
    ),
    (
        ("no space", "磁盘", "disk full", "磁盘空间不足"),
        "disk_full",
        "电脑磁盘空间不足：请清理 BiliNote 的临时目录后重试。",
    ),
)

_GENERIC_PREFIX = "BiliNote 生成失败"


def translate_failure(reason: str, *, fallback_code: str = "bilinote_error") -> FailureInfo:
    """把 BiliNote 的报错翻译成中文可读 message（附原始错误，便于排障）。"""
    raw = str(reason or "").strip()
    low = raw.lower()

    for keywords, code, hint in _FAILURE_RULES:
        if any(k in low for k in keywords):
            return FailureInfo(code=code, message=_compose(hint, raw))

    if not raw:
        return FailureInfo(
            code=fallback_code,
            message=f"{_GENERIC_PREFIX}：BiliNote 没有返回失败原因，请到 BiliNote 界面查看任务详情。",
        )
    return FailureInfo(code=fallback_code, message=_compose(f"{_GENERIC_PREFIX}。", raw))


def _compose(hint: str, raw: str) -> str:
    message = f"{hint}\n原始错误：{raw}" if raw else hint
    if len(message) > FAIL_MESSAGE_MAX_CHARS:
        message = message[: FAIL_MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return message


__all__ = [
    "FAIL_MESSAGE_MAX_CHARS",
    "FailureInfo",
    "MESSAGE_MAX_CHARS",
    "ProgressUpdate",
    "STAGE_LABELS",
    "TERMINAL_BACKEND_STATUSES",
    "format_elapsed",
    "is_terminal_status",
    "map_status",
    "normalize_status",
    "translate_failure",
    "truncate_message",
]

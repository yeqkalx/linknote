"""链接处理：提取 URL、识别平台、展开短链。

平台判定逻辑与 BiliNote 后端 ``app/validators/video_url_validator.py`` 保持一致，
保证 Relay 接受的链接这里也接受；Agent 侧再校验一次，作为白名单的双保险。
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import httpx

# 匹配正文里的第一个 URL（中英文标点作为边界，避免把中文句号吃进去）
URL_RE = re.compile(r"https?://[^\s<>\"'“”‘’（）()\[\]{}，。；;、]+", re.IGNORECASE)

# 分享文案里经常没有 http:// 前缀（被聊天软件自动识别成卡片），按已知平台域名再兜一层。
# 只白名单已知平台域名，不做通用裸域名匹配，免得出把普通文字里的 xxx.com 当链接。
BARE_PLATFORM_RE = re.compile(
    r"(?:www\.)?(?:"
    r"bilibili\.com/video/[A-Za-z0-9]+"
    r"|b23\.tv/[A-Za-z0-9]+"
    r"|youtube\.com/watch\?v=[\w-]+"
    r"|youtube\.com/shorts/[\w-]+"
    r"|youtu\.be/[\w-]+"
    r"|douyin\.com/[\w-]+"
    r"|v\.douyin\.com/[\w-]+"
    r"|kuaishou\.com/[\w-]+"
    r"|v\.kuaishou\.com/[\w-]+"
    r")"
    r"(?:[/?#][^\s<>\"'“”‘’（）()\[\]{}，。；;、]*)?",
    re.IGNORECASE,
)

_TRAILING_JUNK = ".,;:!?)>】》」』、。，；：！？"

SHORT_LINK_HOSTS = {
    "b23.tv": "bilibili",
    "v.douyin.com": "douyin",
    "v.kuaishou.com": "kuaishou",
    "youtu.be": "youtube",
}

PLATFORM_LABELS = {
    "bilibili": "B站",
    "youtube": "YouTube",
    "douyin": "抖音",
    "kuaishou": "快手",
}

SUPPORTED_HINT = "B站（bilibili.com / b23.tv）、YouTube、抖音、快手"

SUPPORTED_PLATFORMS = frozenset(PLATFORM_LABELS)


def extract_first_url(text: str) -> Optional[str]:
    """从任意文本里取出第一个视频链接，并保证返回的是带协议的完整 URL。

    先找 ``http(s)://`` 开头的；找不到再按已知平台域名兜底并补上 ``https://``。
    """
    if not text:
        return None

    m = URL_RE.search(text)
    if m:
        return m.group(0).rstrip(_TRAILING_JUNK)

    m = BARE_PLATFORM_RE.search(text)
    if m:
        return ("https://" + m.group(0)).rstrip(_TRAILING_JUNK)

    return None


def host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:
        return ""


def detect_platform(url: str) -> Optional[str]:
    """按域名识别平台，识别不了返回 None。"""
    host = host_of(url)
    if not host:
        return None

    if host in SHORT_LINK_HOSTS:
        return SHORT_LINK_HOSTS[host]

    bare = host[4:] if host.startswith("www.") else host
    if bare == "bilibili.com" or bare.endswith(".bilibili.com"):
        return "bilibili"
    if bare == "youtube.com" or bare.endswith(".youtube.com") or bare == "youtu.be":
        return "youtube"
    if "douyin" in bare or "iesdouyin" in bare:
        return "douyin"
    if "kuaishou" in bare:
        return "kuaishou"
    return None


def ensure_supported_url(url: str) -> tuple[str, str]:
    """校验并返回 ``(平台, 链接)``；不支持时抛 ``UnsupportedUrlError``。

    Relay 已经做过一次校验，这里是 Agent 侧的双保险（也防止 Relay 被改坏时
    把任意 URL 交给本机 BiliNote）。
    """
    candidate = extract_first_url(url) or (url or "").strip()
    if not candidate:
        raise UnsupportedUrlError("链接为空")
    platform = detect_platform(candidate)
    if platform is None:
        raise UnsupportedUrlError(
            f"不支持的链接：{candidate}（只支持 {SUPPORTED_HINT}）"
        )
    return platform, candidate


class UnsupportedUrlError(ValueError):
    """URL 不在白名单平台内。"""


async def resolve_short_link(
    url: str,
    timeout: float = 8.0,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """把短链展开成真实地址（主要是 b23.tv），失败时原样返回。

    展开后 BiliNote 的 ``extract_video_id`` 才能拿到正确的 BV 号。
    ``transport`` 仅用于测试注入（httpx.MockTransport）。
    """
    host = host_of(url)
    if host not in ("b23.tv", "v.douyin.com", "v.kuaishou.com"):
        return url
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport
        ) as client:
            resp = await client.head(url, headers={"User-Agent": "Mozilla/5.0"})
            final = str(resp.url)
            # HEAD 被拒时用 GET 兜底
            if final.rstrip("/") == url.rstrip("/"):
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                final = str(resp.url)
            return final or url
    except Exception:
        return url


__all__ = [
    "PLATFORM_LABELS",
    "SUPPORTED_HINT",
    "SUPPORTED_PLATFORMS",
    "UnsupportedUrlError",
    "detect_platform",
    "ensure_supported_url",
    "extract_first_url",
    "host_of",
    "resolve_short_link",
]

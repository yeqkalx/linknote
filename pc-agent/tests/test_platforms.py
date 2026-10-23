"""链接提取 / 平台识别 / 短链展开。"""

from __future__ import annotations

import httpx
import pytest

from linknote_agent.platforms import (
    UnsupportedUrlError,
    detect_platform,
    ensure_supported_url,
    extract_first_url,
    resolve_short_link,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("看这个 https://www.bilibili.com/video/BV1xx411c7mD?p=1 不错", "https://www.bilibili.com/video/BV1xx411c7mD?p=1"),
        ("【标题】 https://youtu.be/dQw4w9WgXcQ。", "https://youtu.be/dQw4w9WgXcQ"),
        ("https://v.douyin.com/abcdef/ 复制此链接", "https://v.douyin.com/abcdef/"),
        ("纯文本没有链接", None),
        ("b23.tv/AbCdEf", "https://b23.tv/AbCdEf"),
        ("分享 https://www.kuaishou.com/short-video/3xabc，快看", "https://www.kuaishou.com/short-video/3xabc"),
        ("", None),
    ],
)
def test_extract_first_url(text, expected):
    assert extract_first_url(text) == expected


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.bilibili.com/video/BV1xx", "bilibili"),
        ("https://b23.tv/abc", "bilibili"),
        ("https://m.bilibili.com/video/BV1xx", "bilibili"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtube.com/shorts/abc", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://www.douyin.com/video/123", "douyin"),
        ("https://v.douyin.com/abc/", "douyin"),
        ("https://www.kuaishou.com/short-video/3xabc", "kuaishou"),
        ("https://v.kuaishou.com/abc", "kuaishou"),
        ("https://example.com/video/1", None),
        ("http://127.0.0.1:8787/x", None),
        ("not-a-url", None),
    ],
)
def test_detect_platform(url, platform):
    assert detect_platform(url) == platform


def test_ensure_supported_url_ok():
    platform, url = ensure_supported_url(
        "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333"
    )
    assert platform == "bilibili"
    assert url.startswith("https://www.bilibili.com/video/")


def test_ensure_supported_url_rejects_other_sites():
    with pytest.raises(UnsupportedUrlError) as excinfo:
        ensure_supported_url("https://example.com/video/1")
    assert "example.com" in str(excinfo.value)
    assert "B站" in str(excinfo.value)


def test_ensure_supported_url_rejects_empty():
    with pytest.raises(UnsupportedUrlError):
        ensure_supported_url("")


async def test_resolve_short_link_non_short_does_not_network():
    def handler(request: httpx.Request):  # pragma: no cover - 不应被调用
        raise AssertionError("非短链不应该发网络请求")

    url = "https://www.bilibili.com/video/BV1xx411c7mD"
    result = await resolve_short_link(url, transport=httpx.MockTransport(handler))
    assert result == url


async def test_resolve_short_link_follows_redirect():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "b23.tv":
            return httpx.Response(302, headers={"Location": "https://www.bilibili.com/video/BV1xx411c7mD"})
        return httpx.Response(200, text="ok")

    result = await resolve_short_link(
        "https://b23.tv/AbCdEf", transport=httpx.MockTransport(handler)
    )
    assert result == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert len(seen) >= 2


async def test_resolve_short_link_failure_returns_original():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("网络不通", request=request)

    url = "https://b23.tv/AbCdEf"
    assert await resolve_short_link(url, transport=httpx.MockTransport(handler)) == url

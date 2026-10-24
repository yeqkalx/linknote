"""LinkNote Relay API v1 客户端（httpx 异步）。

严格按 ``docs/api-contract.md``：
- Base URL 形如 ``https://relay.xykong.xyz`` / ``http://127.0.0.1:8787``，路径统一 ``/api/v1``；
- ``Authorization: Bearer <agent_token>``；
- 响应统一 ``{"ok": true, "data": ...}`` / ``{"ok": false, "error": {"code", "message"}}``。

所有 HTTP 错误都映射成 :class:`RelayError`（message 是可以直接展示给用户的中文短句，
``retriable`` 表示 Agent 是否应该稍后重试，``is_auth_error`` 表示需要重新配对）。
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from .logging_setup import get_logger

logger = get_logger("relay")

API_PREFIX = "/api/v1"

#: 契约 §7.2：Agent 可回传的 stage 白名单
PROGRESS_STAGES = frozenset(
    {"parsing", "downloading", "transcribing", "summarizing", "formatting", "saving"}
)

#: 契约 §7.3：Markdown ≤1.5 MB（UTF-8 字节）
MARKDOWN_MAX_BYTES = int(1.5 * 1024 * 1024)
TITLE_MAX_CHARS = 200
FAIL_MESSAGE_MAX_CHARS = 1000
PROGRESS_MESSAGE_MAX_CHARS = 200

_HTTP_ERROR_MESSAGES = {
    400: "请求参数不被 Relay 接受",
    401: "配对已失效或未配对",
    403: "Relay 拒绝了请求",
    404: "内容不存在或不属于本机配对",
    409: "任务状态冲突",
    413: "笔记超过 Relay 的 1.5 MB 限制",
    429: "Relay 请求过于频繁",
}


class RelayError(Exception):
    """Relay 调用失败（网络 / HTTP / 协议层）。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        status_code: Optional[int] = None,
        retriable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.retriable = retriable

    @property
    def is_auth_error(self) -> bool:
        return self.status_code == 401 or self.code == "unauthorized"

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.message


class _NotJSON(Exception):
    pass


class RelayClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "LinkNote-Agent/0.1",
    ):
        self.base_url = _normalize_base_url(base_url)
        self.timeout = timeout
        headers = {"User-Agent": user_agent, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers=headers,
            transport=transport,
        )

    # ---------------------------------------------------------------- 基础

    def set_token(self, token: str) -> None:
        """配对创建成功后补上 token（后续请求需要 Agent 认证）。"""
        if token:
            self._client.headers["Authorization"] = f"Bearer {token}"
        else:
            self._client.headers.pop("Authorization", None)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{API_PREFIX}{path}"
        try:
            resp = await self._client.request(
                method, url, json=json, timeout=timeout if timeout is not None else self.timeout
            )
        except httpx.TimeoutException as exc:
            raise RelayError(
                f"连接 Relay 超时（{self.base_url}）：请检查网络或 Relay 地址。",
                code="timeout",
                retriable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise RelayError(
                f"无法连接 Relay（{self.base_url}）：{exc}",
                code="network_error",
                retriable=True,
            ) from exc

        payload: Any
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            if resp.status_code >= 400:
                raise _map_http_error(resp.status_code, None) from exc
            raise RelayError(
                f"Relay 返回了非 JSON 响应（HTTP {resp.status_code}）：{resp.text[:200]}",
                code="bad_response",
                status_code=resp.status_code,
                retriable=resp.status_code >= 500,
            ) from exc

        if not isinstance(payload, dict):
            raise RelayError(
                "Relay 返回格式异常（应为 JSON 对象）。",
                code="bad_response",
                status_code=resp.status_code,
                retriable=resp.status_code >= 500,
            )

        ok = payload.get("ok")
        error = payload.get("error")
        if ok is False or (isinstance(error, dict) and error):
            if resp.status_code >= 400:
                raise _map_http_error(resp.status_code, error)
            raise _map_http_error(400, error)

        if resp.status_code >= 400:
            raise _map_http_error(resp.status_code, error if isinstance(error, dict) else None)

        if ok is True:
            return payload.get("data")
        # 契约要求 ok 字段；容错处理极少数只返回 data 的代理/旧版本。
        return payload.get("data") if "data" in payload else payload

    # ---------------------------------------------------------------- 健康检查

    async def health(self) -> dict[str, Any]:
        data = await self._request("GET", "/health", timeout=5.0)
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- 配对

    async def create_pairing(
        self,
        agent_name: str = "我的电脑",
        platform: str = "windows",
        version: str = "",
    ) -> dict[str, Any]:
        """契约 §3.1：创建配对，返回 agent_id/pairing_id/code/expires_at/agent_token。"""
        body: dict[str, Any] = {"agent_name": agent_name, "platform": platform}
        if version:
            body["version"] = version
        data = await self._request("POST", "/pairings", json=body)
        if not isinstance(data, dict):
            raise RelayError("Relay 创建配对返回格式异常。", code="bad_response")
        missing = [k for k in ("pairing_id", "code", "agent_token") if not data.get(k)]
        if missing:
            raise RelayError(
                f"Relay 创建配对响应缺少字段：{', '.join(missing)}",
                code="bad_response",
            )
        return data

    async def get_pairing(self, pairing_id: str) -> dict[str, Any]:
        """契约 §3.2：查询配对状态（pending / claimed / expired）。"""
        data = await self._request("GET", f"/pairings/{quote(pairing_id, safe='')}")
        return data if isinstance(data, dict) else {}

    async def create_extra_pairing(self) -> dict[str, Any]:
        """契约 §3.4：给第二台手机追加配对码（复用当前 Agent）。"""
        data = await self._request("POST", "/agent/pairings", json={})
        return data if isinstance(data, dict) else {}

    async def cancel_pairing(self, pairing_id: str) -> None:
        """契约 §3.5：取消配对。"""
        await self._request("POST", f"/pairings/{quote(pairing_id, safe='')}/cancel", json={})

    # ---------------------------------------------------------------- Agent 自身

    async def me(self) -> dict[str, Any]:
        """契约 §4.1：Agent 信息 + 设备列表 + 队列长度。"""
        data = await self._request("GET", "/agent/me")
        return data if isinstance(data, dict) else {}

    async def heartbeat(self, version: str, busy: bool) -> dict[str, Any]:
        """契约 §4.2：每 30 秒心跳。"""
        data = await self._request(
            "POST", "/agent/heartbeat", json={"version": version, "busy": bool(busy)}
        )
        return data if isinstance(data, dict) else {}

    async def revoke_device(self, device_id: str) -> None:
        """契约 §4.3：撤销某台手机（其 device token 立即失效）。"""
        await self._request("DELETE", f"/agent/devices/{quote(device_id, safe='')}")

    async def create_agent_device(self, name: str, platform: str) -> dict[str, Any]:
        """v0.4：为本机 Bot 入口签发一个专用 device token（Agent 认证）。

        用于 Telegram/QQ bot 以自己的身份调 Relay 建任务/查任务，不碰手机 token。
        返回 ``{"device": {...}, "token": "dev_..."}``；token 只出现这一次。
        """
        data = await self._request(
            "POST",
            "/agent/devices",
            json={"name": (name or "Bot")[:40], "platform": platform},
        )
        if not isinstance(data, dict):
            raise RelayError("Relay 签发 Bot 设备返回格式异常。", code="bad_response")
        device = data.get("device")
        token = str(data.get("token") or "")
        if not isinstance(device, dict) or not token:
            raise RelayError(
                "Relay 签发 Bot 设备响应缺少 device/token（可能尚未部署 v0.4 端点）。",
                code="bad_response",
            )
        return {"device": device, "token": token}

    # ---------------------------------------------------------------- 任务

    async def next_task(self) -> Optional[dict[str, Any]]:
        """契约 §7.1：原子领取下一个任务；没有任务时返回 None（服务端返回 task=null）。"""
        data = await self._request("GET", "/agent/tasks/next")
        if not isinstance(data, dict):
            return None
        task = data.get("task")
        return task if isinstance(task, dict) else None

    async def progress(
        self,
        task_id: str,
        stage: str,
        progress: Optional[int] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        """契约 §7.2：回传进度（同时顺延 30 分钟租约）。"""
        if stage not in PROGRESS_STAGES:
            raise ValueError(
                f"非法 stage={stage!r}，契约只允许：{sorted(PROGRESS_STAGES)}"
            )
        body: dict[str, Any] = {"stage": stage}
        if progress is not None:
            body["progress"] = max(0, min(100, int(progress)))
        if message:
            body["message"] = _truncate(str(message), PROGRESS_MESSAGE_MAX_CHARS)
        data = await self._request(
            "POST", f"/agent/tasks/{quote(task_id, safe='')}/progress", json=body
        )
        return data if isinstance(data, dict) else {}

    async def complete(
        self,
        task_id: str,
        title: str,
        markdown: str,
        *,
        duration_seconds: Optional[int] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """契约 §7.3：任务成功，回传 Markdown（服务端幂等）。"""
        md = markdown or ""
        if not md.strip():
            raise RelayError("笔记内容为空，不能回传。", code="empty_markdown")
        size = len(md.encode("utf-8"))
        if size > MARKDOWN_MAX_BYTES:
            raise RelayError(
                f"笔记超过 1.5 MB 限制（{size} 字节），Relay 会拒绝接收。",
                code="markdown_too_large",
                status_code=413,
            )
        body: dict[str, Any] = {
            "title": _truncate((title or "").strip() or "未命名笔记", TITLE_MAX_CHARS),
            "markdown": md,
        }
        if duration_seconds is not None:
            body["duration_seconds"] = int(duration_seconds)
        if meta:
            body["meta"] = meta
        data = await self._request(
            "POST", f"/agent/tasks/{quote(task_id, safe='')}/complete", json=body
        )
        return data if isinstance(data, dict) else {}

    async def fail(self, task_id: str, code: str, message: str) -> dict[str, Any]:
        """契约 §7.4：任务失败（message 会直接展示给手机用户）。"""
        body = {
            "code": (code or "bilinote_error")[:80],
            "message": _truncate(message or "生成失败", FAIL_MESSAGE_MAX_CHARS),
        }
        data = await self._request(
            "POST", f"/agent/tasks/{quote(task_id, safe='')}/fail", json=body
        )
        return data if isinstance(data, dict) else {}

    async def task_heartbeat(self, task_id: str) -> dict[str, Any]:
        """契约 §7.5：长任务租约续期（每 5 分钟）。"""
        data = await self._request(
            "POST", f"/agent/tasks/{quote(task_id, safe='')}/heartbeat", json={}
        )
        return data if isinstance(data, dict) else {}

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """契约 §7.6：查询自己的任务摘要（outbox 补传前自检）。"""
        data = await self._request("GET", f"/agent/tasks/{quote(task_id, safe='')}")
        if isinstance(data, dict):
            task = data.get("task")
            if isinstance(task, dict):
                return task
            return data
        return {}


# ---------------------------------------------------------------- 工具


def _normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    # 容忍用户把 /api/v1 也写进配置
    for suffix in ("/api/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    if not url:
        raise RelayError("Relay 地址为空，请用 --relay 指定，例如 https://relay.xykong.xyz")
    if not url.startswith(("http://", "https://")):
        raise RelayError(f"Relay 地址必须以 http:// 或 https:// 开头：{base_url}")
    return url


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _map_http_error(status: int, error: Optional[dict[str, Any]]) -> RelayError:
    """HTTP 状态码 + 契约 error 对象 → RelayError（中文可读消息）。"""
    error = error or {}
    code = str(error.get("code") or _default_error_code(status))
    message = str(error.get("message") or "").strip()

    if status == 401:
        message = "配对已失效或未配对（HTTP 401）：请重新运行 pair 命令配对。"
    elif status == 429:
        message = message or "Relay 请求过于频繁（HTTP 429），Agent 会稍后自动重试。"
    elif status >= 500:
        message = f"Relay 服务端异常（HTTP {status}），Agent 会稍后自动重试。"
    elif not message:
        base = _HTTP_ERROR_MESSAGES.get(status, f"Relay 请求失败（HTTP {status}）")
        message = f"{base}（{code}）。"

    retriable = status == 429 or status >= 500
    return RelayError(
        message,
        code=code,
        status_code=status,
        retriable=retriable,
    )


def _default_error_code(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "invalid_state",
        413: "markdown_too_large",
        429: "rate_limited",
        500: "internal",
    }.get(status, f"http_{status}")


__all__ = [
    "API_PREFIX",
    "MARKDOWN_MAX_BYTES",
    "PROGRESS_STAGES",
    "RelayClient",
    "RelayError",
]

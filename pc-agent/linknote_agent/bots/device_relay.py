"""Bot 专用 Relay REST 客户端：用 bot device token（``dev_...``）建任务/查任务。

Bot 不碰手机 token，也不使用 agent token：
- Runner 先用 agent 认证的 :class:`~linknote_agent.relay_client.RelayClient` 调
  ``POST /api/v1/agent/devices`` 签发一个 bot device token；
- 之后本模块用该 device token 调 ``POST /tasks``、``GET /tasks/{id}``、``GET /tasks``。
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from ..logging_setup import get_logger

logger = get_logger("bots.relay")


class BotRelayError(Exception):
    """Bot device 调 Relay 失败；错误信息可直接给用户/日志。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: str = "unknown",
        retriable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.retriable = retriable

    @property
    def is_auth_error(self) -> bool:
        return self.status_code == 401 or self.code == "unauthorized"


def _normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


class BotRelayClient:
    def __init__(
        self,
        base_url: str,
        device_token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "LinkNote-Agent-Bot/0.4",
    ):
        self.base_url = _normalize_base_url(base_url)
        self.device_token = device_token
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {device_token}",
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- 底层

    async def _request(self, method: str, path: str, *, json: Any = None) -> Any:
        try:
            resp = await self._client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise BotRelayError("连接 Relay 超时，请稍后重试", code="timeout", retriable=True) from exc
        except httpx.HTTPError as exc:
            raise BotRelayError(
                f"无法连接 Relay：{exc}", code="network_error", retriable=True
            ) from exc

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BotRelayError(
                f"Relay 返回非 JSON（HTTP {resp.status_code}）",
                status_code=resp.status_code,
                code="bad_response",
                retriable=resp.status_code >= 500,
            ) from exc

        if not isinstance(payload, dict):
            raise BotRelayError("Relay 返回格式异常", code="bad_response")

        if payload.get("ok") is False or resp.status_code >= 400:
            error = payload.get("error")
            error = error if isinstance(error, dict) else {}
            code = str(error.get("code") or f"http_{resp.status_code}")
            message = str(error.get("message") or "").strip()
            if resp.status_code == 401:
                message = "Bot 设备凭据失效（401）：将自动重新签发 device token"
            elif resp.status_code == 429:
                message = message or "Relay 限流，稍后重试"
            elif not message:
                message = f"Relay 请求失败（HTTP {resp.status_code}）"
            raise BotRelayError(
                message,
                status_code=resp.status_code,
                code=code,
                retriable=resp.status_code == 429 or resp.status_code >= 500,
            )
        return payload.get("data")

    # ---------------------------------------------------------------- 任务

    async def create_task(self, url: str, client_key: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"url": url}
        if client_key:
            body["client_key"] = client_key
        data = await self._request("POST", "/api/v1/tasks", json=body)
        task = data.get("task") if isinstance(data, dict) else None
        if not isinstance(task, dict) or not task.get("id"):
            raise BotRelayError("Relay 没有返回任务 id", code="bad_response")
        return task

    async def get_task(self, task_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/api/v1/tasks/{quote(task_id, safe='')}")
        task = data.get("task") if isinstance(data, dict) else None
        if not isinstance(task, dict):
            raise BotRelayError("Relay 没有返回任务详情", code="bad_response")
        return task

    async def list_tasks(self, limit: int = 5) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/api/v1/tasks?limit={max(1, min(50, int(limit)))}")
        items = data.get("items") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    async def retry_task(self, task_id: str) -> dict[str, Any]:
        data = await self._request(
            "POST", f"/api/v1/tasks/{quote(task_id, safe='')}/retry", json={}
        )
        task = data.get("task") if isinstance(data, dict) else None
        if not isinstance(task, dict) or not task.get("id"):
            raise BotRelayError("Relay 没有返回重试任务 id", code="bad_response")
        return task


__all__ = ["BotRelayClient", "BotRelayError"]

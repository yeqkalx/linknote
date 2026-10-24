"""BiliNote 本机后端 HTTP 客户端（httpx 异步）。

只通过 HTTP 接口调用本机 BiliNote（默认 ``http://127.0.0.1:8483/api``），
不 import 后端代码，因此对 PyInstaller 打包安装版同样有效。

BiliNote 的响应统一是 ``ResponseWrapper``：

成功：``{ "code": 0, "msg": "success", "data": {...} }``
失败：``{ "code": <非 0>, "msg": "错误说明", "data": null }``
（任务本身失败时 ``/task_status`` 可能返回 code=500，这里转换成统一状态结构，
让调用方能拿到失败原因而不是抛异常。）
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from .logging_setup import get_logger

logger = get_logger("bilinote")

#: 与 BiliNote 前端（NoteForm）和 wecom_bot 保持一致的默认生成参数。
DEFAULT_BODY: dict[str, Any] = {
    "format": [],
    "video_understanding": False,
    "video_interval": 0,
    "grid_size": [],
}


class BiliNoteError(Exception):
    """后端返回 code != 0，或网络/协议层错误。"""

    def __init__(self, message: str, code: int = -1):
        super().__init__(message)
        self.message = message
        self.code = code


class BiliNoteClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "LinkNote-Agent/0.1",
    ):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": user_agent},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- 底层

    async def _unwrap(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 500:
            raise BiliNoteError(
                f"BiliNote 后端异常 HTTP {resp.status_code}: {resp.text[:200]}",
                resp.status_code,
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - 任何解析失败都转成可读错误
            raise BiliNoteError(
                f"BiliNote 返回非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}"
            ) from exc

        if not isinstance(payload, dict):
            raise BiliNoteError("BiliNote 返回格式异常（不是 JSON 对象）")
        code = payload.get("code", 0)
        if code not in (0, None):
            try:
                code_int = int(code)
            except (TypeError, ValueError):
                code_int = -1
            raise BiliNoteError(str(payload.get("msg") or "BiliNote 返回错误"), code_int)
        return payload.get("data")

    # ---------------------------------------------------------------- 接口

    async def is_alive(self) -> bool:
        """探测后端是否在跑（用无需参数、无副作用的 provider 列表接口）。"""
        try:
            resp = await self._client.get("/get_all_providers", timeout=5.0)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - 连不上就是没起来
            return False

    async def engine_health(self, timeout: float = 2.0) -> bool:
        """v0.3 readiness 用的引擎健康探测（BiliNote ``/api/health``，总超时 ≤ ``timeout``）。

        - ``/api/health`` 返回 2xx → 健康；
        - BiliNote 老版本没有 ``/api/health``（404/405）→ 回退到
          ``/get_all_providers``（与 :meth:`is_alive` 同一个探针），避免把老版本误判为不可用；
        - 超时/连不上/其它状态码 → 不健康。

        总耗时受 ``timeout`` 约束（含回退请求），供 agent_ready 上报使用。
        """
        import asyncio

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout))

        async def _get(path: str) -> int:
            remaining = deadline - loop.time()
            if remaining <= 0.05:
                raise asyncio.TimeoutError
            resp = await self._client.get(path, timeout=remaining)
            return resp.status_code

        try:
            status = await asyncio.wait_for(_get("/health"), timeout=max(0.1, timeout))
            if 200 <= status < 300:
                return True
            if status in (404, 405):
                fallback = await asyncio.wait_for(
                    _get("/get_all_providers"), timeout=max(0.1, timeout)
                )
                return 200 <= fallback < 300
            return False
        except asyncio.TimeoutError:
            logger.debug("BiliNote 引擎健康探测超时（%.1fs）", timeout)
            return False
        except Exception as exc:  # noqa: BLE001 - 连不上/协议错误都视为不可用
            logger.debug("BiliNote 引擎健康探测失败：%s", exc)
            return False

    async def generate_note(
        self,
        video_url: str,
        platform: str,
        *,
        provider_id: str,
        model_name: str,
        quality: str = "fast",
        style: str = "minimal",
        screenshot: bool = False,
        link: bool = True,
        reuse_task_id: Optional[str] = None,
    ) -> str:
        """提交生成任务，返回 BiliNote 的 task_id。

        ``reuse_task_id`` 传已有 task_id 时走后端「重试」路径，可复用已缓存的
        音频/字幕，跳过下载与转写（自测时很省时间）。
        """
        # 注意：style 在后端是 str 而非 Optional，显式传 null 会 422。
        body: dict[str, Any] = {
            "video_url": video_url,
            "platform": platform,
            "quality": quality,
            "style": style,
            "screenshot": screenshot,
            "link": link,
            "model_name": model_name,
            "provider_id": provider_id,
            **DEFAULT_BODY,
        }
        if reuse_task_id:
            body["task_id"] = reuse_task_id
        try:
            resp = await self._client.post("/generate_note", json=body, timeout=60.0)
        except httpx.HTTPError as exc:
            raise BiliNoteError(f"无法连接 BiliNote 后端：{exc}") from exc

        data = await self._unwrap(resp)
        task_id = (data or {}).get("task_id") if isinstance(data, dict) else None
        if not task_id:
            raise BiliNoteError("BiliNote 后端没有返回 task_id")
        return str(task_id)

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """查询任务状态。

        返回 ``{'status': 'SUCCESS'|'FAILED'|'PENDING'|..., 'message': str, 'result': dict|None}``。
        任务 FAILED 时后端返回 code=500，这里转换成统一结构而不是抛异常，
        让调用方能拿到失败原因。
        """
        # uvicorn 会回收空闲 keep-alive 连接，偶发 ReadError / RemoteProtocolError，
        # 对读操作做一次重试，避免把一次抖动当失败。
        last_err: Exception | None = None
        resp: httpx.Response | None = None
        for attempt in range(3):
            try:
                resp = await self._client.get(f"/task_status/{task_id}", timeout=30.0)
                break
            except httpx.HTTPError as exc:
                last_err = exc
                logger.debug("查询 BiliNote 状态失败（第 %d 次）：%s", attempt + 1, exc)
                await asyncio.sleep(1.0)
        if resp is None:
            raise BiliNoteError(f"查询 BiliNote 任务状态失败：{last_err}")

        if resp.status_code >= 500:
            raise BiliNoteError(f"BiliNote 后端异常 HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BiliNoteError(
                f"BiliNote 返回非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}"
            ) from exc

        code = payload.get("code", 0)
        if code not in (0, None):
            return {
                "status": "FAILED",
                "message": str(payload.get("msg") or "任务失败"),
                "result": None,
            }

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise BiliNoteError("BiliNote 状态返回格式异常")
        return {
            "status": str(data.get("status") or "PENDING").upper(),
            "message": str(data.get("message") or ""),
            "result": data.get("result"),
        }


__all__ = ["BiliNoteClient", "BiliNoteError", "DEFAULT_BODY"]

"""BiliNote 引擎的 HTTP API 客户端（同步，供桌面 GUI 线程调用）。"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class EngineAPIError(RuntimeError):
    def __init__(self, message: str, code: int = -1):
        super().__init__(message)
        self.message = message
        self.code = code


class EngineAPI:
    def __init__(self, base_url: str = "http://127.0.0.1:8483/api", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ 基础
    def _request(self, method: str, path: str, *, json: Any = None, timeout: float | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=timeout or self.timeout, trust_env=False) as client:
                resp = client.request(method, url, json=json)
        except httpx.HTTPError as exc:
            raise EngineAPIError(f"无法连接 BiliNote 引擎：{exc}") from exc

        try:
            payload = resp.json()
        except ValueError:
            raise EngineAPIError(f"BiliNote 引擎返回了非 JSON（HTTP {resp.status_code}）") from None
        if resp.status_code >= 500:
            raise EngineAPIError(f"BiliNote 引擎异常（HTTP {resp.status_code}）", resp.status_code)
        code = payload.get("code", 0) if isinstance(payload, dict) else 0
        if code not in (0, None):
            message = payload.get("msg") or payload.get("message") or "BiliNote 返回错误"
            raise EngineAPIError(str(message), int(code) if code is not None else -1)
        return payload.get("data") if isinstance(payload, dict) else payload

    def is_alive(self, timeout: float = 2.0) -> bool:
        try:
            self._request("GET", "/sys_check", timeout=timeout)
            return True
        except EngineAPIError:
            return False

    # ------------------------------------------------------------------ 健康
    def sys_check(self) -> Any:
        return self._request("GET", "/sys_check")

    def sys_health(self) -> dict[str, Any]:
        return self._request("GET", "/sys_health") or {}

    def deploy_status(self) -> dict[str, Any]:
        return self._request("GET", "/deploy_status") or {}

    # ------------------------------------------------------------------ 供应商/模型
    def providers(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/get_all_providers")
        return data if isinstance(data, list) else []

    def update_provider(self, provider_id: str, **fields: Any) -> Any:
        body = {"id": provider_id, **{k: v for k, v in fields.items() if v is not None}}
        return self._request("POST", "/update_provider", json=body)

    def add_provider(self, name: str, api_key: str, base_url: str, type_: str = "custom") -> Any:
        body = {"name": name, "api_key": api_key, "base_url": base_url, "logo": "custom", "type": type_}
        return self._request("POST", "/add_provider", json=body)

    def connect_test(self, provider_id: str, model: str | None = None) -> Any:
        body: dict[str, Any] = {"id": provider_id}
        if model:
            body["model"] = model
        return self._request("POST", "/connect_test", json=body, timeout=60.0)

    def models(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/model_list")
        return data if isinstance(data, list) else []

    def enabled_models(self, provider_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/model_enable/{provider_id}")
        return data if isinstance(data, list) else []

    def add_model(self, provider_id: str, model_name: str) -> Any:
        return self._request("POST", "/models", json={"provider_id": provider_id, "model_name": model_name})

    def delete_model(self, model_id: int) -> Any:
        return self._request("GET", f"/models/delete/{model_id}")

    # ------------------------------------------------------------------ 转写
    def transcriber_config(self) -> dict[str, Any]:
        return self._request("GET", "/transcriber_config") or {}

    def update_transcriber_config(self, transcriber_type: str, whisper_model_size: str | None = None) -> Any:
        body: dict[str, Any] = {"transcriber_type": transcriber_type}
        if whisper_model_size:
            body["whisper_model_size"] = whisper_model_size
        return self._request("POST", "/transcriber_config", json=body)

    def transcriber_status(self) -> dict[str, Any]:
        return self._request("GET", "/transcriber_models_status") or {}

    def whisper_models(self) -> dict[str, Any]:
        return self._request("GET", "/whisper_models") or {}

    def download_whisper(self, model_size: str, transcriber_type: str = "fast-whisper") -> Any:
        return self._request(
            "POST",
            "/transcriber_download",
            json={"model_size": model_size, "transcriber_type": transcriber_type},
            timeout=120.0,
        )

    def add_whisper_model(self, name: str, target: str) -> Any:
        return self._request("POST", "/whisper_models", json={"name": name, "target": target})

    # ------------------------------------------------------------------ 其他配置
    def proxy_config(self) -> dict[str, Any]:
        return self._request("GET", "/proxy_config") or {}

    def update_proxy_config(self, enabled: bool, url: str | None = None) -> Any:
        return self._request("POST", "/proxy_config", json={"enabled": enabled, "url": url or ""})

    def get_cookie(self, platform: str) -> dict[str, Any]:
        return self._request("GET", f"/get_downloader_cookie/{platform}") or {}

    def update_cookie(self, platform: str, cookie: str) -> Any:
        return self._request("POST", "/update_downloader_cookie", json={"platform": platform, "cookie": cookie})


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, EngineAPIError):
        return exc.message
    return str(exc) or type(exc).__name__

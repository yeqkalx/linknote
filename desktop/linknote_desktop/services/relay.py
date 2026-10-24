"""Relay 配对/状态（复用 pc-agent 的 RelayClient 异步实现）。"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from linknote_agent.relay_client import RelayClient, RelayError

from ..config import APP_VERSION


class RelayService:
    def __init__(self, relay_base_url: str = ""):
        self.relay_base_url = relay_base_url

    def _base(self) -> str:
        return self.relay_base_url.rstrip("/")

    def health(self, timeout: float = 5.0) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{self._base()}/api/v1/health")
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                return data.get("data") or {}
            raise RelayError("Relay 健康检查失败")
        except httpx.HTTPError as exc:
            raise RelayError(f"无法连接 Relay：{exc}") from exc

    def _run(self, coro):
        return asyncio.run(coro)

    def create_pairing(self, agent_name: str, platform: str = "windows") -> dict[str, Any]:
        async def work():
            client = RelayClient(self._base(), "")
            try:
                return await client.create_pairing(agent_name, platform, APP_VERSION)
            finally:
                await client.aclose()

        return self._run(work())

    def get_pairing(self, pairing_id: str, agent_token: str) -> dict[str, Any]:
        async def work():
            client = RelayClient(self._base(), agent_token)
            try:
                return await client.get_pairing(pairing_id)
            finally:
                await client.aclose()

        return self._run(work())

    def create_extra_pairing(self, agent_token: str) -> dict[str, Any]:
        async def work():
            client = RelayClient(self._base(), agent_token)
            try:
                return await client.create_extra_pairing()
            finally:
                await client.aclose()

        return self._run(work())

    def me(self, agent_token: str) -> dict[str, Any]:
        async def work():
            client = RelayClient(self._base(), agent_token)
            try:
                return await client.me()
            finally:
                await client.aclose()

        return self._run(work())

    def cancel_pairing(self, agent_token: str, pairing_id: str) -> None:
        async def work():
            client = RelayClient(self._base(), agent_token)
            try:
                await client.cancel_pairing(pairing_id)
            finally:
                await client.aclose()

        self._run(work())
    def revoke_device(self, agent_token: str, device_id: str) -> None:
        async def work():
            client = RelayClient(self._base(), agent_token)
            try:
                await client.revoke_device(device_id)
            finally:
                await client.aclose()

        self._run(work())


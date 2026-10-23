"""测试公共设施：进程内假 Relay / 假 BiliNote（基于 httpx.MockTransport）。

测试不依赖真实 Relay / BiliNote，也不需要起 HTTP server：
- :class:`FakeRelay` 按 docs/api-contract.md 实现 Agent 用到的端点，并记录所有请求；
- :class:`FakeBiliNote` 按 BiliNote ResponseWrapper 格式实现 /generate_note、/task_status；
- 通过 ``transport`` 注入到真实客户端，因此 HTTP 层/鉴权/错误映射也是被测代码。
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import pytest

from linknote_agent.backend import BackendSupervisor
from linknote_agent.bilinote_client import BiliNoteClient
from linknote_agent.config import AgentConfig
from linknote_agent.outbox import Outbox
from linknote_agent.relay_client import RelayClient

RELAY_BASE = "http://relay.test"
BILINOTE_BASE = "http://bilinote.test/api"
AGENT_TOKEN = "agt_test_token_0123456789"
PAIRING_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ok(data: Any = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"ok": True, "data": data})


def _err(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"ok": False, "error": {"code": code, "message": message}})


def _read_json(request: httpx.Request) -> Any:
    try:
        return json.loads(request.content.decode("utf-8")) if request.content else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ================================================================ Fake Relay


class FakeRelay:
    """按接口契约实现 Agent 侧端点，状态全部在内存里。"""

    def __init__(self, token: str = AGENT_TOKEN):
        self.token = token
        self.agent = {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "我的电脑",
            "platform": "windows",
            "version": "0.1.0",
            "online": True,
            "last_seen_at": iso_now(),
            "created_at": iso_now(),
        }
        self.pairings: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.queue: list[str] = []
        self.devices: list[dict[str, Any]] = []

        # 记录
        self.requests: list[dict[str, Any]] = []
        self.progress_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []
        self.fail_calls: list[dict[str, Any]] = []
        self.heartbeat_calls: list[dict[str, Any]] = []
        self.lease_calls: list[dict[str, Any]] = []

        # 故障注入
        self.down = False
        self.next_task_fail_times = 0
        self.progress_fail_times = 0
        self.progress_fail_status = 500
        self.progress_fail_code = "internal"
        self.complete_fail_times = 0
        self.complete_fail_status = 500
        self.complete_fail_code = "internal"
        self.fail_fail_times = 0
        self.fail_fail_status = 500
        self.fail_fail_code = "internal"
        self.heartbeat_fail_times = 0

        # 配对
        self.pairing_claim_after_polls: Optional[int] = None
        self.pairing_expire_immediately = False
        self.pairing_poll_count = 0
        self.last_pairing: dict[str, Any] = {}

    # ---------------------------------------------------------------- 工具

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, method: str, path_suffix: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.requests
            if r["method"] == method and r["path"].endswith(path_suffix)
        ]

    def add_task(
        self,
        url: str = "https://www.bilibili.com/video/BV1xx411c7mD",
        platform: str = "bilibili",
        *,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        tid = task_id or str(uuid.uuid4())
        task = {
            "id": tid,
            "url": url,
            "platform": platform,
            "status": "queued",
            "stage": "pending",
            "progress": 0,
            "message": "排队中",
            "title": None,
            "error_code": None,
            "error_message": None,
            "attempts": 0,
            "created_at": iso_now(),
        }
        self.tasks[tid] = task
        self.queue.append(tid)
        return task

    def claim_pairing(self, pairing_id: Optional[str] = None, device_name: str = "Pixel 9") -> None:
        pairing = self.pairings[pairing_id] if pairing_id else self.last_pairing
        pairing["status"] = "claimed"
        pairing["device"] = {
            "id": "22222222-2222-4222-8222-222222222222",
            "name": device_name,
            "platform": "android",
            "paired_at": iso_now(),
        }
        if pairing.get("id"):
            self.devices.append(pairing["device"])

    # ---------------------------------------------------------------- 路由

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("模拟 Relay 断网", request=request)

        method = request.method.upper()
        path = request.url.path
        body = _read_json(request)
        self.requests.append(
            {
                "method": method,
                "path": path,
                "json": body,
                "authorization": request.headers.get("authorization", ""),
            }
        )

        # ---- 公开端点 ----
        if method == "GET" and path == "/api/v1/health":
            return _ok({"status": "ok", "version": "0.1.0", "time": iso_now()})
        if method == "POST" and path == "/api/v1/pairings":
            return self._create_pairing(body or {})

        # ---- 以下都需要 Agent 认证 ----
        if request.headers.get("authorization", "") != f"Bearer {self.token}":
            return _err(401, "unauthorized", "token 缺失或已失效")

        if path.startswith("/api/v1/pairings/"):
            return self._pairing_routes(method, path, body)

        if method == "GET" and path == "/api/v1/agent/me":
            return self._agent_me()
        if method == "POST" and path == "/api/v1/agent/heartbeat":
            return self._heartbeat(body or {})
        if method == "POST" and path == "/api/v1/agent/pairings":
            extra = self._create_pairing({}, extra=True)
            return extra
        if method == "DELETE" and path.startswith("/api/v1/agent/devices/"):
            device_id = path.rsplit("/", 1)[-1]
            self.devices = [d for d in self.devices if d.get("id") != device_id]
            return _ok({})
        if method == "GET" and path == "/api/v1/agent/tasks/next":
            return self._next_task()
        if path.startswith("/api/v1/agent/tasks/"):
            return self._task_routes(method, path, body)

        return _err(404, "not_found", f"未实现的端点：{method} {path}")

    # ---------------------------------------------------------------- 配对

    def _create_pairing(self, body: dict[str, Any], extra: bool = False) -> httpx.Response:
        pairing_id = str(uuid.uuid4())
        code = "".join(random.choice(PAIRING_ALPHABET) for _ in range(8))
        token = self.token if extra else f"agt_{uuid.uuid4().hex}{uuid.uuid4().hex[:11]}"
        if not extra:
            self.token = token
        pairing = {
            "id": pairing_id,
            "agent_id": self.agent["id"],
            "code": f"{code[:4]}-{code[4:]}",
            "status": "expired" if self.pairing_expire_immediately else "pending",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "device": None,
            "claimed_at": None,
            "canceled": False,
        }
        self.pairings[pairing_id] = pairing
        self.last_pairing = pairing
        self.pairing_poll_count = 0
        data: dict[str, Any] = {
            "pairing_id": pairing_id,
            "code": pairing["code"],
            "expires_at": pairing["expires_at"],
        }
        if not extra:
            data.update({"agent_id": self.agent["id"], "agent_token": token})
        return _ok(data, status=201)

    def _pairing_routes(
        self, method: str, path: str, body: Any
    ) -> httpx.Response:
        rest = path[len("/api/v1/pairings/") :]
        if rest.endswith("/cancel") and method == "POST":
            pairing_id = rest[: -len("/cancel")]
            pairing = self.pairings.get(pairing_id)
            if pairing is None:
                return _err(404, "pairing_not_found", "配对不存在")
            pairing["canceled"] = True
            return _ok({})
        if method == "GET":
            pairing = self.pairings.get(rest)
            if pairing is None:
                return _err(404, "pairing_not_found", "配对不存在")
            if pairing["canceled"]:
                return _ok({"status": "expired", "expires_at": pairing["expires_at"], "device": None})
            if pairing["status"] != "claimed":
                self.pairing_poll_count += 1
                if (
                    self.pairing_claim_after_polls is not None
                    and self.pairing_poll_count >= self.pairing_claim_after_polls
                ):
                    self.claim_pairing(pairing["id"])
            return _ok(
                {
                    "status": pairing["status"],
                    "expires_at": pairing["expires_at"],
                    "device": pairing["device"],
                }
            )
        return _err(404, "not_found", "未实现的配对端点")

    # ---------------------------------------------------------------- Agent

    def _agent_me(self) -> httpx.Response:
        queued = sum(1 for t in self.tasks.values() if t["status"] == "queued")
        running = sum(1 for t in self.tasks.values() if t["status"] == "running")
        return _ok(
            {
                "agent": dict(self.agent),
                "devices": list(self.devices),
                "queue": {"queued": queued, "running": running},
                "server_time": iso_now(),
            }
        )

    def _heartbeat(self, body: dict[str, Any]) -> httpx.Response:
        self.heartbeat_calls.append(body)
        self.agent["last_seen_at"] = iso_now()
        if self.heartbeat_fail_times > 0:
            self.heartbeat_fail_times -= 1
            return _err(500, "internal", "模拟心跳失败")
        return _ok({"server_time": iso_now(), "queued_tasks": len(self.queue)})

    # ---------------------------------------------------------------- 任务

    def _next_task(self) -> httpx.Response:
        if self.next_task_fail_times > 0:
            self.next_task_fail_times -= 1
            return _err(500, "internal", "模拟领取任务失败")
        while self.queue:
            task_id = self.queue.pop(0)
            task = self.tasks[task_id]
            if task["status"] != "queued":
                continue
            task["status"] = "running"
            task["attempts"] += 1
            task["claimed_at"] = iso_now()
            return _ok(
                {
                    "task": {
                        "id": task["id"],
                        "url": task["url"],
                        "platform": task["platform"],
                        "attempts": task["attempts"],
                        "created_at": task["created_at"],
                    }
                }
            )
        return _ok({"task": None})

    def _task_routes(self, method: str, path: str, body: Any) -> httpx.Response:
        rest = path[len("/api/v1/agent/tasks/") :]
        parts = rest.split("/")
        task_id = parts[0]
        action = parts[1] if len(parts) > 1 else ""
        task = self.tasks.get(task_id)
        if task is None:
            return _err(404, "task_not_found", "任务不存在")

        if method == "GET":
            return _ok({"task": dict(task)})

        if action == "progress":
            if self.progress_fail_times > 0:
                self.progress_fail_times -= 1
                return _err(self.progress_fail_status, self.progress_fail_code, "模拟进度回传失败")
            if task["status"] != "running":
                return _err(409, "invalid_state", "任务不在 running 状态")
            assert isinstance(body, dict)
            stage = body.get("stage")
            if stage not in {
                "parsing",
                "downloading",
                "transcribing",
                "summarizing",
                "formatting",
                "saving",
            }:
                return _err(400, "bad_request", f"非法 stage: {stage}")
            record = {
                "task_id": task_id,
                "stage": stage,
                "progress": body.get("progress"),
                "message": body.get("message"),
                "time": time.monotonic(),
            }
            self.progress_calls.append(record)
            task["stage"] = stage
            if body.get("progress") is not None:
                task["progress"] = int(body["progress"])
            if body.get("message") is not None:
                task["message"] = str(body["message"])
            task["updated_at"] = iso_now()
            return _ok({"lease_expires_at": iso_now()})

        if action == "complete":
            if self.complete_fail_times > 0:
                self.complete_fail_times -= 1
                return _err(self.complete_fail_status, self.complete_fail_code, "模拟完成回传失败")
            if task["status"] == "succeeded":
                return _ok({})  # 契约：幂等
            if task["status"] != "running":
                return _err(409, "invalid_state", "任务不在 running 状态")
            assert isinstance(body, dict)
            if not str(body.get("markdown") or "").strip():
                return _err(400, "bad_request", "markdown 不能为空")
            task["status"] = "succeeded"
            task["stage"] = "done"
            task["progress"] = 100
            task["title"] = body.get("title")
            task["markdown"] = body.get("markdown")
            task["duration_seconds"] = body.get("duration_seconds")
            task["meta"] = body.get("meta")
            task["completed_at"] = iso_now()
            self.complete_calls.append({"task_id": task_id, **body})
            return _ok({})

        if action == "fail":
            if self.fail_fail_times > 0:
                self.fail_fail_times -= 1
                return _err(self.fail_fail_status, self.fail_fail_code, "模拟失败回传失败")
            if task["status"] == "failed":
                return _ok({})  # 幂等
            if task["status"] != "running":
                return _err(409, "invalid_state", "任务不在 running 状态")
            assert isinstance(body, dict)
            task["status"] = "failed"
            task["stage"] = "failed"
            task["error_code"] = body.get("code")
            task["error_message"] = body.get("message")
            task["completed_at"] = iso_now()
            self.fail_calls.append({"task_id": task_id, **body})
            return _ok({})

        if action == "heartbeat":
            if task["status"] != "running":
                return _err(409, "invalid_state", "任务不在 running 状态")
            self.lease_calls.append({"task_id": task_id, "at": iso_now()})
            return _ok({"lease_expires_at": iso_now()})

        return _err(404, "not_found", "未实现的任务端点")


# ================================================================ Fake BiliNote


class FakeBiliNote:
    """按 BiliNote ResponseWrapper 格式实现的假后端。"""

    def __init__(self) -> None:
        self.alive = True
        self.requests: list[dict[str, Any]] = []
        self.generate_bodies: list[dict[str, Any]] = []
        self.generate_error: Optional[tuple[str, int]] = None
        self.default_script = [
            "PENDING",
            "PARSING",
            "DOWNLOADING",
            "TRANSCRIBING",
            "SUMMARIZING",
            "SUCCESS",
        ]
        self.next_script: Optional[list[str]] = None
        self.next_result: Optional[dict[str, Any]] = None
        self.failure_message = "任务失败"
        self.status_500_remaining = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.task_counter = 0
        self.result = {
            "markdown": "# 测试笔记\n\n这是正文。",
            "audio_meta": {"title": "测试视频标题", "duration": 754},
        }

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path
        body = _read_json(request)
        self.requests.append({"method": method, "path": path, "json": body})

        if path == "/api/get_all_providers":
            if not self.alive:
                raise httpx.ConnectError("模拟 BiliNote 未启动", request=request)
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": []})

        if method == "POST" and path == "/api/generate_note":
            assert isinstance(body, dict)
            self.generate_bodies.append(body)
            if self.generate_error is not None:
                message, code = self.generate_error
                return httpx.Response(200, json={"code": code, "msg": message, "data": None})
            self.task_counter += 1
            bilinote_task_id = f"bilinote-task-{self.task_counter}"
            script = self.next_script or self.default_script
            self.next_script = None
            self.tasks[bilinote_task_id] = {
                "statuses": list(script),
                "index": 0,
                "result": self.next_result or self.result,
            }
            self.next_result = None
            return httpx.Response(
                200, json={"code": 0, "msg": "success", "data": {"task_id": bilinote_task_id}}
            )

        if path.startswith("/api/task_status/"):
            bilinote_task_id = path.rsplit("/", 1)[-1]
            if self.status_500_remaining > 0:
                self.status_500_remaining -= 1
                return httpx.Response(500, text="backend exploded")
            task = self.tasks.get(bilinote_task_id)
            if task is None:
                return httpx.Response(404, json={"code": 404, "msg": "任务不存在", "data": None})
            statuses = task["statuses"]
            index = min(task["index"], len(statuses) - 1)
            status = statuses[index]
            task["index"] += 1
            if status == "FAILED":
                return httpx.Response(
                    200, json={"code": 500, "msg": self.failure_message, "data": None}
                )
            data: dict[str, Any] = {"status": status, "message": ""}
            if status == "SUCCESS":
                data["result"] = task["result"]
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": data})

        return httpx.Response(404, json={"code": 404, "msg": f"未实现 {method} {path}", "data": None})


# ================================================================ fixtures


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """所有测试都用临时 HOME，避免碰到真实的 ~/.linknote。"""
    home = tmp_path / "linknote-home"
    monkeypatch.setenv("LINKNOTE_HOME", str(home))
    monkeypatch.delenv("LINKNOTE_CONFIG_PATH", raising=False)
    for key in list(os.environ):
        if key.startswith("LINKNOTE_") and key != "LINKNOTE_HOME":
            monkeypatch.delenv(key, raising=False)
    return home


@pytest.fixture
def fake_relay() -> FakeRelay:
    return FakeRelay()


@pytest.fixture
def fake_bilinote() -> FakeBiliNote:
    return FakeBiliNote()


@pytest.fixture
async def relay_client(fake_relay: FakeRelay):
    client = RelayClient(RELAY_BASE, AGENT_TOKEN, transport=fake_relay.transport)
    yield client
    await client.aclose()


@pytest.fixture
async def bilinote_client(fake_bilinote: FakeBiliNote):
    client = BiliNoteClient(BILINOTE_BASE, transport=fake_bilinote.transport)
    yield client
    await client.aclose()


@pytest.fixture
def make_config(isolated_home):
    def _make(**overrides: Any) -> AgentConfig:
        cfg = AgentConfig()
        cfg.relay_base_url = RELAY_BASE
        cfg.agent_token = AGENT_TOKEN
        cfg.agent_id = "11111111-1111-4111-8111-111111111111"
        cfg.agent_name = "我的电脑"
        cfg.platform = "windows"
        cfg.backend_base_url = BILINOTE_BASE
        cfg.backend_exe = str(isolated_home / "BiliNoteBackend.exe")
        cfg.backend_workdir = str(isolated_home)
        cfg.auto_start_backend = False
        cfg.backend_start_timeout = 0.2
        cfg.poll_interval_seconds = 0.01
        cfg.task_status_poll_interval_seconds = 0.01
        cfg.progress_min_interval_seconds = 0.02
        cfg.heartbeat_interval_seconds = 0.03
        cfg.lease_heartbeat_interval_seconds = 0.03
        cfg.task_timeout_seconds = 5.0
        cfg.pair_poll_interval_seconds = 0.01
        cfg.max_status_errors = 3
        cfg.log_dir = str(isolated_home / "logs")
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    return _make


@pytest.fixture
def make_runner(make_config, relay_client, bilinote_client):
    def _make(**overrides: Any):
        from linknote_agent.runner import AgentRunner

        cfg = make_config(**overrides)
        backend = BackendSupervisor(cfg, bilinote=bilinote_client)
        outbox = Outbox(cfg.resolved_outbox_dir(), notes_dir=cfg.resolved_notes_dir())
        return AgentRunner(cfg, relay_client, bilinote_client, backend=backend, outbox=outbox)

    return _make

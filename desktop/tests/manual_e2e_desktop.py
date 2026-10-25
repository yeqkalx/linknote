"""桌面客户端真实链路手工 E2E：独立 LOCALAPPDATA，不污染用户配置。

流程：DesktopController 创建配对 → HTTP 领码 → 自动配对 → 启动 Agent 子进程 →
用手机设备 token 提交一个短视频 → 等 Agent 调本机 BiliNote 生成 → 取回 Markdown。

用法（从 desktop 目录）：
    .venv\\Scripts\\python.exe -m tests.manual_e2e_desktop
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# 必须在导入 linknote_desktop 之前改，保证使用临时数据目录
_TMP = Path(tempfile.mkdtemp(prefix="linknote-desktop-e2e-"))
os.environ["LOCALAPPDATA"] = str(_TMP)

import httpx
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from linknote_desktop.config import DesktopConfig
from linknote_desktop.controller import DesktopController

RELAY = os.environ.get("E2E_RELAY", "https://relay.xykong.xyz").rstrip("/")
URL = os.environ.get("E2E_VIDEO_URL", "https://www.youtube.com/watch?v=jNQXAC9IVRw")
TASK_TIMEOUT = 240


def wait(controller: DesktopController, predicate, timeout: float, label: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            print(f"[ok] {label}")
            return True
        time.sleep(0.1)
    raise TimeoutError(f"{label} 超时")


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    cfg = DesktopConfig.load()
    cfg.relay_base_url = RELAY
    cfg.engine_auto_start = False
    cfg.onboarding_done = True
    cfg.agent_name = "LinkNote-E2E"
    cfg.save()
    controller = DesktopController(cfg)
    controller.start()

    print(f"[info] temp data: {_TMP}")
    controller.create_pairing_async()
    wait(controller, lambda: bool(controller.pairing_state().get("code")), 20, "生成配对码")
    state = controller.pairing_state()
    code = state["code"]
    print(f"[info] pairing code: {code}")

    with httpx.Client(timeout=30) as client:
        claim = client.post(
            f"{RELAY}/api/v1/pairings/claim",
            json={"code": code, "device_name": "桌面 E2E 手机", "platform": "android"},
        ).json()
        if not claim.get("ok"):
            print("claim failed", claim)
            return 1
        device_token = claim["data"]["device_token"]
        print(f"[info] claimed device: {claim['data']['device_id']}")

    wait(controller, lambda: controller.pairing_state().get("status") == "claimed", 20, "Controller 收到配对成功")
    wait(controller, lambda: controller.agent.running, 20, "Agent 子进程启动")

    with httpx.Client(timeout=30) as client:
        task_resp = client.post(
            f"{RELAY}/api/v1/tasks",
            headers={"Authorization": f"Bearer {device_token}"},
            json={"url": URL, "client_key": f"desktop-e2e-{int(time.time())}"},
        ).json()
        if not task_resp.get("ok"):
            print("create task failed", task_resp)
            return 1
        task_id = task_resp["data"]["task"]["id"]
        print(f"[info] task: {task_id}")

        deadline = time.monotonic() + TASK_TIMEOUT
        task = None
        while time.monotonic() < deadline:
            QApplication.processEvents()
            data = client.get(
                f"{RELAY}/api/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {device_token}"},
            ).json()
            task = data.get("data", {}).get("task", {})
            print(f"  status={task.get('status')} stage={task.get('stage')} progress={task.get('progress')} msg={task.get('message')}")
            if task.get("status") in ("succeeded", "failed"):
                break
            time.sleep(3)
        if not task or task.get("status") != "succeeded":
            print("[FAIL] task did not succeed:", task)
            return 1
        note = client.get(
            f"{RELAY}/api/v1/tasks/{task_id}/note",
            headers={"Authorization": f"Bearer {device_token}"},
        ).json()
        md = note.get("data", {}).get("markdown", "")
        print(f"[ok] note: {note.get('data', {}).get('title')} / {len(md)} chars")
        Path(_TMP / "note.md").write_text(md, encoding="utf-8")

    controller.shutdown()
    print(f"[PASS] temp data kept at {_TMP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Agent 子进程管理：启动 / 停止 / 日志。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import DesktopConfig, log_dir, user_data_dir
from .ffmpeg import find_ffmpeg

CREATE_NO_WINDOW = 0x08000000


class AgentProcess:
    def __init__(self, config: DesktopConfig):
        self.config = config
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None
        self.last_error = ""

    @property
    def config_path(self) -> Path:
        return self.config.agent_config_path

    @property
    def log_path(self) -> Path:
        return log_dir() / "agent-worker.log"

    def read_config(self) -> dict[str, Any]:
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def write_config(self, data: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.config_path)

    @property
    def is_paired(self) -> bool:
        return bool(self.read_config().get("agent_token"))

    @property
    def telegram_local_mode(self) -> bool:
        """无 Relay + Telegram 已配置：允许不配对手机直接启动 Agent（本地模式）。"""
        try:
            return bool(self.config.telegram_local_mode())
        except (AttributeError, TypeError):
            return False

    @property
    def agent_token(self) -> str:
        return str(self.read_config().get("agent_token") or "")

    def save_pairing(self, data: dict[str, Any]) -> None:
        cfg = self.read_config()
        cfg.update(
            {
                "relay_base_url": self.config.relay_base_url.rstrip("/"),
                "agent_name": self.config.agent_name or "我的电脑",
                "platform": "windows",
            }
        )
        for key in (
            "agent_token",
            "agent_id",
            "pairing_id",
            "pairing_code",
            "pairing_expires_at",
            "paired_at",
            "device_id",
            "device_name",
            "device_platform",
        ):
            if key in data and data[key] is not None:
                cfg[key] = data[key]
        self.write_config(cfg)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self.running and self._proc else None

    def _command(self) -> list[str]:
        cfg_path = str(self.config_path)
        if getattr(sys, "frozen", False):
            return [sys.executable, "--agent-worker", "--config", cfg_path, "run"]
        return [sys.executable, "-m", "linknote_agent", "--config", cfg_path, "run"]

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        ffmpeg = find_ffmpeg(self.config)
        if ffmpeg.bin_dir:
            env["FFMPEG_BIN_PATH"] = ffmpeg.bin_dir
        if self.config.proxy_enabled and self.config.proxy_url.strip():
            proxy = self.config.proxy_url.strip()
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = env["ALL_PROXY"] = proxy
        if self.config.hf_endpoint.strip():
            env["HF_ENDPOINT"] = self.config.hf_endpoint.strip()
        return env

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            if not self.is_paired and not self.telegram_local_mode:
                self.last_error = (
                    "还没有配对手机，也没有启用 Telegram 消息入口。"
                    "请生成配对码，或在设置里启用 Telegram（可无 Relay 本地运行）。"
                )
                return False
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._log_handle = open(self.log_path, "ab", buffering=0)
                kwargs: dict = {
                    "cwd": str(user_data_dir()),
                    "stdout": self._log_handle,
                    "stderr": subprocess.STDOUT,
                    "stdin": subprocess.DEVNULL,
                    "env": self._env(),
                }
                if os.name == "nt":
                    kwargs["creationflags"] = CREATE_NO_WINDOW
                self._proc = subprocess.Popen(self._command(), **kwargs)
                self.last_error = ""
                return True
            except OSError as exc:
                self.last_error = f"启动 Agent 失败：{exc}"
                return False

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        proc.kill()
                    except OSError:
                        pass
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except OSError:
                    pass
                self._log_handle = None

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def clear_pairing(self) -> None:
        """只清除身份字段，保留后端/模型等设置。"""
        cfg = self.read_config()
        for key in (
            "agent_token",
            "agent_id",
            "pairing_id",
            "pairing_code",
            "pairing_expires_at",
            "paired_at",
            "device_id",
            "device_name",
            "device_platform",
        ):
            cfg[key] = ""
        self.write_config(cfg)
    def log_tail(self, lines: int = 120) -> str:
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(content[-lines:])
        except OSError:
            return ""

    def tail_raw(self) -> str:
        """供托盘通知解析：返回最近一行。"""
        tail = self.log_tail(8)
        return tail.splitlines()[-1] if tail else ""


def build_agent_worker_argv(argv: list[str]) -> list[str]:
    """`LinkNote.exe --agent-worker ...` 的剩余参数交给 linknote_agent CLI。"""
    if "--agent-worker" in argv:
        idx = argv.index("--agent-worker")
        return argv[:idx] + argv[idx + 1 :]
    return argv



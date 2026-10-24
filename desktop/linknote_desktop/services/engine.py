"""BiliNote 引擎进程管理（启动 / 停止 / 探测 / 健康）。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import DesktopConfig, log_dir
from .bilinote_api import EngineAPI, EngineAPIError
from .ffmpeg import find_ffmpeg

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


@dataclass
class EngineState:
    state: str = "stopped"  # stopped / starting / running / error
    pid: Optional[int] = None
    message: str = ""
    owned: bool = False
    path: str = ""


class EngineManager:
    def __init__(self, config: DesktopConfig):
        self.config = config
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None
        self._state = EngineState(path=config.resolved_engine_exe())
        self._api = EngineAPI(config.backend_base_url)

    @property
    def api(self) -> EngineAPI:
        self._api.base_url = self.config.backend_base_url.rstrip("/")
        return self._api

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def log_path(self) -> Path:
        return log_dir() / "bilinote_backend.log"

    @property
    def engine_exe(self) -> Path:
        return Path(self.config.resolved_engine_exe())

    def is_alive(self) -> bool:
        return self.api.is_alive()

    def _set(self, state: str, message: str = "", pid: Optional[int] = None, owned: Optional[bool] = None) -> None:
        self._state.state = state
        self._state.message = message
        if pid is not None:
            self._state.pid = pid
        if owned is not None:
            self._state.owned = owned
        self._state.path = str(self.engine_exe) if self.config.resolved_engine_exe() else ""

    def refresh_state(self) -> EngineState:
        with self._lock:
            if self.is_alive():
                self._state.state = "running"
                self._state.message = "BiliNote 引擎运行中"
                if self._proc is not None and self._proc.poll() is None:
                    self._state.pid = self._proc.pid
                    self._state.owned = True
                else:
                    self._state.pid = None
                    self._state.owned = False
            else:
                if self._proc is not None and self._proc.poll() is not None:
                    self._state.state = "error"
                    self._state.message = f"引擎已退出（exit={self._proc.returncode}）"
                elif self._state.state != "starting":
                    self._state.state = "stopped"
                    self._state.message = "引擎未运行"
            return self._state

    def start(self, timeout: float = 90.0) -> EngineState:
        with self._lock:
            if self.is_alive():
                self._set("running", "BiliNote 引擎运行中", owned=False)
                return self._state
            exe = self.engine_exe
            if not str(exe) or not exe.is_file():
                self._set("error", f"未找到 BiliNote 引擎：{exe or '（未设置）'}")
                return self._state
            ffmpeg = find_ffmpeg(self.config)
            if not ffmpeg.available:
                self._set("error", "未检测到 ffmpeg，请先在「引擎与 ffmpeg」或首次向导中下载安装。")
                return self._state

            workdir = Path(self.config.resolved_engine_workdir() or exe.parent)
            if not workdir.is_dir():
                workdir = exe.parent
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = open(self.log_path, "ab", buffering=0)
            except OSError as exc:
                self._set("error", f"无法写引擎日志：{exc}")
                return self._state

            env = os.environ.copy()
            ffmpeg = find_ffmpeg(self.config)
            if ffmpeg.bin_dir:
                env["FFMPEG_BIN_PATH"] = ffmpeg.bin_dir
            if self.config.proxy_enabled and self.config.proxy_url.strip():
                proxy = self.config.proxy_url.strip()
                env["HTTP_PROXY"] = env["HTTPS_PROXY"] = env["ALL_PROXY"] = proxy
            if self.config.hf_endpoint.strip():
                env["HF_ENDPOINT"] = self.config.hf_endpoint.strip()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            kwargs: dict = {
                "cwd": str(workdir),
                "stdout": self._log_handle,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.DEVNULL,
                "env": env,
            }
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

            self._set("starting", "正在启动 BiliNote 引擎…")
            try:
                self._proc = subprocess.Popen([str(exe)], **kwargs)
            except OSError as exc:
                self._set("error", f"启动引擎失败：{exc}")
                return self._state

            deadline = time.monotonic() + max(5.0, timeout)
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    tail = self.read_log_tail(20)
                    self._set("error", f"引擎启动后立刻退出（exit={self._proc.returncode}）。\n{tail}")
                    return self._state
                if self.api.is_alive(timeout=2.0):
                    self._set("running", "BiliNote 引擎运行中", pid=self._proc.pid, owned=True)
                    return self._state
                time.sleep(1.0)
            self._set("error", "等待 BiliNote 引擎启动超时，请查看引擎日志。", pid=self._proc.pid, owned=True)
            return self._state

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
            self._state = EngineState(state="stopped", message="引擎已停止", path=str(self.engine_exe))

    def restart(self) -> EngineState:
        self.stop()
        return self.start()

    def read_log_tail(self, lines: int = 80) -> str:
        try:
            data = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(data[-lines:])
        except OSError:
            return ""

    def health(self) -> dict:
        try:
            return {
                "ok": True,
                "sys_health": self.api.sys_health(),
                "deploy_status": self.api.deploy_status(),
            }
        except EngineAPIError as exc:
            return {"ok": False, "error": exc.message}

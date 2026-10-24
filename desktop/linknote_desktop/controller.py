"""桌面端控制器：把服务层包成 Qt 信号 + 后台任务，供 UI 使用。"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .config import DesktopConfig, agent_config_path, ensure_agent_config, log_dir, user_data_dir
from .services.agent_process import AgentProcess
from .services.autostart import is_enabled as autostart_is_enabled, set_enabled as autostart_set_enabled
from .services.bilinote_api import EngineAPI, friendly_error
from .services.engine import EngineManager
from .services.ffmpeg import download_ffmpeg, find_ffmpeg
from .services.relay import RelayService


class _WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)


class _Worker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        # autoDelete 会在 run() 返回后立刻销毁 C++ QRunnable，连带 QObject signals 一起销毁；
        # PySide 已排队到 GUI 线程的 result/error 回调会被丢掉。由 controller 强引用保活。
        self.setAutoDelete(False)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            value = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.error.emit(str(exc) or type(exc).__name__)
            except RuntimeError:
                # 退出过程中 QObject 已被销毁，忽略
                pass
        else:
            try:
                self.signals.result.emit(value)
            except RuntimeError:
                pass


class DesktopController(QObject):
    statusChanged = Signal(dict)
    engineChanged = Signal(dict)
    agentChanged = Signal(dict)
    pairingChanged = Signal(dict)
    ffmpegProgress = Signal(int, int)
    notification = Signal(str, str, str)  # title, message, level(info/success/warning/error)
    logAppended = Signal(str)
    error = Signal(str)

    def __init__(self, config: Optional[DesktopConfig] = None):
        super().__init__()
        self.config = config or DesktopConfig.load()
        self.config.ensure_dirs()
        ensure_agent_config(self.config)
        self.engine = EngineManager(self.config)
        self.relay = RelayService(self.config.relay_base_url)
        self.agent = AgentProcess(self.config)
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(4)
        self._pairing: dict[str, Any] = {}
        self._pair_timer = QTimer(self)
        self._pair_timer.setInterval(2000)
        self._pair_timer.timeout.connect(self._poll_pairing)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(4000)
        self._status_timer.timeout.connect(self.refresh_status_async)
        self._notify_timer = QTimer(self)
        self._notify_timer.setInterval(2500)
        self._notify_timer.timeout.connect(self._check_agent_events)
        self._last_agent_log_line = ""
        self._last_status: dict[str, Any] = {}
        self._active_workers: set[_Worker] = set()
        self._started = False

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._status_timer.start()
        self._notify_timer.start()
        # 首次启动由 onboarding 向导决定何时启动引擎（必须先有 ffmpeg）
        if self.config.onboarding_done:
            if self.config.engine_auto_start:
                self.start_engine_async(silent=True)
            if self.agent.is_paired:
                self.start_agent()

    def complete_onboarding(self) -> None:
        """首次配置向导完成后调用：落盘并启动引擎/Agent。"""
        self.config.onboarding_done = True
        self.config.save()
        ensure_agent_config(self.config)
        self.start_engine_async(silent=True)
        if self.agent.is_paired:
            self.start_agent()
        self.refresh_status_async()

    def ffmpeg_ready(self) -> bool:
        return find_ffmpeg(self.config).available
    def shutdown(self) -> None:
        self._status_timer.stop()
        self._notify_timer.stop()
        self._pair_timer.stop()
        try:
            self.agent.stop()
        finally:
            self.engine.stop()

    # ------------------------------------------------------------------ 基础异步
    def run_async(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> None:
        worker = _Worker(fn, *args, **kwargs)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_error:
            worker.signals.error.connect(on_error)
        else:
            worker.signals.error.connect(self.error.emit)
        # 保证 queued result/error 送达前 worker（以及 signals）不会被回收；
        # 延迟几秒再释放，避免和 GUI 线程的回调投递竞争。
        self._active_workers.add(worker)

        def _release_later(*_args: Any) -> None:
            QTimer.singleShot(10_000, lambda: self._active_workers.discard(worker))

        worker.signals.result.connect(_release_later)
        worker.signals.error.connect(_release_later)
        self.pool.start(worker)

    def _emit_error(self, message: str) -> None:
        self.error.emit(message)

    # ------------------------------------------------------------------ 状态
    def _collect_status(self) -> dict[str, Any]:
        engine_state = self.engine.refresh_state()
        token = self.agent.agent_token
        relay_info: dict[str, Any] = {"online": False, "error": ""}
        if token:
            try:
                relay_info = self.relay.me(token)
            except Exception as exc:  # noqa: BLE001
                relay_info = {"online": False, "error": str(exc)}
        paired = bool(token)
        devices = relay_info.get("devices") if isinstance(relay_info, dict) else None
        return {
            "engine": {
                "state": engine_state.state,
                "message": engine_state.message,
                "pid": engine_state.pid,
                "owned": engine_state.owned,
                "path": engine_state.path,
                "alive": engine_state.state == "running",
            },
            "agent": {
                "running": self.agent.running,
                "pid": self.agent.pid,
                "paired": paired,
                "error": self.agent.last_error,
            },
            "relay": {
                "url": self.config.relay_base_url,
                "online": bool(relay_info.get("online")),
                "agent": relay_info.get("agent") if isinstance(relay_info, dict) else None,
                "queue": relay_info.get("queue") if isinstance(relay_info, dict) else None,
                "devices": devices if isinstance(devices, list) else [],
                "error": relay_info.get("error", "") if isinstance(relay_info, dict) else "",
            },
            "ffmpeg": self._ffmpeg_status(),
        }

    def _ffmpeg_status(self) -> dict[str, Any]:
        info = find_ffmpeg(self.config)
        return {"available": info.available, "path": info.path, "bin_dir": info.bin_dir, "version": info.version}

    def refresh_status_async(self) -> None:
        self.run_async(self._collect_status, on_result=self._on_status)

    def _on_status(self, data: dict[str, Any]) -> None:
        self._last_status = data
        self.statusChanged.emit(data)

    @property
    def last_status(self) -> dict[str, Any]:
        return self._last_status

    # ------------------------------------------------------------------ 引擎
    def start_engine_async(self, silent: bool = False) -> None:
        def work():
            state = self.engine.start()
            return {
                "state": state.state,
                "message": state.message,
                "pid": state.pid,
                "owned": state.owned,
            }

        def done(value):
            self.engineChanged.emit(value)
            if value.get("state") == "running":
                if not silent:
                    self.notification.emit("LinkNote", "BiliNote 引擎已启动", "success")
            elif value.get("state") == "error":
                self.notification.emit("LinkNote", f"引擎启动失败：{value.get('message', '')}", "error")

        self.run_async(work, on_result=done, on_error=lambda m: self.engineChanged.emit({"state": "error", "message": m}))

    def stop_engine_async(self) -> None:
        def work():
            self.engine.stop()
            return {"state": "stopped", "message": "引擎已停止", "pid": None}

        self.run_async(work, on_result=self.engineChanged.emit)

    def restart_engine_async(self) -> None:
        def work():
            state = self.engine.restart()
            return {"state": state.state, "message": state.message, "pid": state.pid, "owned": state.owned}

        self.run_async(work, on_result=self.engineChanged.emit)

    def engine_api(self) -> EngineAPI:
        return self.engine.api

    # ------------------------------------------------------------------ Agent
    def start_agent(self) -> None:
        if self.agent.running:
            self.agentChanged.emit({"running": True, "pid": self.agent.pid})
            return
        ok = self.agent.start()
        self.agentChanged.emit({"running": self.agent.running, "pid": self.agent.pid, "error": self.agent.last_error})
        if not ok:
            self.notification.emit("LinkNote", self.agent.last_error or "Agent 启动失败", "error")

    def stop_agent(self) -> None:
        self.agent.stop()
        self.agentChanged.emit({"running": False, "pid": None})

    def restart_agent(self) -> None:
        self.agent.stop()
        self.start_agent()

    # ------------------------------------------------------------------ 配对
    def create_pairing_async(self, agent_name: str | None = None) -> None:
        name = agent_name or self.config.agent_name or "我的电脑"
        existing_token = self.agent.agent_token

        def work():
            if existing_token:
                data = self.relay.create_extra_pairing(existing_token)
                data["agent_token"] = existing_token
                data["agent_id"] = self.agent.read_config().get("agent_id", "")
            else:
                data = self.relay.create_pairing(name, "windows")
            self.agent.save_pairing(
                {
                    "agent_token": data.get("agent_token"),
                    "agent_id": data.get("agent_id"),
                    "pairing_id": data.get("pairing_id"),
                    "pairing_code": data.get("code"),
                    "pairing_expires_at": data.get("expires_at"),
                    "agent_name": name,
                    "paired_at": "",
                }
            )
            return data

        def done(data):
            self._pairing = {
                "pairing_id": data.get("pairing_id"),
                "code": data.get("code"),
                "expires_at": data.get("expires_at"),
                "status": "pending",
                "relay_url": self.config.relay_base_url.rstrip("/"),
            }
            self.pairingChanged.emit(dict(self._pairing))
            self._pair_timer.start()

        self.run_async(work, on_result=done, on_error=lambda m: self.error.emit(f"生成配对码失败：{m}"))

    def _poll_pairing(self) -> None:
        pairing_id = self._pairing.get("pairing_id")
        token = self.agent.agent_token
        if not pairing_id or not token:
            self._pair_timer.stop()
            return

        def work():
            return self.relay.get_pairing(pairing_id, token)

        def done(data):
            status = data.get("status", "pending")
            self._pairing.update(data)
            self._pairing["status"] = status
            if status == "claimed":
                self._pair_timer.stop()
                device = data.get("device") or {}
                self.agent.save_pairing(
                    {
                        "paired_at": device.get("paired_at") or "",
                        "device_id": device.get("id") or "",
                        "device_name": device.get("name") or "",
                        "device_platform": device.get("platform") or "",
                    }
                )
                self.pairingChanged.emit(dict(self._pairing))
                self.notification.emit("LinkNote", "手机配对成功，Agent 已启动", "success")
                self.start_agent()
                self.refresh_status_async()
            elif status == "expired":
                self._pair_timer.stop()
                self.pairingChanged.emit(dict(self._pairing))
                self.notification.emit("LinkNote", "配对码已过期，请重新生成", "warning")
            else:
                self.pairingChanged.emit(dict(self._pairing))

        self.run_async(work, on_result=done, on_error=lambda m: None)

    def cancel_pairing(self) -> None:
        pairing_id = self._pairing.get("pairing_id")
        token = self.agent.agent_token
        self._pair_timer.stop()
        self._pairing = {}
        self.pairingChanged.emit({})
        if pairing_id and token:
            self.run_async(
                self.relay.cancel_pairing,
                token,
                pairing_id,
                on_error=lambda m: None,
            )

    def unpair(self) -> None:
        """清除本机 Agent 配对凭据；手机端需要重新配对。"""
        self.agent.stop()
        self.agent.clear_pairing()
        self.agentChanged.emit({"running": False, "paired": False, "pid": None})
        self.refresh_status_async()
        self.notification.emit("LinkNote", "已解除本机配对，请重新生成配对码", "info")

    def pairing_state(self) -> dict[str, Any]:
        return dict(self._pairing)

    def revoke_device(self, device_id: str) -> None:
        token = self.agent.agent_token
        if not token:
            return

        def work():
            self.relay.revoke_device(token, device_id)
            return True

        self.run_async(work, on_result=lambda _: self.refresh_status_async())

    # ------------------------------------------------------------------ ffmpeg
    def download_ffmpeg_async(self) -> None:
        stop_event = threading.Event()

        def progress(done: int, total: int):
            self.ffmpegProgress.emit(done, total)

        def work():
            return download_ffmpeg(self.config, progress=progress, stop_event=stop_event)

        def done(info):
            self.notification.emit("LinkNote", "ffmpeg 安装完成", "success")
            self.refresh_status_async()
            self.engineChanged.emit({"state": "ffmpeg_ready", "message": info.version})

        self.run_async(work, on_result=done, on_error=lambda m: self.notification.emit("LinkNote", f"ffmpeg 下载失败：{m}", "error"))

    # ------------------------------------------------------------------ 配置
    def save_config(self) -> None:
        self.config.save()
        ensure_agent_config(self.config)
        self.relay.relay_base_url = self.config.relay_base_url
        self.engine.config = self.config
        self.agent.config = self.config
        self.statusChanged.emit(self._collect_status_safe())

    def _collect_status_safe(self) -> dict[str, Any]:
        try:
            return self._collect_status()
        except Exception:
            return {}

    def set_autostart(self, enabled: bool) -> None:
        autostart_set_enabled(enabled)
        self.config.autostart = enabled
        self.config.save()

    def autostart_enabled(self) -> bool:
        return autostart_is_enabled()

    # ------------------------------------------------------------------ 通知/日志
    def _check_agent_events(self) -> None:
        line = self.agent.tail_raw()
        if not line or line == self._last_agent_log_line:
            return
        self._last_agent_log_line = line
        self.logAppended.emit(line)
        if "任务完成并已回传" in line:
            self.notification.emit("LinkNote", "笔记生成完成，手机端已可查看", "success")
        elif "任务失败已回传" in line:
            self.notification.emit("LinkNote", "笔记生成失败，请在手机端查看原因", "warning")
        elif "配对已失效" in line:
            self.notification.emit("LinkNote", "配对已失效，请重新配对", "error")

    def agent_log_tail(self, lines: int = 120) -> str:
        return self.agent.log_tail(lines)

    def engine_log_tail(self, lines: int = 120) -> str:
        return self.engine.read_log_tail(lines)

    def data_paths(self) -> dict[str, str]:
        return {
            "data": str(user_data_dir()),
            "logs": str(log_dir()),
            "agent_config": str(agent_config_path()),
            "engine_log": str(self.engine.log_path),
            "agent_log": str(self.agent.log_path),
        }




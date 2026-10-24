"""桌面端控制器：把服务层包成 Qt 信号 + 后台任务，供 UI 使用。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

import httpx

from linknote_agent.stage_map import format_elapsed as _format_elapsed
from linknote_agent.stage_map import map_status as _map_status

from .config import (
    APP_VERSION,
    DesktopConfig,
    agent_config_path,
    ensure_agent_config,
    log_dir,
    user_data_dir,
)
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
    # 新增：当前任务 / 历史笔记快照（后台线程采集后通过信号送到 GUI）
    tasksChanged = Signal(dict)
    historyChanged = Signal(dict)
    archiveChanged = Signal(dict)  # 自动/手动归档结果

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
        self._last_tasks: dict[str, Any] = {}
        self._active_workers: set[_Worker] = set()
        self._archive_inflight = False
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
        self._maybe_auto_archive()

    def complete_onboarding(self) -> None:
        """首次配置向导完成后调用：落盘并启动引擎/Agent。"""
        self.config.onboarding_done = True
        self.config.save()
        ensure_agent_config(self.config)
        self.start_engine_async(silent=True)
        if self.agent.is_paired:
            self.start_agent()
        self.refresh_status_async()
        self._maybe_auto_archive()

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
            self._maybe_auto_archive()
        elif "任务失败已回传" in line:
            self.notification.emit("LinkNote", "笔记生成失败，请在手机端查看原因", "warning")
        elif "配对已失效" in line:
            self.notification.emit("LinkNote", "配对已失效，请重新配对", "error")

    def agent_log_tail(self, lines: int = 120) -> str:
        return self.agent.log_tail(lines)

    def engine_log_tail(self, lines: int = 120) -> str:
        return self.engine.read_log_tail(lines)

    def data_paths(self) -> dict[str, str]:
        notes_dir, outbox_dir = self.history_dirs()
        return {
            "data": str(user_data_dir()),
            "logs": str(log_dir()),
            "agent_config": str(agent_config_path()),
            "engine_log": str(self.engine.log_path),
            "agent_log": str(self.agent.log_path),
            "notes": str(notes_dir),
            "outbox": str(outbox_dir),
        }

    # ================================================================== 消息入口（Bot 凭证）
    def test_telegram_bot(self, token: str) -> dict[str, Any]:
        """Telegram `getMe` 连通性测试。

        注意：请求 URL 本身包含 Bot Token，因此错误信息只保留异常类型，
        绝不把 token / 带 token 的 URL 带进异常、日志或 UI 弹窗。
        """
        token_text = str(token or "").strip()
        if not token_text:
            raise ValueError("请先填写 Telegram Bot Token")
        try:
            with httpx.Client(timeout=12.0, trust_env=False) as client:
                response = client.get(f"https://api.telegram.org/bot{token_text}/getMe")
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"无法连接 Telegram（{type(exc).__name__}），请检查网络或代理"
            ) from None
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(
                f"Telegram 返回了非 JSON 响应（HTTP {response.status_code}）"
            ) from None
        payload = payload if isinstance(payload, dict) else {}
        if response.status_code == 200 and payload.get("ok"):
            result = payload.get("result")
            result = result if isinstance(result, dict) else {}
            return {
                "ok": True,
                "username": str(result.get("username") or "").strip(),
                "name": str(result.get("first_name") or "").strip(),
                "bot_id": result.get("id"),
            }
        description = str(payload.get("description") or "").strip()
        raise RuntimeError(
            "Telegram 验证失败：" + (description or "Token 无效，或网络无法直连 Telegram")
        )

    def test_qq_bot(self, app_id: str, app_secret: str) -> dict[str, Any]:
        """QQ 开放平台 `getAppAccessToken` 测试；只验证可行，不保存 access_token。"""
        app_id_text = str(app_id or "").strip()
        secret_text = str(app_secret or "").strip()
        if not app_id_text:
            raise ValueError("请先填写 QQ AppID")
        if not secret_text:
            raise ValueError("请先填写 QQ AppSecret")
        body = {"appId": app_id_text, "clientSecret": secret_text}
        try:
            with httpx.Client(timeout=15.0, trust_env=False) as client:
                response = client.post(
                    "https://api.bot.qq.com/app/getAppAccessToken", json=body
                )
        except httpx.HTTPError as exc:
            # 请求体里有 AppSecret：错误信息只用异常类型，避免凭证泄漏
            raise RuntimeError(
                f"无法连接 QQ 开放平台（{type(exc).__name__}），请检查网络或代理"
            ) from None
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(
                f"QQ 开放平台返回了非 JSON 响应（HTTP {response.status_code}）"
            ) from None
        payload = payload if isinstance(payload, dict) else {}
        access_token = str(payload.get("access_token") or "").strip()
        if response.status_code == 200 and access_token:
            expires_in = 0
            raw_expires = payload.get("expires_in")
            if raw_expires is not None:
                try:
                    expires_in = int(raw_expires)
                except (TypeError, ValueError):
                    expires_in = 0
            return {"ok": True, "expires_in": expires_in}
        message = str(
            payload.get("message") or payload.get("msg") or payload.get("error") or ""
        ).strip()
        raise RuntimeError(
            "QQ 验证失败："
            + (message or f"HTTP {response.status_code}，请检查 AppID / AppSecret")
        )

    # ================================================================== 笔记自动归档
    def archive_status(self) -> dict[str, Any]:
        """设置页展示用：归档开关 / 目录是否可用（不写文件）。"""
        directory = str(getattr(self.config, "archive_dir", "") or "")
        enabled = bool(getattr(self.config, "archive_enabled", False))
        result: dict[str, Any] = {
            "enabled": enabled,
            "dir": directory,
            "configured": bool(directory),
            "exists": False,
            "error": "",
        }
        if not directory:
            result["error"] = "还没有选择归档目录"
            return result
        try:
            path = Path(directory).expanduser()
            result["absolute"] = path.is_absolute()
            result["exists"] = path.is_dir()
            if not path.is_absolute():
                result["error"] = "归档目录必须是绝对路径"
            elif not path.exists():
                result["error"] = f"归档目录不存在：{path}"
            elif not path.is_dir():
                result["error"] = f"归档路径不是文件夹：{path}"
        except OSError as exc:
            result["error"] = f"归档目录不可用：{exc}"
        return result

    def archive_notes(self) -> dict[str, Any]:
        """扫描本机 notes/*.md，把新笔记复制到 Obsidian/本地归档目录。

        在后台线程执行；返回 exported/skipped/errors 等结构化结果。
        """
        notes_dir, outbox_dir = self.history_dirs()
        try:
            events = parse_agent_task_events(self.agent.log_tail(300))
        except Exception:  # noqa: BLE001
            events = []
        model = "/".join(
            part
            for part in [
                str(self.config.provider_id or ""),
                str(self.config.model_name or ""),
            ]
            if part
        )
        result = archive_notes_to_dir(
            notes_dir,
            outbox_dir,
            str(getattr(self.config, "archive_dir", "") or ""),
            model=model,
            app_version=APP_VERSION,
            template=str(getattr(self.config, "archive_template", "") or ""),
            task_events=events,
        )
        result["enabled"] = bool(getattr(self.config, "archive_enabled", False))
        result["dir"] = str(getattr(self.config, "archive_dir", "") or "")
        return result

    def archive_notes_async(self, on_result=None, on_error=None) -> None:
        self.run_async(
            self.archive_notes,
            on_result=on_result,
            on_error=on_error or self.error.emit,
        )

    def _maybe_auto_archive(self) -> None:
        """任务完成/应用启动时自动归档（开启归档且目录有效才执行）。"""
        if not bool(getattr(self.config, "archive_enabled", False)):
            return
        if not str(getattr(self.config, "archive_dir", "") or "").strip():
            return
        if self._archive_inflight:
            return
        self._archive_inflight = True

        def done(result: dict[str, Any]) -> None:
            self._archive_inflight = False
            data = result if isinstance(result, dict) else {}
            self.archiveChanged.emit(data)
            exported = int(data.get("exported") or 0)
            if exported:
                self.notification.emit(
                    "LinkNote", f"已自动归档 {exported} 篇笔记", "success"
                )
            elif data.get("error"):
                self.notification.emit(
                    "LinkNote", f"自动归档失败：{data.get('error')}", "warning"
                )

        def failed(message: str) -> None:
            self._archive_inflight = False
            self.notification.emit("LinkNote", f"自动归档失败：{message}", "warning")

        self.archive_notes_async(on_result=done, on_error=failed)

    # ================================================================== 当前任务
    def collect_task_snapshot(self) -> dict[str, Any]:
        """汇总当前任务 / 队列 / 最近完成失败（Agent 日志 + Relay + 引擎状态）。

        在后台线程里执行；任何子项失败都降级成字段错误，不抛出给 GUI。
        """
        try:
            log_text = self.agent.log_tail(500)
            events = parse_agent_task_events(log_text)
            relay_me: dict[str, Any] = {}
            relay_error = ""
            token = self.agent.agent_token
            if token:
                try:
                    relay_me = self.relay.me(token) or {}
                except Exception as exc:  # noqa: BLE001
                    relay_error = str(exc)
            outbox_pending = 0
            try:
                _, outbox_dir = self.history_dirs()
                outbox_pending = len(list(outbox_dir.glob("*.json"))) if outbox_dir.is_dir() else 0
            except OSError:
                outbox_pending = 0
            snapshot = build_task_snapshot(
                events,
                relay_me=relay_me,
                agent_running=self.agent.running,
                agent_pid=self.agent.pid,
                agent_paired=self.agent.is_paired,
                outbox_pending=outbox_pending,
            )
            snapshot["relay_error"] = relay_error
            snapshot["relay_url"] = self.config.relay_base_url
            snapshot["agent_name"] = self.config.agent_name or "我的电脑"
            snapshot["engine"] = {
                "state": ((self._last_status or {}).get("engine") or {}).get("state", "stopped")
            }
            current = snapshot.get("current")
            if current and current.get("bilinote_task"):
                self._enrich_live_progress(current)
            if current and current.get("started_at"):
                try:
                    started = datetime.fromisoformat(str(current["started_at"]))
                    current["elapsed_seconds"] = max(
                        0.0, (datetime.now() - started).total_seconds()
                    )
                except (TypeError, ValueError):
                    current["elapsed_seconds"] = 0.0
            for item in snapshot.get("recent") or []:
                item.setdefault("elapsed_seconds", 0.0)
            return snapshot
        except Exception as exc:  # noqa: BLE001 - 任务快照失败不应让页面崩溃
            return {
                "error": f"读取任务状态失败：{exc}",
                "current": None,
                "recent": [],
                "queue": {"queued": 0, "running": 0},
                "agent": {"running": False, "paired": False},
                "engine": {"state": "unknown"},
            }

    def _enrich_live_progress(self, current: dict[str, Any]) -> None:
        """用 BiliNote `/task_status/<id>` 给当前任务补实时 stage/progress。"""
        bilinote_task = str(current.get("bilinote_task") or "")
        if not bilinote_task:
            return
        try:
            payload = self.engine_api()._request(  # noqa: SLF001 - EngineAPI 未封装该只读接口
                "GET", f"/task_status/{quote(bilinote_task, safe='')}", timeout=5.0
            )
        except Exception:  # noqa: BLE001 - 引擎没起来就沿用日志里的状态
            return
        if not isinstance(payload, dict):
            return
        backend_status = str(payload.get("status") or "").upper()
        current["backend_status"] = backend_status
        if backend_status == "FAILED":
            current["message"] = str(payload.get("message") or "BiliNote 任务失败")
            return
        if backend_status == "SUCCESS":
            current["progress"] = max(int(current.get("progress") or 0), 99)
            current["message"] = "笔记已生成，正在回传手机…"
            return
        update = _map_status(backend_status)
        if update is not None:
            current["stage"] = update.stage
            current["progress"] = max(int(current.get("progress") or 0), update.progress)
            current["message"] = update.message

    def refresh_tasks_async(self) -> None:
        self.run_async(
            self.collect_task_snapshot,
            on_result=self._on_tasks,
            on_error=lambda m: self.tasksChanged.emit(
                {"error": f"读取任务状态失败：{m}", "current": None, "recent": []}
            ),
        )

    def _on_tasks(self, data: dict[str, Any]) -> None:
        self._last_tasks = data or {}
        self.tasksChanged.emit(self._last_tasks)

    @property
    def last_tasks(self) -> dict[str, Any]:
        return self._last_tasks

    # ================================================================== 历史笔记
    def history_dirs(self) -> tuple[Path, Path]:
        """notes / outbox 目录：优先取 Agent 配置里的覆盖值。"""
        notes_dir = user_data_dir() / "notes"
        outbox_dir = user_data_dir() / "outbox"
        try:
            cfg = self.agent.read_config()
        except Exception:  # noqa: BLE001
            cfg = {}
        raw_notes = str(cfg.get("notes_dir") or "").strip()
        raw_outbox = str(cfg.get("outbox_dir") or "").strip()
        if raw_notes:
            notes_dir = Path(raw_notes)
        if raw_outbox:
            outbox_dir = Path(raw_outbox)
        return notes_dir, outbox_dir

    def collect_history_snapshot(self) -> dict[str, Any]:
        try:
            notes_dir, outbox_dir = self.history_dirs()
            return scan_history_entries(notes_dir, outbox_dir)
        except Exception as exc:  # noqa: BLE001
            return {
                "error": f"读取历史笔记失败：{exc}",
                "items": [],
                "counts": {"notes": 0, "pending": 0, "total": 0, "words": 0},
            }

    def refresh_history_async(self) -> None:
        self.run_async(
            self.collect_history_snapshot,
            on_result=self._on_history,
            on_error=lambda m: self.historyChanged.emit(
                {"error": f"读取历史笔记失败：{m}", "items": [], "counts": {}}
            ),
        )

    def _on_history(self, data: dict[str, Any]) -> None:
        self.historyChanged.emit(data or {})

    def load_note_markdown(self, path: str) -> str:
        """读取一条本地笔记内容（限制在 notes/outbox 目录内，避免任意文件读取）。"""
        target = Path(str(path or "")).expanduser().resolve()
        notes_dir, outbox_dir = self.history_dirs()
        allowed = [notes_dir.resolve(), outbox_dir.resolve()]
        if not any(target.is_relative_to(directory) for directory in allowed):
            raise ValueError("只允许读取 LinkNote 笔记目录内的文件")
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在：{target}")
        if target.suffix.lower() == ".json":
            data = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                payload = data.get("payload")
                if isinstance(payload, dict):
                    return str(payload.get("markdown") or "")
            return ""
        return target.read_text(encoding="utf-8", errors="replace")

    # ================================================================== AI 模型列表
    def fetch_provider_model_catalog(self, provider_id: str) -> dict[str, Any]:
        """取某供应商的「可用模型」+「已配置到 BiliNote 模型库的模型」。

        动态列表来自 BiliNote `/model_list/{provider_id}`（内部会做内置回退），
        该只读接口未在 EngineAPI 封装，这里直接调用其 `_request`。
        """
        pid = str(provider_id or "")
        result: dict[str, Any] = {
            "provider_id": pid,
            "provider_models": [],
            "db_models": [],
            "error": "",
        }
        api = self.engine_api()
        try:
            payload = api._request("GET", f"/model_list/{quote(pid, safe='')}", timeout=20.0)  # noqa: SLF001
            raw: Any = []
            if isinstance(payload, dict):
                raw = payload.get("models") or []
                if payload.get("error"):
                    result["error"] = str(payload["error"])
            elif isinstance(payload, list):
                raw = payload
            names: list[str] = []
            for item in raw if isinstance(raw, list) else []:
                if isinstance(item, dict):
                    name = str(
                        item.get("model_name") or item.get("id") or item.get("name") or ""
                    ).strip()
                else:
                    name = str(item).strip()
                if name and name not in names:
                    names.append(name)
            result["provider_models"] = names
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
        try:
            db_models = api.models()
            result["db_models"] = [
                str(item.get("model_name"))
                for item in db_models
                if isinstance(item, dict)
                and str(item.get("provider_id")) == pid
                and item.get("model_name")
            ]
        except Exception as exc:  # noqa: BLE001
            result.setdefault("db_error", str(exc))
        return result


# ====================================================================== 任务日志解析
# Agent 日志格式（linknote_agent/logging_setup.py）：
#   2026-10-24 16:05:00,123 [INFO] linknote_agent.runner: 领到任务 task_id=... url=... platform=...
# 这些行是桌面端唯一能拿到的「Agent 正在干什么」实时信息；解析成事件后由
# build_task_snapshot 汇总成「当前任务 / 队列 / 最近完成失败」。

_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s+"
    r"\[(?P<level>[A-Z]+)\]\s+[^:]+:\s*(?P<message>.*)$"
)

_TASK_EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "start",
        re.compile(
            r"^领到任务 task_id=(?P<task_id>\S+) url=(?P<url>\S+) platform=(?P<platform>\S*)"
        ),
    ),
    (
        "submitted",
        re.compile(
            r"^已提交 BiliNote 任务 relay_task=(?P<task_id>\S+) "
            r"bilinote_task=(?P<bilinote_task>\S+) platform=(?P<platform>\S*)"
        ),
    ),
    (
        "completed",
        re.compile(
            r"^任务完成并已回传 task_id=(?P<task_id>\S+) title=(?P<title>.*?) "
            r"markdown=(?P<markdown_chars>\d+) 字 duration=(?P<duration>\S*) 秒"
        ),
    ),
    (
        "failed",
        re.compile(
            r"^任务失败已回传 task_id=(?P<task_id>\S+) code=(?P<code>\S+) message=(?P<message>.*)"
        ),
    ),
    (
        "pending_note",
        re.compile(r"^任务 (?P<task_id>\S+) 已生成笔记但 Relay 暂时不可达"),
    ),
    (
        "pending_fail",
        re.compile(r"^任务 (?P<task_id>\S+) 的失败状态无法回传"),
    ),
    (
        "note_saved",
        re.compile(r"^任务 (?P<task_id>\S+) 的笔记无法回传"),
    ),
    (
        "lost",
        re.compile(r"^任务 (?P<task_id>\S+) 已不再归本 Agent 处理：(?P<message>.*)"),
    ),
)

_HEADING_RE = re.compile(r"^\s{0,3}#\s+(?P<title>.+?)\s*$", re.MULTILINE)
_WORD_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_log_timestamp(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value).replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _format_ts_label(value: Any) -> str:
    """ISO / 日志时间字符串 → 本地 'MM-DD HH:MM'。"""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = _parse_log_timestamp(text)
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:16]
    try:
        parsed = parsed.astimezone()
    except (OSError, ValueError):
        pass
    return parsed.strftime("%m-%d %H:%M")


def _match_task_event(message: str) -> Optional[dict[str, Any]]:
    text = (message or "").strip()
    for kind, pattern in _TASK_EVENT_PATTERNS:
        match = pattern.match(text)
        if match is not None:
            event: dict[str, Any] = {"kind": kind}
            event.update({key: value for key, value in match.groupdict().items()})
            return event
    return None


def parse_agent_task_events(text: str, limit: int = 120) -> list[dict[str, Any]]:
    """把 Agent 日志里与任务相关的行解析为按时间排序的事件列表（可单测）。"""
    events: list[dict[str, Any]] = []
    for raw in (text or "").splitlines():
        match = _LOG_LINE_RE.match(raw.strip())
        if match is None:
            continue
        event = _match_task_event(match.group("message"))
        if event is None:
            continue
        timestamp = _parse_log_timestamp(match.group("ts"))
        event["timestamp"] = timestamp
        event["ts"] = timestamp.isoformat(timespec="seconds") if timestamp else ""
        event["time_label"] = timestamp.strftime("%m-%d %H:%M:%S") if timestamp else ""
        events.append(event)
    if limit and limit > 0:
        return events[-limit:]
    return events


def build_task_snapshot(
    events: list[dict[str, Any]],
    *,
    relay_me: Optional[dict[str, Any]] = None,
    agent_running: bool = False,
    agent_pid: Optional[int] = None,
    agent_paired: bool = False,
    outbox_pending: int = 0,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """把任务事件汇总成「当前任务 / 队列 / 最近完成失败」快照（纯函数，可单测）。"""
    now = now or datetime.now()
    tasks: dict[str, dict[str, Any]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            continue
        state = tasks.setdefault(
            task_id,
            {
                "task_id": task_id,
                "url": "",
                "platform": "",
                "title": "",
                "bilinote_task": "",
                "stage": "",
                "progress": 0,
                "status": "running",
                "message": "",
                "started_at": "",
                "updated_at": "",
                "markdown_chars": 0,
                "duration_seconds": None,
                "error": "",
            },
        )
        kind = str(event.get("kind") or "")
        if event.get("ts"):
            state["updated_at"] = str(event["ts"])
        if kind == "start":
            state["status"] = "running"
            state["url"] = str(event.get("url") or state["url"])
            platform = str(event.get("platform") or "").strip()
            if platform:
                state["platform"] = platform
            if not state["started_at"]:
                state["started_at"] = str(event.get("ts") or "")
            state["message"] = "任务已领取，正在准备处理"
        elif kind == "submitted":
            state["bilinote_task"] = str(event.get("bilinote_task") or state["bilinote_task"])
            state["stage"] = "parsing"
            state["progress"] = max(2, _safe_int(state.get("progress")))
            state["message"] = "已提交 BiliNote 任务"
            if not state["started_at"]:
                state["started_at"] = str(event.get("ts") or "")
            if not state["platform"]:
                state["platform"] = str(event.get("platform") or "").strip()
        elif kind == "completed":
            state["status"] = "completed"
            title = str(event.get("title") or state["title"]).strip()
            if title:
                state["title"] = title
            state["markdown_chars"] = _safe_int(event.get("markdown_chars"))
            duration = _safe_int(event.get("duration"))
            if duration:
                state["duration_seconds"] = duration
            state["stage"] = "done"
            state["progress"] = 100
            state["message"] = "笔记已生成并回传手机"
        elif kind == "failed":
            state["status"] = "failed"
            state["stage"] = "failed"
            state["error"] = str(event.get("code") or "")
            state["message"] = str(event.get("message") or "生成失败").strip()
        elif kind == "pending_note":
            state["status"] = "pending"
            state["message"] = "笔记待回传（Relay 暂时不可达，会自动重试）"
        elif kind == "pending_fail":
            state["status"] = "failed"
            state["message"] = "失败状态待回传（Relay 暂时不可达）"
        elif kind == "note_saved":
            state["status"] = "failed"
            state["message"] = "笔记已保存到本机「历史笔记」"
        elif kind == "lost":
            state["status"] = "lost"
            state["message"] = str(event.get("message") or "任务已不再归本机处理").strip()

    ordered = sorted(tasks.values(), key=lambda item: str(item.get("updated_at") or ""))
    running = [item for item in ordered if item.get("status") == "running"]
    current = running[-1] if running else None
    finished = [
        item
        for item in ordered
        if item.get("status") in ("completed", "failed", "pending", "lost")
    ]
    finished.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    recent = [
        {
            "task_id": item["task_id"],
            "title": item.get("title") or "",
            "platform": item.get("platform") or "",
            "url": item.get("url") or "",
            "status": item.get("status"),
            "message": item.get("message") or "",
            "error": item.get("error") or "",
            "updated_at": item.get("updated_at") or "",
            "time_label": _format_ts_label(item.get("updated_at")),
            "markdown_chars": _safe_int(item.get("markdown_chars")),
            "duration_seconds": item.get("duration_seconds"),
        }
        for item in finished[:8]
    ]

    relay_me = relay_me or {}
    queue = relay_me.get("queue") if isinstance(relay_me.get("queue"), dict) else {}
    relay_agent = relay_me.get("agent") if isinstance(relay_me.get("agent"), dict) else {}
    devices = relay_me.get("devices")
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_label": now.strftime("%H:%M:%S"),
        "current": dict(current) if current is not None else None,
        "recent": recent,
        "queue": {
            "queued": _safe_int(queue.get("queued")),
            "running": _safe_int(queue.get("running")),
        },
        "relay_online": bool(relay_me.get("online")) or bool(relay_agent.get("online")),
        "agent_online": bool(relay_agent.get("online")),
        "devices": len(devices) if isinstance(devices, list) else 0,
        "agent": {
            "running": bool(agent_running),
            "pid": agent_pid,
            "paired": bool(agent_paired),
        },
        "outbox_pending": _safe_int(outbox_pending),
        "error": "",
    }


# ====================================================================== 历史笔记
def count_words(text: str) -> int:
    """中文字符按字计，英文/数字按词计。"""
    return len(_WORD_RE.findall(text or ""))


def _note_title_from_markdown(text: str, fallback: str = "") -> str:
    match = _HEADING_RE.search(text or "")
    title = match.group("title").strip().strip("#").strip() if match else ""
    return (title or fallback or "未命名笔记")[:120]


def _iso_from_timestamp(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
    except (OSError, ValueError, OverflowError):
        return ""


def scan_history_entries(
    notes_dir: Path | str,
    outbox_dir: Path | str,
    limit: int = 200,
) -> dict[str, Any]:
    """扫描本地 notes/*.md 与 outbox 里待回传的笔记（纯文件读取，可单测）。"""
    notes_path = Path(notes_dir)
    outbox_path = Path(outbox_dir)
    items: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    notes_count = 0
    pending_count = 0
    failed_pending = 0

    if notes_path.is_dir():
        note_files = []
        for path in notes_path.glob("*.md"):
            try:
                note_files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        for mtime, path in sorted(note_files, key=lambda pair: pair[0], reverse=True):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
            except OSError:
                continue
            notes_count += 1
            stem = path.stem
            task_id = stem if re.fullmatch(r"[0-9a-fA-F-]{8,}", stem) else ""
            if task_id:
                seen_task_ids.add(task_id)
            items.append(
                {
                    "id": f"note:{path.name}",
                    "task_id": task_id,
                    "title": _note_title_from_markdown(text, stem),
                    "platform": "",
                    "url": "",
                    "created_at": _iso_from_timestamp(mtime),
                    "time_label": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                    "status": "saved",
                    "status_label": "已保存本机",
                    "source": "notes",
                    "path": str(path),
                    "markdown_chars": len(text),
                    "word_count": count_words(text),
                    "size_bytes": len(text.encode("utf-8")),
                }
            )

    if outbox_path.is_dir():
        for path in sorted(outbox_path.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            kind = str(data.get("kind") or "")
            if kind == "fail":
                failed_pending += 1
                continue
            if kind != "complete":
                continue
            pending_count += 1
            task_id = str(data.get("task_id") or path.stem)
            if task_id in seen_task_ids:
                continue
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            markdown = str(payload.get("markdown") or "")
            created = str(data.get("created_at") or "")
            try:
                size_bytes = path.stat().st_size
            except OSError:
                size_bytes = 0
            items.append(
                {
                    "id": f"outbox:{path.name}",
                    "task_id": task_id,
                    "title": str(payload.get("title") or "未命名笔记")[:120],
                    "platform": str(meta.get("platform") or ""),
                    "url": str(meta.get("url") or ""),
                    "created_at": created,
                    "time_label": _format_ts_label(created),
                    "status": "pending",
                    "status_label": "待回传",
                    "source": "outbox",
                    "path": str(path),
                    "markdown_chars": len(markdown),
                    "word_count": count_words(markdown),
                    "size_bytes": size_bytes,
                }
            )

    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "items": items[:limit],
        "counts": {
            "notes": notes_count,
            "pending": pending_count,
            "failed_pending": failed_pending,
            "total": len(items),
            "words": sum(_safe_int(item.get("word_count")) for item in items),
        },
        "notes_dir": str(notes_path),
        "outbox_dir": str(outbox_path),
        "error": "",
    }


# ====================================================================== 自动归档
# 归档目标 = Obsidian vault 或任意本地目录；把 notes/*.md 复制过去并补 YAML frontmatter。
# 已存在的同名文件不覆盖（幂等，避免每次任务完成都重复导出）。

_ARCHIVE_INVALID_CHARS = set('<>"|?*')


def _yaml_scalar(value: Any) -> str:
    """把任意值渲染成安全的双引号 YAML 字符串。"""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_archive_frontmatter(meta: dict[str, Any]) -> str:
    """生成 Obsidian 兼容的 YAML frontmatter（字段固定、缺失留空）。"""
    lines = ["---"]
    for key in (
        "title",
        "source_url",
        "platform",
        "created_at",
        "model",
        "app_version",
    ):
        lines.append(f'{key}: "{_yaml_scalar(meta.get(key))}"')
    lines.append("---")
    return "\n".join(lines) + "\n"


def build_archived_note(markdown: str, meta: dict[str, Any]) -> str:
    """给 Markdown 正文补 frontmatter；正文本身已有 frontmatter 时原样保留。"""
    body = str(markdown or "").strip()
    if not body:
        return ""
    if body.startswith("---") and re.match(r"^---\s*\n.*?\n---\s*(\n|$)", body, re.DOTALL):
        return body + ("\n" if not body.endswith("\n") else "")
    return build_archive_frontmatter(meta) + "\n" + body + "\n"


def _archive_meta_index(
    outbox_dir: Path,
    task_events: Optional[list[dict[str, Any]]] = None,
) -> dict[str, dict[str, Any]]:
    """按 task_id 汇总 outbox / Agent 日志里能拿到的笔记元数据。"""
    index: dict[str, dict[str, Any]] = {}
    for event in task_events or []:
        if not isinstance(event, dict):
            continue
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            continue
        item = index.setdefault(task_id, {})
        kind = str(event.get("kind") or "")
        if kind == "start":
            if event.get("url"):
                item["source_url"] = str(event["url"])
            if event.get("platform"):
                item["platform"] = str(event["platform"]).strip()
        elif kind == "submitted":
            if event.get("platform"):
                item["platform"] = str(event["platform"]).strip()
            if event.get("ts") and not item.get("created_at"):
                item["created_at"] = str(event["ts"])
        elif kind == "completed":
            if event.get("title"):
                item["title"] = str(event["title"]).strip()
            if event.get("ts"):
                item["created_at"] = str(event["ts"])
    if outbox_dir.is_dir():
        for path in outbox_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or str(data.get("kind") or "") != "complete":
                continue
            task_id = str(data.get("task_id") or path.stem)
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            item = index.setdefault(task_id, {})
            if payload.get("title"):
                item["title"] = str(payload["title"]).strip()
            if meta.get("url"):
                item["source_url"] = str(meta["url"])
            if meta.get("platform"):
                item["platform"] = str(meta["platform"])
            if data.get("created_at"):
                item["created_at"] = str(data["created_at"])
    return index


def _safe_archive_filename(name: str, fallback: str = "note") -> str:
    raw = str(name or "")
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    text = re.sub(r"[^\w\u4e00-\u9fff .-]+", "-", raw).strip(" .-")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback
    return f"{text[:80]}.md"


def archive_notes_to_dir(
    notes_dir: Path | str,
    outbox_dir: Path | str,
    archive_dir: Path | str,
    *,
    model: str = "",
    app_version: str = "",
    template: str = "",
    task_events: Optional[list[dict[str, Any]]] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """把 notes/*.md 复制到归档目录并补 frontmatter（纯文件操作，可单测）。"""
    result: dict[str, Any] = {
        "exported": 0,
        "skipped": 0,
        "failed": 0,
        "total": 0,
        "errors": [],
        "dir": str(archive_dir or ""),
        "error": "",
    }
    raw_dir = str(archive_dir or "").strip()
    if not raw_dir:
        result["error"] = "还没有选择归档目录"
        return result
    invalid = sorted(ch for ch in raw_dir if ch in _ARCHIVE_INVALID_CHARS)
    if invalid:
        result["error"] = f"归档目录包含非法字符：{' '.join(invalid)}"
        return result
    target_dir = Path(raw_dir).expanduser()
    try:
        if not target_dir.is_absolute():
            result["error"] = "归档目录必须是绝对路径"
            return result
        target_dir = target_dir.resolve()
    except OSError as exc:
        result["error"] = f"归档目录不可用：{exc}"
        return result
    if not target_dir.exists():
        result["error"] = f"归档目录不存在：{target_dir}"
        return result
    if not target_dir.is_dir():
        result["error"] = f"归档路径不是文件夹：{target_dir}"
        return result

    notes_path = Path(notes_dir)
    outbox_path = Path(outbox_dir)
    meta_index = _archive_meta_index(outbox_path, task_events)
    files: list[tuple[float, Path]] = []
    if notes_path.is_dir():
        for path in notes_path.glob("*.md"):
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
    files.sort(key=lambda pair: pair[0])
    result["total"] = len(files[:limit])
    for mtime, source in files[:limit]:
        try:
            markdown = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result["failed"] += 1
            result["errors"].append(f"{source.name}: {exc}")
            continue
        task_id = source.stem
        meta = dict(meta_index.get(task_id) or {})
        heading_match = _HEADING_RE.search(markdown or "")
        title = (
            str(meta.get("title") or "").strip()
            or (heading_match.group("title").strip() if heading_match else "")
            or source.stem
        )
        created_at = str(meta.get("created_at") or "").strip()
        if not created_at:
            created_at = _iso_from_timestamp(mtime)
        front_meta = {
            "title": title,
            "source_url": str(meta.get("source_url") or ""),
            "platform": str(meta.get("platform") or ""),
            "created_at": created_at,
            "model": str(model or ""),
            "app_version": str(app_version or ""),
        }
        if template:
            try:
                date_label = str(created_at)[:10]
                rendered = template.format(
                    title=title,
                    task_id=task_id,
                    platform=front_meta["platform"] or "unknown",
                    date=date_label,
                    created_at=created_at,
                )
                filename = _safe_archive_filename(rendered, task_id)
            except (KeyError, IndexError, ValueError):
                filename = _safe_archive_filename(source.name, task_id)
        else:
            filename = _safe_archive_filename(source.name, task_id)
        target = target_dir / filename
        if target.exists():
            result["skipped"] += 1
            continue
        content = build_archived_note(markdown, front_meta)
        if not content:
            result["skipped"] += 1
            continue
        tmp = target.with_name(target.name + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(target)
            result["exported"] += 1
        except OSError as exc:
            result["failed"] += 1
            result["errors"].append(f"{filename}: {exc}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return result




"""BiliNote 后端进程管理：先探测，没起来就自动拉起 ``BiliNoteBackend.exe``。

经验（来自 wecom_bot 的 ``ensure_backend``）：
- 不能用 ``DETACHED_PROCESS``：子进程没有有效的 std 句柄，uvicorn/logging 往
  stdout 写日志会直接崩掉（实测启动即退出）；
- 用 ``CREATE_NO_WINDOW`` 隐藏黑窗口 + stdout/stderr 重定向到日志文件。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .bilinote_client import BiliNoteClient
from .config import AgentConfig
from .logging_setup import get_logger

logger = get_logger("backend")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

Probe = Callable[[], Awaitable[bool]]
PopenFactory = Callable[..., Any]


class BackendError(RuntimeError):
    """BiliNote 后端没起来 / 起不来。message 是可以直接展示给用户的中文。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BackendSupervisor:
    """探测 + 按需拉起 BiliNote 后端。

    ``probe`` / ``popen`` / ``sleep`` / ``clock`` 可注入，便于测试不启动真实进程。
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        bilinote: BiliNoteClient | None = None,
        popen: Optional[PopenFactory] = None,
        probe: Optional[Probe] = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        platform: Optional[str] = None,
        probe_interval_seconds: float = 2.0,
    ):
        self.config = config
        self._bilinote = bilinote
        self._popen = popen or subprocess.Popen
        self._probe = probe or self._default_probe
        self._sleep = sleep
        self._clock = clock
        self._platform = platform or sys.platform
        self._probe_interval = probe_interval_seconds
        self._proc: Any = None
        self._log_fh: Any = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- 属性

    @property
    def is_windows(self) -> bool:
        return self._platform.startswith("win")

    @property
    def backend_exe(self) -> Path:
        return Path(self.config.backend_exe) if self.config.backend_exe else Path("")

    @property
    def workdir(self) -> Path:
        cfg_workdir = (self.config.backend_workdir or "").strip()
        if cfg_workdir and Path(cfg_workdir).is_dir():
            return Path(cfg_workdir)
        exe = self.backend_exe
        return exe.parent if exe.parent != Path("") else Path.cwd()

    @property
    def log_path(self) -> Path:
        return self.config.resolved_log_dir() / "bilinote_backend.log"

    @property
    def started_pid(self) -> Optional[int]:
        return getattr(self._proc, "pid", None) if self._proc is not None else None

    # ---------------------------------------------------------------- 探测

    async def _default_probe(self) -> bool:
        if self._bilinote is None:
            raise RuntimeError("BackendSupervisor 缺少 bilinote 客户端，无法探测后端")
        return await self._bilinote.is_alive()

    async def is_alive(self) -> bool:
        try:
            return bool(await self._probe())
        except Exception as exc:  # noqa: BLE001 - 探测失败一律视为没起来
            logger.debug("探测 BiliNote 后端失败：%s", exc)
            return False

    # ---------------------------------------------------------------- 主流程

    async def ensure_running(self) -> bool:
        """确保后端可用；可用返回 True，否则抛 :class:`BackendError`。"""
        if await self.is_alive():
            return True

        if not self.config.auto_start_backend:
            raise BackendError(
                "BiliNote 后端没有运行，且配置里关闭了自动启动（auto_start_backend=false）。"
                "请先手动打开 BiliNote（或设置 backend_exe 后开启自动启动）再试。"
            )

        async with self._lock:
            # 拿到锁后再确认一次，避免并发重复拉起
            if await self.is_alive():
                return True
            self._start_process()
            assert self._proc is not None
            await self._wait_until_ready()
            return True

    def _start_process(self) -> None:
        exe = self.backend_exe
        if not str(exe) or not exe.exists():
            raise BackendError(
                f"找不到 BiliNote 后端程序：{exe}\n"
                "请确认已安装 BiliNote，或在 ~/.linknote/agent.json 里把 "
                "backend_exe 改成 BiliNoteBackend.exe 的实际路径。"
            )

        if self._proc is not None and self._proc.poll() is None:
            return  # 已经有一个在跑

        workdir = self.workdir
        log_path = self.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._log_fh = open(log_path, "ab", buffering=0)  # noqa: SIM115 - 进程生命周期内持有
        except OSError as exc:
            raise BackendError(f"无法写入后端日志 {log_path}：{exc}") from exc

        kwargs: dict[str, Any] = {
            "cwd": str(workdir),
            "stdout": self._log_fh,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if self.is_windows:
            # CREATE_NO_WINDOW 隐藏窗口；CREATE_NEW_PROCESS_GROUP 方便独立收信号。
            # 注意：绝不能用 DETACHED_PROCESS（没有 std 句柄，uvicorn 写日志会崩）。
            kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

        logger.info("BiliNote 后端未运行，尝试启动：%s（工作目录 %s）", exe, workdir)
        try:
            self._proc = self._popen([str(exe)], **kwargs)
        except OSError as exc:
            self._close_log()
            raise BackendError(f"启动 BiliNote 后端失败：{exc}") from exc
        logger.info(
            "已拉起 BiliNote 后端 pid=%s，日志见 %s",
            getattr(self._proc, "pid", "?"),
            log_path,
        )

    async def _wait_until_ready(self) -> None:
        assert self._proc is not None
        timeout = max(1.0, float(self.config.backend_start_timeout or 90.0))
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            await self._sleep(self._probe_interval)
            returncode = self._proc.poll()
            if returncode is not None:
                raise BackendError(
                    f"BiliNote 后端启动后立刻退出了（exit={returncode}）。\n"
                    f"详见日志：{self.log_path}"
                )
            if await self.is_alive():
                logger.info("BiliNote 后端已就绪")
                return
        raise BackendError(
            f"等待 BiliNote 后端启动超时（{int(timeout)} 秒）。\n"
            f"请查看日志：{self.log_path}"
        )

    # ---------------------------------------------------------------- 清理

    def _close_log(self) -> None:
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None

    async def close(self) -> None:
        """关闭日志句柄。

        故意**不**杀掉已经拉起的 BiliNote 后端：它是用户自己的程序，
        保活对用户无害，下次 Agent 启动时复用即可。
        """
        self._close_log()


__all__ = ["BackendError", "BackendSupervisor"]

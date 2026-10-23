"""后端进程管理：探测、自动拉起、超时/立即退出、日志重定向参数。"""

from __future__ import annotations

import pytest

from linknote_agent.backend import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    BackendError,
    BackendSupervisor,
)
from linknote_agent.bilinote_client import BiliNoteClient


class FakeProc:
    def __init__(self, poll_result=None, pid=4242):
        self.pid = pid
        self._poll_result = poll_result
        self.killed = False

    def poll(self):
        return self._poll_result


async def _never_alive():
    return False


async def _always_alive():
    return True


def _bilinote(cfg, fake):
    return BiliNoteClient(cfg.backend_base_url, transport=fake.transport)


async def test_already_running_returns_true(make_config, fake_bilinote):
    cfg = make_config()
    supervisor = BackendSupervisor(cfg, bilinote=_bilinote(cfg, fake_bilinote))
    assert await supervisor.ensure_running() is True
    assert supervisor.started_pid is None


async def test_auto_start_disabled_raises(make_config, fake_bilinote):
    cfg = make_config(auto_start_backend=False)
    fake_bilinote.alive = False
    supervisor = BackendSupervisor(cfg, bilinote=_bilinote(cfg, fake_bilinote))
    with pytest.raises(BackendError) as excinfo:
        await supervisor.ensure_running()
    assert "auto_start_backend" in str(excinfo.value)


async def test_missing_exe_raises(make_config, tmp_path):
    cfg = make_config(auto_start_backend=True, backend_exe=str(tmp_path / "不存在.exe"))
    supervisor = BackendSupervisor(cfg, probe=_never_alive)
    with pytest.raises(BackendError) as excinfo:
        await supervisor.ensure_running()
    assert "找不到 BiliNote 后端程序" in str(excinfo.value)


async def test_launch_timeout_reports_log_path(make_config, tmp_path):
    exe = tmp_path / "BiliNoteBackend.exe"
    exe.write_bytes(b"fake")
    cfg = make_config(
        auto_start_backend=True,
        backend_exe=str(exe),
        backend_workdir=str(tmp_path),
        backend_start_timeout=0.05,
    )
    launched = {}

    def popen(args, **kwargs):
        launched["args"] = args
        launched["kwargs"] = kwargs
        return FakeProc(poll_result=None)

    supervisor = BackendSupervisor(
        cfg,
        popen=popen,
        probe=_never_alive,
        platform="win32",
        probe_interval_seconds=0.01,
    )
    with pytest.raises(BackendError) as excinfo:
        await supervisor.ensure_running()
    assert "超时" in str(excinfo.value)
    assert launched["args"] == [str(exe)]
    # Windows 下必须带 CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP，且 stdout 重定向到文件
    assert launched["kwargs"]["creationflags"] == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    assert launched["kwargs"]["cwd"] == str(tmp_path)
    log_path = cfg.resolved_log_dir() / "bilinote_backend.log"
    assert log_path.exists()
    await supervisor.close()


async def test_launch_immediate_exit_reports_log(make_config, tmp_path):
    exe = tmp_path / "BiliNoteBackend.exe"
    exe.write_bytes(b"fake")
    cfg = make_config(
        auto_start_backend=True,
        backend_exe=str(exe),
        backend_workdir=str(tmp_path),
        backend_start_timeout=1.0,
    )
    supervisor = BackendSupervisor(
        cfg,
        popen=lambda args, **kwargs: FakeProc(poll_result=1),
        probe=_never_alive,
        platform="win32",
        probe_interval_seconds=0.01,
    )
    with pytest.raises(BackendError) as excinfo:
        await supervisor.ensure_running()
    assert "立刻退出" in str(excinfo.value)
    assert "bilinote_backend.log" in str(excinfo.value)
    await supervisor.close()


async def test_wait_until_ready_after_launch(make_config, tmp_path):
    exe = tmp_path / "BiliNoteBackend.exe"
    exe.write_bytes(b"fake")
    cfg = make_config(
        auto_start_backend=True,
        backend_exe=str(exe),
        backend_workdir=str(tmp_path),
        backend_start_timeout=2.0,
    )
    state = {"calls": 0}

    async def probe():
        state["calls"] += 1
        return state["calls"] >= 3  # 探测两次后认为起来了

    supervisor = BackendSupervisor(
        cfg,
        popen=lambda args, **kwargs: FakeProc(poll_result=None),
        probe=probe,
        platform="win32",
        probe_interval_seconds=0.01,
    )
    assert await supervisor.ensure_running() is True
    assert supervisor.started_pid == 4242
    await supervisor.close()


async def test_non_windows_has_no_creationflags(make_config, tmp_path):
    exe = tmp_path / "BiliNoteBackend.exe"
    exe.write_bytes(b"fake")
    cfg = make_config(
        auto_start_backend=True,
        backend_exe=str(exe),
        backend_workdir=str(tmp_path),
        backend_start_timeout=0.05,
    )
    launched = {}

    def popen(args, **kwargs):
        launched.update(kwargs)
        return FakeProc(poll_result=None)

    supervisor = BackendSupervisor(
        cfg, popen=popen, probe=_never_alive, platform="linux", probe_interval_seconds=0.01
    )
    with pytest.raises(BackendError):
        await supervisor.ensure_running()
    assert "creationflags" not in launched
    await supervisor.close()


async def test_probe_error_counts_as_not_alive(make_config):
    async def broken_probe():
        raise RuntimeError("boom")

    cfg = make_config(auto_start_backend=False)
    supervisor = BackendSupervisor(cfg, probe=broken_probe)
    assert await supervisor.is_alive() is False
    with pytest.raises(BackendError):
        await supervisor.ensure_running()


async def test_close_is_idempotent(make_config):
    cfg = make_config(auto_start_backend=False)
    supervisor = BackendSupervisor(cfg, probe=_never_alive)
    await supervisor.close()
    await supervisor.close()

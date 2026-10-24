"""LinkNote Desktop 全局路径与配置。

配置存 `%LOCALAPPDATA%\\LinkNote\\settings.json`；Agent 配置存同目录 `agent.json`。
桌面端不保存 token 在 settings 里，token 由 Agent 配置持有。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

APP_NAME = "LinkNote"
APP_NAME_CN = "链记"
APP_VERSION = "0.3.0"
DEFAULT_RELAY_URL = "https://relay.xykong.xyz"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8483/api"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-flash"


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "LinkNote"
    return Path.home() / ".linknote-desktop"


def resource_dir() -> Path:
    """PyInstaller 打包后资源目录；开发时是 linknote_desktop/resources。"""
    if getattr(sys, "frozen", False):
        candidates = [
            Path(sys._MEIPASS) / "resources",  # type: ignore[attr-defined]
            Path(sys._MEIPASS) / "linknote_desktop" / "resources",  # type: ignore[attr-defined]
            Path(sys.executable).resolve().parent / "resources",
        ]
        for path in candidates:
            if path.is_dir():
                return path
        return candidates[0]
    return Path(__file__).resolve().parent / "resources"


def app_dir() -> Path:
    """安装目录（exe 所在目录）；开发时为仓库 desktop 目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def agent_config_path() -> Path:
    return user_data_dir() / "agent.json"


def log_dir() -> Path:
    return user_data_dir() / "logs"


def _engine_candidates() -> Iterable[Path]:
    # 1) 安装包内置引擎
    yield app_dir() / "engine" / "BiliNoteBackend.exe"
    # 2) 本仓库打包目录（开发）
    yield app_dir() / "build" / "engine" / "BiliNoteBackend.exe"
    # 3) 常见的 BiliNote 安装位置
    local = os.environ.get("LOCALAPPDATA")
    pf = os.environ.get("PROGRAMFILES")
    pf86 = os.environ.get("PROGRAMFILES(X86)")
    for base in (local, pf, pf86):
        if not base:
            continue
        yield Path(base) / "Programs" / "BiliNote" / "BiliNoteBackend.exe"
        yield Path(base) / "BiliNote" / "BiliNoteBackend.exe"
    yield Path("C:/app/bilinote/BiliNoteBackend.exe")
    # 4) 安装版卸载信息里可能记录的目录（简单扫描）
    for drive in ("C:/", "D:/"):
        p = Path(drive) / "app" / "bilinote" / "BiliNoteBackend.exe"
        yield p


def detect_engine_exe() -> str:
    for candidate in _engine_candidates():
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


@dataclass
class DesktopConfig:
    # 连接
    relay_base_url: str = DEFAULT_RELAY_URL
    agent_name: str = "我的电脑"
    # BiliNote 引擎
    engine_exe: str = ""
    engine_workdir: str = ""
    backend_base_url: str = DEFAULT_BACKEND_URL
    engine_auto_start: bool = True
    # ffmpeg（目录，里面要有 ffmpeg.exe；留空自动找）
    ffmpeg_bin_dir: str = ""
    # 生成参数
    provider_id: str = DEFAULT_PROVIDER
    model_name: str = DEFAULT_MODEL
    quality: str = "fast"
    style: str = "minimal"
    screenshot: bool = False
    insert_video_links: bool = True
    # 网络
    proxy_enabled: bool = False
    proxy_url: str = ""
    # 国内网络默认走 HF 镜像；用户可在设置里改回 https://huggingface.co
    hf_endpoint: str = "https://hf-mirror.com"
    # 启动/托盘
    autostart: bool = False
    start_minimized: bool = False
    close_to_tray: bool = True
    theme: str = "system"  # system / light / dark
    # 笔记自动归档（Obsidian / 本地目录）
    archive_enabled: bool = False
    archive_dir: str = ""          # 目标目录（Obsidian vault 或普通目录）
    archive_template: str = ""     # 文件名模板；留空 = 沿用 notes/ 里的文件名
    # 日志
    log_retention_days: int = 14
    # 首次引导
    onboarding_done: bool = False
    _extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def agent_config_path(self) -> Path:
        return agent_config_path()

    def resolved_engine_exe(self) -> str:
        if self.engine_exe:
            return self.engine_exe
        found = detect_engine_exe()
        if found:
            self.engine_exe = found
        return self.engine_exe

    def resolved_engine_workdir(self) -> str:
        if self.engine_workdir and Path(self.engine_workdir).is_dir():
            return self.engine_workdir
        exe = self.resolved_engine_exe()
        return str(Path(exe).parent) if exe else ""

    def to_agent_overrides(self) -> dict[str, Any]:
        return {
            "relay_base_url": self.relay_base_url.rstrip("/"),
            "agent_name": self.agent_name or "我的电脑",
            "backend_base_url": self.backend_base_url.rstrip("/"),
            "backend_exe": self.resolved_engine_exe(),
            "backend_workdir": self.resolved_engine_workdir(),
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "quality": self.quality,
            "style": self.style,
            "screenshot": bool(self.screenshot),
            "insert_video_links": bool(self.insert_video_links),
            "log_dir": str(log_dir()),
            "outbox_dir": str(user_data_dir() / "outbox"),
            "notes_dir": str(user_data_dir() / "notes"),
        }

    def save(self, path: Path | None = None) -> Path:
        target = path or settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        extra = data.pop("_extra", {})
        if isinstance(extra, dict):
            data.update({k: v for k, v in extra.items() if k not in data})
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "DesktopConfig":
        cfg = cls()
        target = path or settings_path()
        if target.exists():
            try:
                raw = json.loads(target.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, dict):
                known = {f.name for f in fields(cls)}
                extra: dict[str, Any] = {}
                for key, value in raw.items():
                    if key in known and key != "_extra":
                        current = getattr(cfg, key)
                        try:
                            if isinstance(current, bool):
                                value = bool(value)
                            elif isinstance(current, int):
                                value = int(value)
                        except (TypeError, ValueError):
                            continue
                        setattr(cfg, key, value)
                    else:
                        extra[key] = value
                cfg._extra = extra
        if not cfg.engine_exe:
            cfg.engine_exe = detect_engine_exe()
        if not cfg.engine_workdir and cfg.engine_exe:
            cfg.engine_workdir = str(Path(cfg.engine_exe).parent)
        return cfg

    def ensure_dirs(self) -> None:
        for p in (user_data_dir(), log_dir(), user_data_dir() / "outbox", user_data_dir() / "notes"):
            p.mkdir(parents=True, exist_ok=True)


def ensure_agent_config(cfg: DesktopConfig) -> dict[str, Any]:
    """把桌面设置同步到 linknote_agent 可读的 agent.json，保留已有 token/配对字段。"""
    target = agent_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.exists():
        try:
            value = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                existing = value
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(cfg.to_agent_overrides())
    existing.setdefault("platform", "windows")
    existing.setdefault("version", APP_VERSION)
    existing.setdefault("auto_start_backend", True)
    existing.setdefault("backend_start_timeout", 90.0)
    existing.setdefault("poll_interval_seconds", 4.0)
    existing.setdefault("task_status_poll_interval_seconds", 3.0)
    existing.setdefault("progress_min_interval_seconds", 8.0)
    existing.setdefault("heartbeat_interval_seconds", 30.0)
    existing.setdefault("lease_heartbeat_interval_seconds", 300.0)
    existing.setdefault("task_timeout_seconds", 3600.0)
    existing.setdefault("pair_poll_interval_seconds", 2.0)
    existing.setdefault("max_status_errors", 20)
    existing.setdefault("verbose", False)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return existing

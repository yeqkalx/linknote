"""ffmpeg 查找 / 下载 / 解压。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from ..config import DesktopConfig, app_dir, user_data_dir

FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


@dataclass
class FFmpegInfo:
    path: str = ""
    bin_dir: str = ""
    version: str = ""

    @property
    def available(self) -> bool:
        return bool(self.path and Path(self.path).is_file())


def _version_of(exe: Path) -> str:
    try:
        completed = subprocess.run(
            [str(exe), "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        first = (completed.stdout or completed.stderr or "").splitlines()
        return first[0].strip() if first else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _info_from_bin_dir(bin_dir: Path) -> FFmpegInfo:
    exe = bin_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if exe.is_file():
        return FFmpegInfo(path=str(exe), bin_dir=str(bin_dir), version=_version_of(exe))
    return FFmpegInfo()


def find_ffmpeg(config: DesktopConfig | None = None) -> FFmpegInfo:
    candidates: list[Path] = []
    if config and config.ffmpeg_bin_dir:
        candidates.append(Path(config.ffmpeg_bin_dir))
    env_dir = os.environ.get("FFMPEG_BIN_PATH", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which).resolve().parent)

    # 下载目录 / 内置目录
    candidates.append(user_data_dir() / "ffmpeg" / "bin")
    candidates.append(app_dir() / "ffmpeg" / "bin")
    candidates.append(app_dir() / "engine" / "ffmpeg" / "bin")
    # 本机常见的解压位置
    for pattern in ("C:/app/ffmpeg*/bin", "C:/ffmpeg*/bin", "D:/app/ffmpeg*/bin"):
        try:
            candidates.extend(Path(p) for p in Path(pattern).parent.glob(Path(pattern).name) if p.is_dir())
        except OSError:
            pass
    # glob 的父目录写法在上面不总是对，直接再补一层
    try:
        for base in (Path("C:/app"), Path("C:/"), Path("D:/app")):
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir() and "ffmpeg" in child.name.lower():
                        candidates.append(child / "bin")
    except OSError:
        pass

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        info = _info_from_bin_dir(candidate)
        if info.available:
            return info
    return FFmpegInfo()


ProgressCallback = Callable[[int, int], None]


def download_ffmpeg(
    config: DesktopConfig,
    progress: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
) -> FFmpegInfo:
    """下载官方 essentials 构建并解压到 `%LOCALAPPDATA%\\LinkNote\\ffmpeg`。"""
    target_root = user_data_dir() / "ffmpeg"
    target_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = user_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / "ffmpeg-release-essentials.zip"

    proxy = config.proxy_url.strip() if config.proxy_enabled and config.proxy_url.strip() else None
    client_kwargs: dict = {"follow_redirects": True, "timeout": httpx.Timeout(30.0, read=180.0)}
    if proxy:
        client_kwargs["proxy"] = proxy

    with httpx.Client(**client_kwargs) as client:
        with client.stream("GET", FFMPEG_DOWNLOAD_URL) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            downloaded = 0
            with zip_path.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    if stop_event is not None and stop_event.is_set():
                        raise RuntimeError("下载已取消")
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)

    # 解压
    extract_dir = tmp_dir / "ffmpeg-extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    found = None
    for exe in extract_dir.rglob("ffmpeg.exe"):
        found = exe
        break
    if found is None:
        raise RuntimeError("压缩包里没有找到 ffmpeg.exe")

    bin_dir = target_root / "bin"
    if bin_dir.exists():
        shutil.rmtree(bin_dir, ignore_errors=True)
    shutil.copytree(found.parent, bin_dir)
    info = _info_from_bin_dir(bin_dir)
    if not info.available:
        raise RuntimeError("ffmpeg 解压后校验失败")

    config.ffmpeg_bin_dir = str(bin_dir)
    config.save()
    try:
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)
    except OSError:
        pass
    return info

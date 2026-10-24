"""ffmpeg 查找 / 下载 / 解压。

默认下载 **BtbN FFmpeg-Builds** 的 Windows x64 **LGPL shared** 构建
（`ffmpeg-master-latest-win64-lgpl-shared.zip`），避免自动为用户安装 GPL 构建：

* LGPL shared：`bin\\ffmpeg.exe` + 同目录 `*.dll`（avcodec/avformat/... 动态链接），
  满足 LGPL 动态链接分发条件；许可全文随安装包放在 `licenses\\LGPL-3.0.txt`
  （以及 `GPL-3.0.txt`，因为 LGPL-3.0 以 GPL-3.0 为基础）。
* 仍支持手动指定本机 ffmpeg（设置页填写 bin 目录 / 环境变量 `FFMPEG_BIN_PATH`）。
* 如果用户自行下载 GPL 构建并手动指定，由用户自行负责该构建的 GPL 义务。

下载失败 / 压缩包结构不符时抛出带上下文的 `RuntimeError`，由 UI 层显示；
不会影响 onboarding 的“跳过”流程（调用方 `controller.download_ffmpeg_async`
只把异常转成通知）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from ..config import DesktopConfig, app_dir, user_data_dir

# BtbN FFmpeg-Builds: win64 LGPL shared build (bin\ffmpeg.exe + sibling DLLs).
# The "latest" tag is rebuilt on every upstream FFmpeg master push; this URL is
# verified by the packaging task (HTTP 200, ~82 MB zip, 7 DLLs in bin\).
# Release builds may pin a specific BtbN asset via LINKNOTE_FFMPEG_URL for
# reproducibility (e.g. a dated autobuild tag) without code changes.
_DEFAULT_FFMPEG_DOWNLOAD_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-lgpl-shared.zip"
)
FFMPEG_DOWNLOAD_URL = (
    os.environ.get("LINKNOTE_FFMPEG_URL", "").strip() or _DEFAULT_FFMPEG_DOWNLOAD_URL
)
FFMPEG_BUILD_LABEL = "BtbN FFmpeg-Builds win64 LGPL shared (master-latest)"
FFMPEG_BUILD_LICENSE = "LGPL (FFmpeg LGPL shared build; see bundled LICENSE.txt)"
FFMPEG_ZIP_NAME = "ffmpeg-master-latest-win64-lgpl-shared.zip"


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


def _find_ffmpeg_bin_dir(extract_dir: Path) -> tuple[Optional[Path], bool]:
    """在解压目录里定位 ffmpeg.exe 所在目录。

    兼容两种常见结构：
      * BtbN shared:  ``<root>/bin/ffmpeg.exe`` + 同目录 ``*.dll``
      * 静态构建:     ``<root>/bin/ffmpeg.exe``（没有 DLL）
    返回 ``(bin_dir, has_sibling_dlls)``；找不到 exe 时返回 ``(None, False)``。
    优先选择“同目录有 DLL”的那一份（shared 构建），再退回第一个静态 exe。
    """
    exes = sorted(extract_dir.rglob("ffmpeg.exe"), key=lambda p: (len(p.parts), str(p).lower()))
    for exe in exes:
        parent = exe.parent
        try:
            if any(parent.glob("*.dll")):
                return parent, True
        except OSError:
            continue
    if exes:
        return exes[0].parent, False
    return None, False


def _zip_layout_hint(extract_dir: Path) -> str:
    try:
        names = sorted({entry.name for entry in extract_dir.iterdir()})
    except OSError:
        return "（无法读取解压目录）"
    return ", ".join(names[:8]) or "（空）"


def download_ffmpeg(
    config: DesktopConfig,
    progress: Optional[ProgressCallback] = None,
    stop_event: Optional[threading.Event] = None,
) -> FFmpegInfo:
    """下载 BtbN LGPL shared 构建并解压到 `%LOCALAPPDATA%\\LinkNote\\ffmpeg`。

    下载/解压/校验失败都会抛出带有 URL 与原因说明的 ``RuntimeError``，UI 会把
    错误显示给用户；不会中断其它流程（用户仍可手动指定 ffmpeg 或跳过）。
    """
    target_root = user_data_dir() / "ffmpeg"
    target_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = user_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / FFMPEG_ZIP_NAME
    extract_dir = tmp_dir / "ffmpeg-extract"

    proxy = config.proxy_url.strip() if config.proxy_enabled and config.proxy_url.strip() else None
    client_kwargs: dict = {"follow_redirects": True, "timeout": httpx.Timeout(30.0, read=180.0)}
    if proxy:
        client_kwargs["proxy"] = proxy

    try:
        # ---------------------------------------------------------------- 下载
        try:
            with httpx.Client(**client_kwargs) as client:
                with client.stream("GET", FFMPEG_DOWNLOAD_URL) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"下载 FFmpeg 失败：HTTP {response.status_code}（{FFMPEG_DOWNLOAD_URL}）"
                        )
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
        except RuntimeError:
            raise
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"下载 FFmpeg 失败（{FFMPEG_BUILD_LABEL}）：{exc}\n"
                f"下载地址：{FFMPEG_DOWNLOAD_URL}\n"
                f"请检查网络/代理，或在设置中手动指定本机 ffmpeg。"
            ) from exc

        if not zip_path.is_file() or zip_path.stat().st_size < 1024 * 1024:
            raise RuntimeError(
                f"下载 FFmpeg 失败：文件不完整（{zip_path}）。请重试或手动指定本机 ffmpeg。"
            )

        # ---------------------------------------------------------------- 解压
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise RuntimeError(
                f"FFmpeg 压缩包损坏（{FFMPEG_ZIP_NAME}）。请重试下载，或手动指定本机 ffmpeg。"
            ) from exc

        # ------------------------------------------------------- 定位 bin 目录
        bin_dir, has_dlls = _find_ffmpeg_bin_dir(extract_dir)
        if bin_dir is None:
            raise RuntimeError(
                f"FFmpeg 压缩包结构不符合预期：未找到 ffmpeg.exe（{FFMPEG_ZIP_NAME}，"
                f"解压根目录：{_zip_layout_hint(extract_dir)}）。请手动指定本机 ffmpeg。"
            )
        if not has_dlls:
            raise RuntimeError(
                "下载的 FFmpeg 构建是静态链接版本，缺少同目录 DLL（预期为 LGPL shared 构建）。"
                f"请重试下载 {FFMPEG_ZIP_NAME}，或手动指定本机 ffmpeg。"
            )

        # 目标 bin 目录：整体复制 bin（ffmpeg.exe + DLL + ffprobe.exe）
        target_bin = target_root / "bin"
        if target_bin.exists():
            shutil.rmtree(target_bin, ignore_errors=True)
        shutil.copytree(bin_dir, target_bin)

        # 保留构建自带的 LICENSE（BtbN/FFmpeg 许可说明），便于分发追溯
        try:
            for lic in extract_dir.rglob("LICENSE.txt"):
                if lic.is_file():
                    shutil.copy2(lic, target_root / "FFMPEG-LICENSE.txt")
                    break
        except OSError:
            pass

        info = _info_from_bin_dir(target_bin)
        if not info.available:
            raise RuntimeError(
                "FFmpeg 解压后无法找到 ffmpeg.exe，请手动指定本机 ffmpeg。"
            )
        if not info.version:
            raise RuntimeError(
                "FFmpeg 已解压但无法运行（可能缺少同目录 DLL）。"
                "请重试下载，或在设置中手动指定本机 ffmpeg。"
            )

        config.ffmpeg_bin_dir = str(target_bin)
        config.save()
        return info
    finally:
        # 清理临时文件（成功/失败都清理，避免占用用户磁盘）
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(extract_dir, ignore_errors=True)




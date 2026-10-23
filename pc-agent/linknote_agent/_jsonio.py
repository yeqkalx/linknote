"""本机 JSON 小文件读写工具。

集中处理：
- 原子写（临时文件 + os.replace），避免半个 JSON 把配置/outbox 写坏；
- UTF-8 无 BOM；
- POSIX 上尽量把权限收紧到 0600（token 明文只在本机）；
- Windows 上 ``os.chmod`` 语义有限，这里只做 best-effort，不因失败而中断。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def ensure_dir(path: Path | str) -> Path:
    """创建目录（含父目录）并返回 Path。"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path: Path | str, *, default: Any = None) -> Any:
    """读取 JSON 文件；文件不存在时返回 ``default``。

    utf-8-sig：兼容用户用记事本 / PowerShell 编辑过、带 BOM 的文件。
    """
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8-sig"))


def atomic_write_text(path: Path | str, text: str, *, private: bool = False) -> Path:
    """原子写文本：先写同目录临时文件，fsync 后 os.replace 覆盖目标。"""
    target = Path(path)
    ensure_dir(target.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if private:
            _try_chmod_private(tmp)
        os.replace(tmp, target)
        if private:
            _try_chmod_private(target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def atomic_write_json(path: Path | str, data: Any, *, private: bool = False) -> Path:
    """原子写 JSON（缩进 2、ensure_ascii=False，方便用户直接用记事本查看/改）。"""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_text(path, text, private=private)


def _try_chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows 上 chmod 支持有限（多数情况只是设置只读位），失败不影响功能。
        pass

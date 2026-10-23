"""日志：控制台（有终端时）+ 滚动文件。

注意：开机自启用的是 ``pythonw.exe``，它没有 stdout/stderr。
如果无条件挂 StreamHandler，每条日志都会在内部抛 AttributeError（虽然被吞掉），
所以只在真的有控制台时才加 StreamHandler。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_LOGGER_NAME = "linknote_agent"
_HANDLER_MARK = "_linknote_handler"


def setup_logging(
    *,
    verbose: bool = False,
    log_dir: Path | str | None = None,
    filename: str = "agent.log",
    console: bool = True,
) -> Path | None:
    """初始化根 logger，返回日志文件路径（log_dir 为 None 时只输出控制台）。"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 幂等：重复调用（例如测试里）不重复挂 handler。
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()

    console_ok = console and sys.stderr is not None
    if console_ok:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.setLevel(level)
        setattr(stream, _HANDLER_MARK, True)
        root.addHandler(stream)

    log_path: Path | None = None
    if log_dir is not None:
        log_path = Path(log_dir) / filename
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        setattr(file_handler, _HANDLER_MARK, True)
        root.addHandler(file_handler)

    # httpx 每个请求打一条 INFO，太吵；只在 verbose 时放出来。
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.DEBUG if verbose else logging.WARNING)

    return log_path


def get_logger(name: str | None = None) -> logging.Logger:
    """包内统一 logger（``linknote_agent.*``）。"""
    if not name or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    if name.startswith(_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")

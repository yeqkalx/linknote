"""``python -m linknote_desktop`` / PyInstaller 入口。

- `LinkNote.exe` 正常启动桌面窗口 + 托盘。
- `LinkNote.exe --agent-worker --config ... run` 作为 Agent 子进程使用
  （由桌面应用拉起，避免把 asyncio 轮询塞进 GUI 线程）。
"""
from __future__ import annotations

import sys


def main() -> int:
    argv = list(sys.argv[1:])
    if "--agent-worker" in argv:
        from linknote_agent.cli import main as agent_main

        argv.remove("--agent-worker")
        return int(agent_main(argv))

    from .application import run_app

    return run_app([sys.argv[0], *argv])


if __name__ == "__main__":
    raise SystemExit(main())

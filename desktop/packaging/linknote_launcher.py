"""PyInstaller runtime entry point for LinkNote Desktop.

Why a wrapper instead of using ``linknote_desktop/__main__.py`` directly as the
PyInstaller entry script: PyInstaller executes the entry script as the top-level
``__main__`` module, so relative imports inside it (``from .application import
run_app``) fail with "attempted relative import with no known parent package".
Importing the package module here gives ``linknote_desktop.__main__`` a proper
package context, and keeps ``python -m linknote_desktop`` working unchanged.

Frozen behaviour:
  * ``LinkNote.exe``                     -> GUI (window + tray)
  * ``LinkNote.exe --agent-worker ...``  -> Agent worker process
    (``linknote_desktop.__main__.main`` dispatches to ``linknote_agent.cli``)
"""

from __future__ import annotations

import sys

from linknote_desktop.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

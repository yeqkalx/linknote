"""PyInstaller probe: a minimal PySide6 window used to validate the LinkNote
packaging toolchain before the real app entry point is ready.

What it proves:
  * PySide6 6.12 + PyInstaller 6.22 can build a --windowed / --onedir exe
  * the exe starts with no console window (PE subsystem = GUI)
  * bundled ``resources/icon.ico`` is found through sys._MEIPASS and shown
  * the Qt runtime (platform plugin) loads on this machine

Usage:
  LinkNoteProbe.exe [--autoclose SECONDS] [--marker PATH] [--screenshot PATH]

The probe always writes a JSON marker file so an automated build script can
assert that the GUI actually booted (windowed exes have no stdout). With
``--screenshot`` it also grabs its own rendered window (QWidget.grab), which is
deterministic evidence that the Qt widgets painted, independent of what else is
on the desktop.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer, qVersion
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def resource_path(relative: str) -> Path:
    """Resolve a bundled resource both in source checkout and inside PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def write_marker(payload: dict) -> str:
    marker_dir = Path(tempfile.gettempdir()) / "linknote-probe"
    marker_dir.mkdir(parents=True, exist_ok=True)
    path = marker_dir / f"probe-{os.getpid()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def main() -> int:
    argv = sys.argv[1:]
    autoclose = 0
    marker_override = ""
    screenshot_path = ""
    for i, arg in enumerate(argv):
        if arg == "--autoclose" and i + 1 < len(argv):
            autoclose = int(argv[i + 1])
        elif arg == "--marker" and i + 1 < len(argv):
            marker_override = argv[i + 1]
        elif arg == "--screenshot" and i + 1 < len(argv):
            screenshot_path = argv[i + 1]

    app = QApplication(sys.argv)
    app.setApplicationName("LinkNoteProbe")

    icon_path = resource_path("resources/icon.ico")
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    app.setWindowIcon(icon)

    window = QWidget()
    window.setWindowTitle("LinkNote PyInstaller Probe")
    window.setWindowIcon(icon)
    window.resize(560, 320)
    layout = QVBoxLayout(window)
    title = QLabel("LinkNote 打包探针")
    title.setStyleSheet("font-size: 24px; font-weight: 600;")
    layout.addWidget(title)
    layout.addWidget(QLabel(f"PySide6 / Qt {qVersion()}"))
    layout.addWidget(QLabel(f"frozen={getattr(sys, 'frozen', False)}  _MEIPASS={getattr(sys, '_MEIPASS', '-')}"))
    layout.addWidget(QLabel(f"icon={icon_path} loaded={not icon.isNull()}"))
    layout.addStretch(1)
    window.show()
    window.raise_()
    window.activateWindow()

    marker = write_marker(
        {
            "pid": os.getpid(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "meipass": getattr(sys, "_MEIPASS", None),
            "qt_version": qVersion(),
            "icon_path": str(icon_path),
            "icon_loaded": not icon.isNull(),
            "visible": bool(window.isVisible()),
            "size": [window.width(), window.height()],
        }
    )
    if marker_override:
        try:
            Path(marker_override).write_text(Path(marker).read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass

    if screenshot_path:
        def _grab() -> None:
            pixmap = window.grab()
            Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
            pixmap.save(screenshot_path, "PNG")

        QTimer.singleShot(700, _grab)

    if autoclose > 0:
        QTimer.singleShot(autoclose * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

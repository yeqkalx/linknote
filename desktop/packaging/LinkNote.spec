# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LinkNote Desktop (onedir, windowed).

Build with desktop\\packaging\\build-desktop.ps1, which is the supported entry
point. Manual equivalent:

  desktop\\.venv\\Scripts\\pyinstaller.exe --noconfirm --clean ^
      --distpath dist-desktop --workpath desktop\\build\\app\\work ^
      desktop\\packaging\\LinkNote.spec

Layout produced:  dist-desktop\\LinkNote\\LinkNote.exe + _internal\\

Notes
-----
* ``desktop\\linknote_desktop`` and ``pc-agent`` are *editable* installs of the
  venv, but only ``linknote_agent``'s editable finder actually carries a path
  mapping here, so both source roots are added to ``pathex`` explicitly.
* ``linknote_agent`` is imported lazily (only in ``--agent-worker`` mode), hence
  ``collect_submodules`` so the frozen exe can act as the Agent worker process.
* Resources are bundled to ``linknote_desktop/resources`` because
  ``config.resource_dir()`` resolves ``sys._MEIPASS / "linknote_desktop" / "resources"``.
* The entry script is ``packaging/linknote_launcher.py``, not
  ``linknote_desktop/__main__.py``: PyInstaller runs the entry as top-level
  ``__main__`` where ``from .application import ...`` (a relative import) fails.
  The launcher imports ``linknote_desktop.__main__`` as a package module instead.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

DESKTOP_DIR = Path(SPECPATH).resolve().parent          # ...\bilinote-to-android\desktop
ROOT_DIR = DESKTOP_DIR.parent                          # ...\bilinote-to-android
AGENT_SRC = ROOT_DIR / "pc-agent"                      # linknote_agent package root
RESOURCES = DESKTOP_DIR / "linknote_desktop" / "resources"
ENTRY = DESKTOP_DIR / "packaging" / "linknote_launcher.py"
APP_MAIN = DESKTOP_DIR / "linknote_desktop" / "__main__.py"
# build-desktop.ps1 generates a Windows version resource for the current $Version
# (read from desktop\pyproject.toml) and passes it via LINKNOTE_VERSION_FILE.
VERSION_FILE = Path(
    os.environ.get("LINKNOTE_VERSION_FILE")
    or (DESKTOP_DIR / "packaging" / "version_info.txt")
)

for extra_path in (str(DESKTOP_DIR), str(AGENT_SRC)):
    if extra_path not in sys.path:
        sys.path.insert(0, extra_path)

if not ENTRY.is_file():
    raise SystemExit(f"LinkNote launcher missing: {ENTRY}")
if not APP_MAIN.is_file():
    raise SystemExit(f"LinkNote entry point missing: {APP_MAIN}")
if not RESOURCES.is_dir():
    raise SystemExit(f"LinkNote resources missing: {RESOURCES}")

hiddenimports = []
hiddenimports += collect_submodules("linknote_agent")
# linknote_agent lazily imports websockets in its WS client; make sure it is frozen.
hiddenimports += collect_submodules("websockets")
# qrcode imports its image backends lazily; the UI draws pairing QR codes.
hiddenimports += [
    "qrcode",
    "qrcode.image.pil",
    "qrcode.image.base",
    "qrcode.image.svg",
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
]

datas = [
    (str(RESOURCES), "linknote_desktop/resources"),
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(DESKTOP_DIR), str(AGENT_SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinkNote",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(RESOURCES / "icon.ico"),
    version=str(VERSION_FILE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LinkNote",
)


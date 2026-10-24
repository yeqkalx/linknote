"""Inspect the built LinkNote.exe (PyInstaller onedir PYZ) for packaging regressions.

Checks inside the *built* executable (not just the source tree):
  * linknote_desktop.services.ffmpeg contains the BtbN FFmpeg LGPL shared URL
    and no longer references gyan.dev (task-17 regression)
  * linknote_agent.relay_ws and the websockets package are frozen
    (task-12 regression: 0.2.1 shipped without websockets)
  * linknote_agent.bots.* (base/telegram/qq/runner/state/device_relay) plus the
    httpx/httpcore runtime deps used by the bots are frozen
    (task-23: bot entry must not raise ImportError in the installed app)

Usage:
  desktop\\.venv\\Scripts\\python.exe desktop\\packaging\\check-frozen-package.py ^
      dist-desktop\\LinkNote\\LinkNote.exe [-o report.json]

Exit code 0 = all checks pass; 1 = regression; 2 = could not inspect.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import CodeType
from typing import Any

# LinkNote 0.4.0 bot entry modules; every one must exist in the frozen PYZ.
BOTS_REQUIRED = [
    "linknote_agent.bots",
    "linknote_agent.bots.base",
    "linknote_agent.bots.telegram",
    "linknote_agent.bots.qq",
    "linknote_agent.bots.runner",
    "linknote_agent.bots.state",
    "linknote_agent.bots.device_relay",
]


def _walk_strings(code: CodeType):
    for const in code.co_consts:
        if isinstance(const, str):
            yield const
        elif isinstance(const, CodeType):
            yield from _walk_strings(const)


def _find_pyz_entry(arc: Any) -> str | None:
    for name in arc.toc:
        if str(name).lower().endswith(".pyz"):
            return str(name)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", help="path to the built LinkNote.exe")
    parser.add_argument("-o", "--out", default="", help="optional JSON report path")
    args = parser.parse_args()
    exe = Path(args.exe).resolve()
    if not exe.is_file():
        print(json.dumps({"ok": False, "error": f"exe not found: {exe}"}))
        return 2

    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"PyInstaller readers unavailable: {exc}"}))
        return 2

    report: dict[str, Any] = {"exe": str(exe), "ok": False}
    try:
        arc = CArchiveReader(str(exe))
        pyz_name = _find_pyz_entry(arc)
        report["pyz_entry"] = pyz_name
        if not pyz_name:
            report["error"] = "no PYZ archive found in the executable"
            print(json.dumps(report, ensure_ascii=False))
            return 2
        pyz = arc.open_embedded_archive(pyz_name)
        names = list(pyz.toc.keys())

        # ---------------------------------------------------------- ffmpeg URL
        ffmpeg_module = "linknote_desktop.services.ffmpeg"
        report["ffmpeg_module_found"] = ffmpeg_module in names
        btbn: list[str] = []
        gyan: list[str] = []
        if report["ffmpeg_module_found"]:
            code = pyz.extract(ffmpeg_module)
            for text in _walk_strings(code):
                low = text.lower()
                if "btbn" in low or "lgpl-shared" in low:
                    btbn.append(text)
                if "gyan.dev" in low or "ffmpeg-release-essentials" in low:
                    gyan.append(text)
        report["btbn_strings"] = sorted(set(btbn))[:10]
        report["gyan_strings"] = sorted(set(gyan))[:10]
        report["ffmpeg_btbn_ok"] = bool(btbn) and not gyan

        # ------------------------------------------------- websockets bundling
        ws_modules = sorted(n for n in names if str(n) == "websockets" or str(n).startswith("websockets."))
        report["websockets_module_count"] = len(ws_modules)
        report["websockets_modules_sample"] = ws_modules[:10]
        report["relay_ws_found"] = "linknote_agent.relay_ws" in names
        report["linknote_agent_found"] = any(str(n).startswith("linknote_agent") for n in names)
        report["websockets_ok"] = len(ws_modules) > 0 and report["relay_ws_found"]

        # ----------------------------------------------------- bots modules (0.4)
        bots_found = [m for m in BOTS_REQUIRED if m in names]
        bots_missing = [m for m in BOTS_REQUIRED if m not in names]
        report["bots_required"] = BOTS_REQUIRED
        report["bots_found"] = bots_found
        report["bots_missing"] = bots_missing
        report["bots_ok"] = not bots_missing
        bots_all = sorted(str(n) for n in names if str(n).startswith("linknote_agent.bots"))
        report["bots_modules_in_pyz"] = bots_all
        report["bots_module_count"] = len(bots_all)
        bot_extract_errors: list[str] = []
        for required in BOTS_REQUIRED:
            if required in names:
                try:
                    pyz.extract(required)  # raises if the code object is corrupt
                except Exception as exc:  # noqa: BLE001
                    bot_extract_errors.append(f"{required}: {type(exc).__name__}: {exc}")
        report["bots_extract_ok"] = not bot_extract_errors
        report["bots_extract_errors"] = bot_extract_errors

        # ------------------------------------- runtime deps used by the bots
        httpx_modules = [n for n in names if str(n) == "httpx" or str(n).startswith("httpx.")]
        report["httpx_module_count"] = len(httpx_modules)
        report["httpx_ok"] = len(httpx_modules) > 0
        httpcore_modules = [n for n in names if str(n) == "httpcore" or str(n).startswith("httpcore.")]
        report["httpcore_module_count"] = len(httpcore_modules)
        report["httpcore_ok"] = len(httpcore_modules) > 0

        report["module_count"] = len(names)
        report["ok"] = bool(
            report["ffmpeg_btbn_ok"]
            and report["websockets_ok"]
            and report["bots_ok"]
            and report["bots_extract_ok"]
            and report["httpx_ok"]
            and report["httpcore_ok"]
        )
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False))
        return 2

    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""从品牌参考图生成 LinkNote 全部图标（桌面 / PWA / Android）。

源文件（已裁好）：
  assets/brand/app-icon-light.png  (256×256, 白底圆角)
  assets/brand/app-icon-dark.png   (256×256, 深蓝底)
  assets/brand/mark.png            (230×230, 独立标志)

运行：uv run --with pillow --python 3.11 python scripts/gen_brand_assets.py
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "assets" / "brand"
LIGHT = Image.open(BRAND / "app-icon-light.png").convert("RGBA")
DARK = Image.open(BRAND / "app-icon-dark.png").convert("RGBA")
MARK = Image.open(BRAND / "mark.png").convert("RGBA")

BG_LIGHT = (253, 254, 255, 255)
BG_DARK = (15, 27, 48, 255)


def resized(source: Image.Image, size: int) -> Image.Image:
    return source.resize((size, size), Image.Resampling.LANCZOS)


def icon(size: int, source: Image.Image = LIGHT) -> Image.Image:
    return resized(source, size)


def maskable(size: int, source: Image.Image = LIGHT, padded: float = 0.78) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), BG_LIGHT if source is LIGHT else BG_DARK)
    inner = round(size * padded)
    art = resized(source, inner)
    canvas.alpha_composite(art, ((size - inner) // 2, (size - inner) // 2))
    return canvas


def save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def main() -> None:
    # 桌面 exe / 安装包 / 托盘
    desktop_res = ROOT / "desktop" / "linknote_desktop" / "resources"
    desktop_res.mkdir(parents=True, exist_ok=True)
    save_png(icon(512), desktop_res / "icon.png")
    ico = icon(256)
    ico.save(
        desktop_res / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    # PWA
    web_icons = ROOT / "app" / "www" / "icons"
    save_png(icon(192), web_icons / "icon-192.png")
    save_png(icon(512), web_icons / "icon-512.png")
    save_png(maskable(192), web_icons / "icon-maskable-192.png")
    save_png(maskable(512), web_icons / "icon-maskable-512.png")
    save_png(icon(180), web_icons / "apple-touch-icon.png")
    save_png(icon(32), web_icons / "favicon-32.png")
    save_png(icon(16), web_icons / "favicon-16.png")

    fav = icon(64)
    buffer = io.BytesIO()
    fav.save(buffer, format="PNG", optimize=True)
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    (web_icons / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
        f'<image width="64" height="64" href="data:image/png;base64,{b64}"/></svg>',
        encoding="utf-8",
    )

    # Android legacy + adaptive foreground
    for density, legend_size, fg_size in (
        ("mdpi", 48, 108),
        ("hdpi", 72, 162),
        ("xhdpi", 96, 216),
        ("xxhdpi", 144, 324),
        ("xxxhdpi", 192, 432),
    ):
        legacy_dir = ROOT / "assets" / "android-icons" / "legacy" / density
        fg_dir = ROOT / "assets" / "android-icons" / "foreground" / density
        legacy_dir.mkdir(parents=True, exist_ok=True)
        fg_dir.mkdir(parents=True, exist_ok=True)
        legacy = icon(legend_size)
        save_png(legacy, legacy_dir / "ic_launcher.png")
        save_png(legacy, legacy_dir / "ic_launcher_round.png")

        # 自适应前景：透明画布 + 居中标志；白色背景由 adaptive background 提供
        canvas = Image.new("RGBA", (fg_size, fg_size), (0, 0, 0, 0))
        inner = round(fg_size * 0.60)
        art = resized(MARK, inner)
        canvas.alpha_composite(art, ((fg_size - inner) // 2, (fg_size - inner) // 2))
        save_png(canvas, fg_dir / "ic_launcher_foreground.png")

    values_dir = ROOT / "assets" / "android-icons" / "values"
    values_dir.mkdir(parents=True, exist_ok=True)
    (values_dir / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        '    <color name="ic_launcher_background">#FDFEFF</color>\n'
        "</resources>\n",
        encoding="utf-8",
    )

    # 文档/官网用大图
    save_png(icon(1024), BRAND / "icon-1024.png")
    save_png(maskable(512, padded=0.82), BRAND / "icon-maskable-512.png")
    print("brand assets written")


if __name__ == "__main__":
    main()

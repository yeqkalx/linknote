#!/usr/bin/env python3
"""生成 LinkNote 的 PWA / Android 图标（无需外部设计工具）。

运行：uv run --with pillow --python 3.11 python scripts/gen_icons.py
输出：app/www/icons/*.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "www" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

INK = (28, 26, 24, 255)        # #1C1A18
PAPER = (245, 240, 232, 255)  # #F5F0E8
CORAL = (232, 93, 78, 255)    # #E85D4E
CHAR = (61, 61, 61, 255)      # #3D3D3D


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def draw_icon(size: int, maskable: bool = False) -> Image.Image:
    scale = size / 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def s(v: float) -> int:
        return round(v * scale)

    if maskable:
        # 全出血背景，内容收在 80% 安全区内
        draw.rectangle([0, 0, size, size], fill=INK)
        inset = 102
        card = [s(inset), s(inset), s(512 - inset), s(512 - inset)]
        rounded(draw, card, s(64), PAPER)
        # 缩小 logo 到安全区
        cx, cy = 256, 232
        r = 70
        draw.ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], fill=CORAL)
        draw.polygon(
            [(s(cx - 20), s(cy - 34)), (s(cx + 40), s(cy)), (s(cx - 20), s(cy + 34))],
            fill=PAPER,
        )
        for i, (y, w, color) in enumerate(
            [(302, 180, CHAR), (334, 140, CHAR), (366, 96, CORAL)]
        ):
            rounded(draw, [s(cx - 90), s(y), s(cx - 90 + w), s(y + 14)], s(7), color)
    else:
        rounded(draw, [0, 0, size, size], s(112), INK)
        rounded(draw, [s(72), s(72), s(440), s(440)], s(64), PAPER)

        cx, cy, r = 256, 206, 84
        draw.ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], fill=CORAL)
        draw.polygon(
            [(s(cx - 26), s(cy - 42)), (s(cx + 44), s(cy)), (s(cx - 26), s(cy + 42))],
            fill=PAPER,
        )

        bars = [
            (330, 240, CHAR),
            (370, 180, CHAR),
            (410, 120, CORAL),
        ]
        for y, w, color in bars:
            rounded(draw, [s(136), s(y), s(136 + w), s(y + 20)], s(10), color)

    return image


def main() -> None:
    variants = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-192.png", 192, True),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, True),
        ("favicon-32.png", 32, False),
        ("favicon-16.png", 16, False),
    ]
    for name, size, maskable in variants:
        image = draw_icon(size, maskable=maskable)
        image.save(OUT / name, format="PNG", optimize=True)
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""生成 Android launcher 图标资源（legacy + adaptive foreground）。

输出到 assets/android-icons/，由 packaging/android 构建脚本复制到 app/android res。
运行：uv run --with pillow --python 3.11 python scripts/gen_android_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "android-icons"
OUT.mkdir(parents=True, exist_ok=True)

INK = (28, 26, 24, 255)
PAPER = (245, 240, 232, 255)
CORAL = (232, 93, 78, 255)
CHAR = (61, 61, 61, 255)

DENSITIES = {
    "mdpi": (48, 108),
    "hdpi": (72, 162),
    "xhdpi": (96, 216),
    "xxhdpi": (144, 324),
    "xxxhdpi": (192, 432),
}


def legacy_icon(size: int) -> Image.Image:
    scale = size / 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def s(v: float) -> int:
        return round(v * scale)

    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=s(112), fill=INK)
    draw.rounded_rectangle([s(72), s(72), s(440), s(440)], radius=s(64), fill=PAPER)
    draw.ellipse([s(172), s(122), s(340), s(290)], fill=CORAL)
    draw.polygon([(s(230), s(164)), (s(300), s(206)), (s(230), s(248))], fill=PAPER)
    for y, w, color in [(330, 240, CHAR), (370, 180, CHAR), (410, 120, CORAL)]:
        draw.rounded_rectangle([s(136), s(y), s(136 + w), s(y + 20)], radius=s(10), fill=color)
    return image


def adaptive_foreground(size: int) -> Image.Image:
    """108dp 画布，透明底，内容收在中央安全区。"""
    scale = size / 108
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def s(v: float) -> int:
        return round(v * scale)

    # 纸卡：中央 66dp，圆角 12
    draw.rounded_rectangle([s(21), s(21), s(87), s(87)], radius=s(12), fill=PAPER)
    # 珊瑚播放键
    draw.ellipse([s(39), s(33), s(69), s(63)], fill=CORAL)
    draw.polygon([(s(48), s(41)), (s(62), s(48)), (s(48), s(55))], fill=PAPER)
    # 三条笔记横线
    for y, w, color in [(66, 26, CHAR), (72, 20, CHAR), (78, 14, CORAL)]:
        draw.rounded_rectangle([s(38), s(y), s(38 + w), s(y + 3.6)], radius=s(1.8), fill=color)
    return image


def main() -> None:
    for density, (legacy_size, fg_size) in DENSITIES.items():
        legacy_dir = OUT / "legacy" / density
        fg_dir = OUT / "foreground" / density
        legacy_dir.mkdir(parents=True, exist_ok=True)
        fg_dir.mkdir(parents=True, exist_ok=True)

        icon = legacy_icon(legacy_size)
        icon.save(legacy_dir / "ic_launcher.png", format="PNG", optimize=True)
        icon.save(legacy_dir / "ic_launcher_round.png", format="PNG", optimize=True)
        adaptive_foreground(fg_size).save(
            fg_dir / "ic_launcher_foreground.png", format="PNG", optimize=True
        )
        print(f"{density}: legacy {legacy_size}px, foreground {fg_size}px")

    values_dir = OUT / "values"
    values_dir.mkdir(parents=True, exist_ok=True)
    (values_dir / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        '    <color name="ic_launcher_background">#1C1A18</color>\n'
        "</resources>\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

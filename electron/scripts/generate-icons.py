#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_PACKAGES = ROOT / ".python-packages"
if str(PYTHON_PACKAGES) not in sys.path:
    sys.path.insert(0, str(PYTHON_PACKAGES))

from PIL import Image


SOURCE_LOGO = ROOT / "Truncated_Njord_logo.jpg"
BUILD_RESOURCES = ROOT / "electron" / "buildResources"
MASTER_PNG = BUILD_RESOURCES / "NjordHR.icon.png"
MAC_ICON = BUILD_RESOURCES / "NjordHR.icns"
WIN_ICON = BUILD_RESOURCES / "NjordHR.ico"

CANVAS_SIZE = 1024
PADDING = 96
BACKGROUND = "#F7F8FB"


def build_master_image() -> Image.Image:
    source = Image.open(SOURCE_LOGO).convert("RGBA")
    width, height = source.size

    # The source asset is a wide wordmark. Crop the left square portion so the
    # icon stays legible at small sizes instead of shrinking the full banner.
    crop_size = min(width, height)
    crop_box = (0, 0, crop_size, crop_size)
    subject = source.crop(crop_box)

    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), BACKGROUND)
    max_subject = CANVAS_SIZE - (PADDING * 2)
    subject.thumbnail((max_subject, max_subject), Image.Resampling.LANCZOS)

    offset_x = (CANVAS_SIZE - subject.width) // 2
    offset_y = (CANVAS_SIZE - subject.height) // 2
    canvas.paste(subject, (offset_x, offset_y), subject)
    return canvas


def save_icons(master: Image.Image) -> None:
    BUILD_RESOURCES.mkdir(parents=True, exist_ok=True)
    master.save(MASTER_PNG, format="PNG")
    master.save(MAC_ICON, format="ICNS")
    master.save(
        WIN_ICON,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> None:
    if not SOURCE_LOGO.exists():
        raise FileNotFoundError(f"Missing source logo: {SOURCE_LOGO}")

    master = build_master_image()
    save_icons(master)

    print(f"[NjordHR] Generated icon assets in {BUILD_RESOURCES}")
    for path in (MASTER_PNG, MAC_ICON, WIN_ICON):
        print(f"  - {path.relative_to(ROOT)} ({os.path.getsize(path)} bytes)")


if __name__ == "__main__":
    main()

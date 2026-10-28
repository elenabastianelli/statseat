"""PNG app icons from public/icon-512.png (the statseat logo, 512 x 512, transparent outside the rounded tile).

iPhone takes the home-screen icon only from <link rel="apple-touch-icon"> (PNG, 180 x 180, square: iOS rounds the
corners itself), and Android installs the app with the PNG icons of the manifest (192 and 512, plus a full-bleed
maskable one). The square versions fill the rounded corners with the tile's own background; the maskable one also
shrinks the logo into the safe circle, so that the name is not cut by a round mask.
Run with the generator's environment (Pillow): ../generator/.venv/bin/python scripts/gen_pwa_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PUBLIC = Path(__file__).resolve().parents[1] / "public"
LOGO = Image.open(PUBLIC / "icon-512.png").convert("RGBA")
MASKABLE_SCALE = 0.72  # the logo's corners stay inside the safe circle (radius 40% of the icon)


def background(size: int) -> Image.Image:
    """The tile's vertical gradient, read from its middle column."""
    column = [LOGO.getpixel((256, y)) for y in range(512)]
    solid = [p for p in column if p[3] == 255]
    top, bottom = solid[0], solid[-1]
    bg = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / (size - 1)
        bg.paste(tuple(round(a + (b - a) * t) for a, b in zip(top[:3], bottom[:3])) + (255,), (0, y, size, y + 1))
    return bg


def square(size: int, scale: float = 1.0) -> Image.Image:
    bg = background(size)
    side = round(size * scale)
    offset = (size - side) // 2
    bg.alpha_composite(LOGO.resize((side, side), Image.LANCZOS), (offset, offset))
    return bg.convert("RGB")


OUTPUTS = {
    "icon-192.png": lambda: LOGO.resize((192, 192), Image.LANCZOS),
    "icon-maskable-512.png": lambda: square(512, MASKABLE_SCALE),
    "apple-touch-icon.png": lambda: square(180),
}

if __name__ == "__main__":
    for name, make in OUTPUTS.items():
        make().save(PUBLIC / name, optimize=True)
        print("wrote", name)

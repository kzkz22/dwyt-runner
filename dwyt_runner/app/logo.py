"""The dwyt logo (decision 19.16): a video frame with a fast-forward mark and the summary line under it, on a blue
rounded tile. The same drawing as dwyt-central's favicon (central/tools/ikonok.py there); kept here as Pillow shapes so
the installer icon can be made without a browser. Used for the Windows installer and shortcuts, the AppImage, the
window and the favicon of the local page (SVG).
"""
from __future__ import annotations

from pathlib import Path

TILE = (0x13, 0x59, 0xAE)
INK = (255, 255, 255)
ACCENT = (0xF7, 0xA3, 0x5C)
FAINT = tuple(round(0.6 * i + 0.4 * t) for i, t in zip(INK, TILE))  # white at 60 % over the tile

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
       '<rect x="1" y="1" width="30" height="30" rx="7" fill="#1359ae"/>'
       '<rect x="6.5" y="6.5" width="19" height="12.5" rx="3" fill="none" stroke="#fff" stroke-width="2"/>'
       '<path d="M12.7 9.9l2.9 2.9-2.9 2.9M16.5 9.9l2.9 2.9-2.9 2.9" fill="none" stroke="#fff" stroke-width="2" '
       'stroke-linecap="round" stroke-linejoin="round"/>'
       '<path d="M7 24.6h11.5" stroke="#f7a35c" stroke-width="3.2" stroke-linecap="round"/>'
       '<path d="M22 24.6h3.5" stroke="#fff" stroke-opacity=".6" stroke-width="2" stroke-linecap="round"/></svg>\n')

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def image(size: int = 256):
    """The logo as an RGBA Pillow image, drawn at four times the size and scaled down (smooth edges)."""
    from PIL import Image, ImageDraw
    big = size * 4
    k = big / 32

    def at(*xy: float) -> list[float]:
        return [v * k for v in xy]
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(at(1, 1, 31, 31), radius=7 * k, fill=TILE)
    # The frame: an SVG stroke is centred on the edge, Pillow draws it inside the box.
    d.rounded_rectangle(at(5.5, 5.5, 26.5, 20), radius=4 * k, outline=INK, width=round(2 * k))
    for x in (12.7, 16.5):
        points = [(x * k, 9.9 * k), ((x + 2.9) * k, 12.8 * k), (x * k, 15.7 * k)]
        d.line(points, fill=INK, width=round(2 * k), joint="curve")
        for px, py in (points[0], points[2]):
            d.ellipse((px - k, py - k, px + k, py + k), fill=INK)
    d.rounded_rectangle(at(7 - 1.6, 23, 18.5 + 1.6, 26.2), radius=1.6 * k, fill=ACCENT)
    d.rounded_rectangle(at(22 - 1, 23.6, 25.5 + 1, 25.6), radius=k, fill=FAINT)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def write_icons(out: Path) -> None:
    """dwyt-runner.png (256 px) and dwyt-runner.ico with every size drawn on its own."""
    out.mkdir(parents=True, exist_ok=True)
    images = [image(s) for s in ICO_SIZES]
    images[-1].save(out / "dwyt-runner.png")
    images[-1].save(out / "dwyt-runner.ico", sizes=[(s, s) for s in ICO_SIZES], append_images=images[:-1])

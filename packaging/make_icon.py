"""Draws Runway's icon — the brand mark (a takeoff vector off a runway strip) on an accent tile, as
the rail and the favicon show it — into packaging/runway.ico for the launcher and the program.

    python packaging/make_icon.py          (needs Pillow; the .ico is committed, so only to redraw)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (90, 176, 240, 255)     # --accent #5ab0f0
INK = (4, 18, 29, 255)           # --on-accent #04121d
SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = Path(__file__).resolve().parent / "runway.ico"


def draw(px: int) -> Image.Image:
    s = 8                                           # draw large, then scale down: smooth edges
    big = px * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 6 / 24, fill=ACCENT)
    u = big / 24                                    # the mark is drawn on a 24-unit grid
    w = max(2, round(2.2 * u))

    def line(points):
        pts = [(x * u, y * u) for x, y in points]
        d.line(pts, fill=INK, width=w, joint="curve")
        r = w / 2
        for x, y in (pts[0], pts[-1]):              # round caps
            d.ellipse([x - r, y - r, x + r, y + r], fill=INK)

    line([(4, 19), (10, 19)])                       # the strip
    line([(6.5, 15.5), (18.5, 6)])                  # the climb
    line([(13.5, 6), (18.5, 6), (18.5, 11)])        # the arrow head
    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    images = [draw(px) for px in SIZES]
    images[-1].save(OUT, format="ICO", sizes=[(px, px) for px in SIZES], append_images=images[:-1])
    print("wrote", OUT)


if __name__ == "__main__":
    main()

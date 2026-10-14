"""Generate the Sada application icon.

The design is flat and geometric, so it is defined once here as numbers and
rendered from them - rather than drawn by hand in two places and left to drift.
One run produces:

    assets/sada.svg          vector, for the web and for any future resizing
    assets/sada.ico          multi-resolution, what Windows and the exe use
    assets/sada-1024.png     for Docker Hub, GitHub and store listings
    assets/sada-256.png
    assets/sada-preview.png  all the small sizes side by side, to check legibility

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tools/make_icon.py

The brief it implements: a bold waveform flowing into the corner of a document
page - speech becoming written text. One idea, one silhouette.

Everything is sized as a fraction of the canvas so the shapes hold up at 16x16,
which is the size that actually decides whether an icon works. The strokes are
far thicker than they need to be at 1024 for exactly that reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024                      # design canvas; everything scales from this

# Three colours, no more. Deep indigo ground, off-white page, one warm accent.
INDIGO = (30, 58, 138)        # #1E3A8A
PAPER = (248, 250, 252)       # #F8FAFC
AMBER = (245, 158, 11)        # #F59E0B

BG_RADIUS = int(S * 0.12)     # rounded square, ~12%

# The page: right of centre, portrait, with its top-right corner cut away to
# read as a sheet of paper rather than a plain rectangle. The cut is a large
# diagonal, which survives being scaled to 16 px; a drawn fold line would not.
PAGE = (int(S * 0.445), int(S * 0.146), int(S * 0.905), int(S * 0.854))
PAGE_RADIUS = int(S * 0.043)
CORNER_CUT = int(S * 0.185)

# Three capsule bars, ascending then settling, like a short utterance. Each is
# ~10% of the canvas wide - above the 8% minimum. The gap is deliberately wide:
# at 16 px a narrower one falls below a single pixel and the three bars smear
# into one amber block, which is where most icons of this kind fall apart.
BAR_W = int(S * 0.098)
BAR_GAP = int(S * 0.072)
BAR_X0 = int(S * 0.095)
BAR_HEIGHTS = (int(S * 0.255), int(S * 0.46), int(S * 0.345))
BAR_CENTRE_Y = S // 2

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def bar_boxes() -> list[tuple[int, int, int, int]]:
    boxes = []
    x = BAR_X0
    for h in BAR_HEIGHTS:
        boxes.append((x, BAR_CENTRE_Y - h // 2, x + BAR_W, BAR_CENTRE_Y + h // 2))
        x += BAR_W + BAR_GAP
    return boxes


def render(size: int = S) -> Image.Image:
    """Draw at 4x and downsample, so every edge and curve is properly smooth."""
    scale = 4
    n = size * scale
    k = n / S                                  # design units -> pixels

    def p(v: float) -> float:
        return v * k

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=p(BG_RADIUS), fill=INDIGO)

    x0, y0, x1, y1 = PAGE
    d.rounded_rectangle([p(x0), p(y0), p(x1), p(y1)],
                        radius=p(PAGE_RADIUS), fill=PAPER)

    # Cut the top-right corner by painting the background back over it. Simpler
    # and crisper than building a rounded pentagon, and the diagonal stays exact.
    d.polygon([(p(x1 - CORNER_CUT), p(y0 - 2)),
               (p(x1 + 2), p(y0 + CORNER_CUT)),
               (p(x1 + 2), p(y0 - 2))], fill=INDIGO)

    # Bars last, so the final one crosses the page edge: the waveform running
    # into the paper is the whole idea, and the overlap is what says it.
    for bx0, by0, bx1, by1 in bar_boxes():
        d.rounded_rectangle([p(bx0), p(by0), p(bx1), p(by1)],
                            radius=p(BAR_W / 2), fill=AMBER)

    return img.resize((size, size), Image.LANCZOS)


def svg() -> str:
    """The same geometry as vector, for the web and for any later resizing."""
    x0, y0, x1, y1 = PAGE
    bars = "\n".join(
        f'    <rect x="{bx0}" y="{by0}" width="{bx1 - bx0}" height="{by1 - by0}"'
        f' rx="{BAR_W // 2}" fill="#F59E0B"/>'
        for bx0, by0, bx1, by1 in bar_boxes()
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}"
     width="{S}" height="{S}" role="img" aria-label="Sada">
  <title>Sada</title>
  <desc>A sound waveform running into the corner of a document page.</desc>

  <rect width="{S}" height="{S}" rx="{BG_RADIUS}" fill="#1E3A8A"/>

  <!-- the page, with its top-right corner cut away -->
  <path d="M {x0 + PAGE_RADIUS} {y0}
           H {x1 - CORNER_CUT}
           L {x1} {y0 + CORNER_CUT}
           V {y1 - PAGE_RADIUS}
           a {PAGE_RADIUS} {PAGE_RADIUS} 0 0 1 -{PAGE_RADIUS} {PAGE_RADIUS}
           H {x0 + PAGE_RADIUS}
           a {PAGE_RADIUS} {PAGE_RADIUS} 0 0 1 -{PAGE_RADIUS} -{PAGE_RADIUS}
           V {y0 + PAGE_RADIUS}
           a {PAGE_RADIUS} {PAGE_RADIUS} 0 0 1 {PAGE_RADIUS} -{PAGE_RADIUS} Z"
        fill="#F8FAFC"/>

  <!-- the waveform, the last bar crossing into the page -->
  <g>
{bars}
  </g>
</svg>
"""


def preview(out: Path) -> None:
    """Every small size in a row, on a mid grey, to judge legibility honestly."""
    sizes = ICO_SIZES
    pad = 12
    width = sum(sizes) + pad * (len(sizes) + 1)
    height = max(sizes) + pad * 2
    sheet = Image.new("RGB", (width, height), (128, 128, 128))
    x = pad
    for s in sizes:
        sheet.paste(render(s), (x, (height - s) // 2), render(s))
        x += s + pad
    sheet.save(out)


def main() -> int:
    out = Path(__file__).resolve().parent.parent / "assets"
    out.mkdir(exist_ok=True)

    (out / "sada.svg").write_text(svg(), encoding="utf-8")

    master = render(S)
    master.save(out / "sada-1024.png")
    render(256).save(out / "sada-256.png")

    # One .ico holding every size Windows asks for. Letting Windows downscale a
    # single large image instead gives a muddy 16x16 in the tray and the title
    # bar, which is where this icon is seen most.
    master.save(out / "sada.ico", sizes=[(s, s) for s in ICO_SIZES])

    preview(out / "sada-preview.png")

    for f in sorted(out.iterdir()):
        print(f"  {f.name:22s} {f.stat().st_size / 1024:7.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

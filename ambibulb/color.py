"""Color extraction and shaping -- all pure functions, no I/O.

Kept dependency-light: Pillow is used when available for fast, good-looking
extraction; a pure-Python fallback covers the no-Pillow case.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover - exercised only without Pillow
    HAVE_PIL = False

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class ColorOptions:
    """How a sampled RGB color is shaped before being sent to a bulb."""

    saturation: float = 1.0        # linear saturation scale
    gamma: float = 1.0             # extra saturation curve (1.0 = off)
    brightness: int = 100          # fixed bulb brightness %, unless follow=True
    follow_brightness: bool = False
    brightness_floor: int = 15     # min % when follow_brightness is on


# --------------------------------------------------------------------------- #
# Whole-image extraction
# --------------------------------------------------------------------------- #
def average_color(bgra: bytes, width: int, height: int, step: int = 4) -> RGB:
    """Average a raw BGRA buffer, sampling every `step` pixels (PIL-free path)."""
    r = g = b = n = 0
    row = width * 4
    for y in range(0, height, step):
        base = y * row
        for x in range(0, width, step):
            i = base + x * 4
            b += bgra[i]
            g += bgra[i + 1]
            r += bgra[i + 2]
            n += 1
    if n == 0:
        return (0, 0, 0)
    return (r // n, g // n, b // n)


def average_color_pil(img: "Image.Image") -> RGB:
    """Average color via a 1x1 thumbnail (done in C by Pillow)."""
    r, g, b = img.resize((1, 1), Image.BILINEAR).getpixel((0, 0))[:3]
    return (r, g, b)


# --------------------------------------------------------------------------- #
# Video-aware extraction
# --------------------------------------------------------------------------- #
def trim_letterbox(img: "Image.Image", thresh: int = 20) -> "Image.Image":
    """Crop near-black letterbox/pillarbox bars; bail out if the crop looks wrong."""
    w0, h0 = img.size
    small = img.resize((64, 64), Image.BILINEAR)
    px = small.load()
    sw, sh = small.size

    def row_dark(y: int) -> bool:
        return all(max(px[x, y]) < thresh for x in range(0, sw, 2))

    def col_dark(x: int) -> bool:
        return all(max(px[x, y]) < thresh for y in range(0, sh, 2))

    top, bot, left, right = 0, sh - 1, 0, sw - 1
    while top < bot and row_dark(top):
        top += 1
    while bot > top and row_dark(bot):
        bot -= 1
    while left < right and col_dark(left):
        left += 1
    while right > left and col_dark(right):
        right -= 1
    box = (int(left / sw * w0), int(top / sh * h0),
           int((right + 1) / sw * w0), int((bot + 1) / sh * h0))
    if box[2] - box[0] < w0 // 3 or box[3] - box[1] < h0 // 3:
        return img
    return img.crop(box)


#: valid extraction methods for zone_colors()
METHODS = ("average", "vibrance", "vivid")


def _plain(pixels: list[RGB]) -> RGB:
    n = len(pixels) or 1
    r = sum(p[0] for p in pixels)
    g = sum(p[1] for p in pixels)
    b = sum(p[2] for p in pixels)
    return (r // n, g // n, b // n)


def _vibrance(pixels: list[RGB]) -> RGB:
    """Saturation-weighted average -- colorful pixels pull harder. Good for
    general video where you still want the overall mix."""
    wr = wg = wb = wsum = 0.0
    for r, g, b in pixels:
        sat = (max(r, g, b) - min(r, g, b)) / 255.0
        wt = 0.12 + sat
        wr += r * wt
        wg += g * wt
        wb += b * wt
        wsum += wt
    if wsum <= 0:
        return _plain(pixels)
    return (int(wr / wsum), int(wg / wsum), int(wb / wsum))


def _vivid(pixels: list[RGB], bins: int = 24) -> RGB:
    """Dominant vivid hue via a saturation*value-weighted hue histogram.

    Built for anime/animation: big flat fields of pure color. It votes each
    colorful pixel into a hue bin, finds the strongest hue (counting neighbors
    so near-identical hues combine), and returns the average color of that hue
    -- so a vivid cyan subject on a dark frame reads as cyan, not grey. Falls
    back to a plain average for a greyscale scene.
    """
    acc = [0.0] * bins
    rs = [0.0] * bins
    gs = [0.0] * bins
    bs = [0.0] * bins
    for r, g, b in pixels:
        mx, mn = max(r, g, b), min(r, g, b)
        v = mx / 255.0
        s = 0.0 if mx == 0 else (mx - mn) / mx
        if s < 0.12 or v < 0.08:      # ignore greys and near-black for the vote
            continue
        h, _, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        wt = s * v
        i = int(h * bins) % bins
        acc[i] += wt
        rs[i] += r * wt
        gs[i] += g * wt
        bs[i] += b * wt
    if sum(acc) <= 0:
        return _plain(pixels)

    def score(i: int) -> float:
        return acc[i] + 0.5 * (acc[(i - 1) % bins] + acc[(i + 1) % bins])

    best = max(range(bins), key=score)
    w = acc[best] or 1.0
    return (int(rs[best] / w), int(gs[best] / w), int(bs[best] / w))


_REDUCERS = {"average": _plain, "vibrance": _vibrance, "vivid": _vivid}


def zone_colors(img: "Image.Image", zones: int, method: str = "average"
                ) -> list[RGB]:
    """One representative color per horizontal zone (left -> right).

    method: 'average' (plain mix), 'vibrance' (saturation-weighted, general
    video), or 'vivid' (dominant hue, best for anime/animation). Runs on a tiny
    thumbnail, so it stays cheap.
    """
    zones = max(1, zones)
    reduce = _REDUCERS.get(method, _plain)
    cells, rows = 12, 10
    small = img.resize((zones * cells, rows), Image.BILINEAR).convert("RGB")
    px = small.load()
    out: list[RGB] = []
    for z in range(zones):
        x0 = z * cells
        pixels = [px[xx, yy] for xx in range(x0, x0 + cells)
                  for yy in range(rows)]
        out.append(reduce(pixels))
    return out


# --------------------------------------------------------------------------- #
# Shaping RGB -> bulb HSV
# --------------------------------------------------------------------------- #
def shape_hsv(rgb: RGB, opts: ColorOptions) -> tuple[float, float, float]:
    """Map a screen RGB color to bulb HSV (all 0..1 for tinytuya.set_hsv).

    Hue is taken straight from the screen. Saturation is scaled so the bulb
    isn't neon. Brightness is a fixed bright value by default (accurate color),
    or tracks the scene with a floor when follow_brightness is set.
    """
    r, g, b = (c / 255.0 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    s *= opts.saturation
    if opts.gamma != 1.0:
        s = s ** (1.0 / opts.gamma)
    s = min(1.0, max(0.0, s))
    if opts.follow_brightness:
        v = max(opts.brightness_floor / 100.0, v)
    else:
        v = opts.brightness / 100.0
    return (h, s, min(1.0, max(0.0, v)))

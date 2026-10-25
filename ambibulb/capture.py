"""Screen capture and per-zone color sampling."""

from __future__ import annotations

from dataclasses import dataclass

import mss

from . import color
from .color import RGB


@dataclass(frozen=True)
class CaptureOptions:
    monitor: int = 1                      # 1 = primary
    region: tuple[int, int, int, int] | None = None  # (left, top, w, h)
    zones: int = 1
    method: str = "average"              # average | vibrance | vivid
    letterbox: bool = True               # auto-crop black bars


class ScreenSampler:
    """Grabs the screen and returns one color per zone (left -> right)."""

    def __init__(self, opts: CaptureOptions) -> None:
        self.opts = opts
        self._sct = (mss.MSS if hasattr(mss, "MSS") else mss.mss)()
        self._region = self._resolve_region()

    def _resolve_region(self) -> dict:
        if self.opts.region:
            left, top, w, h = self.opts.region
            return {"left": left, "top": top, "width": w, "height": h}
        mons = self._sct.monitors
        idx = self.opts.monitor if 0 <= self.opts.monitor < len(mons) else 1
        return mons[idx]

    def sample(self) -> list[RGB]:
        zones = max(1, self.opts.zones)
        shot = self._sct.grab(self._region)
        if color.HAVE_PIL:
            img = color.Image.frombytes("RGB", shot.size, shot.rgb)
            if self.opts.letterbox:
                img = color.trim_letterbox(img)
            return color.zone_colors(img, zones, self.opts.method)
        c = color.average_color(shot.bgra, shot.width, shot.height, step=8)
        return [c] * zones

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass

    def __enter__(self) -> "ScreenSampler":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

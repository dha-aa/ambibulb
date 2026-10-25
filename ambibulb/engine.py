"""The ambient streaming engine: screen -> per-bulb color, live."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .bulb import Bulb
from .capture import ScreenSampler
from .color import ColorOptions, shape_hsv

log = logging.getLogger("ambibulb.engine")


@dataclass(frozen=True)
class EngineOptions:
    fps: int = 15             # screen samples per second
    smooth: float = 0.4       # easing 0..1 toward the new color per frame
    min_delta: int = 8        # only push when the eased color moves this much
    cut: int = 120            # per-channel jump above this snaps (scene cut)


class AmbientEngine:
    """Owns the sampler and bulbs, maps zones to bulbs, runs the loop."""

    def __init__(self, bulbs: list[Bulb], sampler: ScreenSampler,
                 color_opts: ColorOptions, engine_opts: EngineOptions,
                 zones: int) -> None:
        self.bulbs = bulbs
        self.sampler = sampler
        self.color = color_opts
        self.opts = engine_opts
        self.zones = max(1, zones)
        n = len(bulbs)
        # Map each bulb to a zone, left -> right, in config order.
        self.bulb_zone = [int(i * self.zones / n) for i in range(n)]

    def start_bulbs(self) -> None:
        for b in self.bulbs:
            b.start()

    def stop_bulbs(self) -> None:
        for b in self.bulbs:
            b.stop()

    def push_once(self) -> list[tuple[str, tuple[int, int, int]]]:
        """Sample and push a single color to each bulb. Returns (name, rgb)."""
        zc = self.sampler.sample()
        result = []
        for i, b in enumerate(self.bulbs):
            rgb = zc[self.bulb_zone[i]]
            b.set(shape_hsv(rgb, self.color))
            result.append((b.name, rgb))
        return result

    def run(self, stop_event: threading.Event, seconds: float = 0.0) -> None:
        """Stream until `stop_event` is set (or `seconds` elapse, if > 0)."""
        n = len(self.bulbs)
        cur = [[0.0, 0.0, 0.0] for _ in range(n)]
        last_pushed: list[tuple[int, int, int] | None] = [None] * n
        interval = 1.0 / max(1, self.opts.fps)
        deadline = time.time() + seconds if seconds > 0 else None

        while not stop_event.is_set():
            if deadline is not None and time.time() >= deadline:
                break
            t0 = time.time()
            zc = self.sampler.sample()
            for i, b in enumerate(self.bulbs):
                target = zc[self.bulb_zone[i]]
                c = cur[i]
                jump = sum(abs(target[k] - c[k]) for k in range(3))
                a = 0.9 if jump > self.opts.cut else self.opts.smooth
                for k in range(3):
                    c[k] += (target[k] - c[k]) * a
                eased = (round(c[0]), round(c[1]), round(c[2]))
                prev = last_pushed[i]
                if prev is None or sum(abs(x - y) for x, y in zip(eased, prev)) \
                        >= self.opts.min_delta:
                    b.set(shape_hsv(eased, self.color))
                    last_pushed[i] = eased
            dt = time.time() - t0
            if dt < interval:
                stop_event.wait(interval - dt)

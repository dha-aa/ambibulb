"""Command-line entry point for ambibulb.

Usage:
    ambibulb run [options]          stream screen color to the bulbs (default)
    ambibulb once [options]         push the current screen color once and exit
    ambibulb discover               scan the LAN for Tuya devices
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__, color
from .bulb import Bulb, BulbOptions
from .capture import CaptureOptions, ScreenSampler
from .color import ColorOptions
from .config import ConfigError, load_bulbs
from .engine import AmbientEngine, EngineOptions

log = logging.getLogger("ambibulb")

# Base defaults for the tunable knobs a preset may set. A knob left unset on
# the command line (None) resolves to the preset's value, else to BASE.
BASE = {
    "mode": "average", "zones": 1, "saturation": 1.0, "brightness": 100,
    "smooth": 0.4, "min_delta": 8, "cut": 120, "bulb_rate": 12, "fps": 15,
}
PRESETS = {
    "default": {},
    # Anime/animation: flat vivid fields + fast cuts -> lock onto the dominant
    # hue, punch the saturation, react snappy, push at the firmware ceiling.
    "anime": {"mode": "vivid", "saturation": 1.3, "smooth": 0.8, "min_delta": 4,
              "cut": 70, "bulb_rate": 15, "fps": 24},
    # Live-action film: smoother, truer color, gentler cuts.
    "movie": {"mode": "vibrance", "saturation": 1.05, "smooth": 0.25,
              "min_delta": 6, "cut": 120},
    # Games: vivid and snappy like anime but slightly calmer saturation.
    "game": {"mode": "vivid", "saturation": 1.15, "smooth": 0.8, "min_delta": 4,
             "cut": 60, "bulb_rate": 15, "fps": 24},
}
_MODE_TO_METHOD = {"average": "average", "dominant": "vibrance", "vivid": "vivid"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ambibulb",
        description="Drive Wipro/Tuya smart bulbs from your screen color (local, no cloud).",
    )
    p.add_argument("command", nargs="?", default="run",
                   choices=("run", "once", "discover"),
                   help="run (default), once, or discover")
    p.add_argument("--version", action="version", version=f"ambibulb {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    g = p.add_argument_group("config")
    g.add_argument("--config", type=Path, default=Path("bulbs.json"))

    p.add_argument("--preset", choices=tuple(PRESETS), default="default",
                   help="tuning bundle: anime (vivid+snappy), movie, game. "
                        "Any explicit flag below overrides the preset.")

    g = p.add_argument_group("capture")
    g.add_argument("--monitor", type=int, default=1, help="monitor index (1=primary)")
    g.add_argument("--region", type=int, nargs=4, metavar=("L", "T", "W", "H"))
    g.add_argument("--zones", type=int, default=None,
                   help="split picture into N left->right zones mapped across bulbs")
    g.add_argument("--mode", choices=("average", "dominant", "vivid"), default=None,
                   help="average = plain mix; dominant = vibrance-weighted (video); "
                        "vivid = dominant hue (best for anime/animation)")
    g.add_argument("--no-letterbox", action="store_true",
                   help="don't auto-crop black movie bars")

    g = p.add_argument_group("color")
    g.add_argument("--saturation", type=float, default=None)
    g.add_argument("--brightness", type=int, default=None,
                   help="fixed bulb brightness %% (1-100)")
    g.add_argument("--follow-brightness", action="store_true",
                   help="bulb brightness tracks the scene instead of fixed bright")
    g.add_argument("--brightness-floor", type=int, default=15)
    g.add_argument("--gamma", type=float, default=1.0)

    g = p.add_argument_group("timing")
    g.add_argument("--fps", type=int, default=None)
    g.add_argument("--bulb-rate", type=int, default=None,
                   help="max color commands/sec per bulb (firmware tops out ~12-15)")
    g.add_argument("--smooth", type=float, default=None)
    g.add_argument("--min-delta", type=int, default=None)
    g.add_argument("--cut", type=int, default=None, help="scene-cut snap threshold")
    g.add_argument("--seconds", type=float, default=0.0,
                   help="run for N seconds then stop (0 = until Ctrl-C)")
    return p


def resolve_preset(a: argparse.Namespace) -> None:
    """Fill any unset (None) tunable from the chosen preset, else BASE."""
    preset = PRESETS[a.preset]
    for key, base in BASE.items():
        if getattr(a, key) is None:
            setattr(a, key, preset.get(key, base))


def _color_opts(a: argparse.Namespace) -> ColorOptions:
    return ColorOptions(
        saturation=a.saturation, gamma=a.gamma, brightness=a.brightness,
        follow_brightness=a.follow_brightness, brightness_floor=a.brightness_floor,
    )


def _capture_opts(a: argparse.Namespace) -> CaptureOptions:
    return CaptureOptions(
        monitor=a.monitor,
        region=tuple(a.region) if a.region else None,
        zones=max(1, a.zones),
        method=_MODE_TO_METHOD[a.mode],
        letterbox=not a.no_letterbox,
    )


def cmd_discover() -> int:
    from .discovery import scan

    print("Scanning LAN for Tuya devices (~18s)...\n")
    devices = scan()
    if not devices:
        print("No devices found. Check they're powered, on the same network, "
              "and paired in the Wipro/Smart Life app.")
        return 1
    print(f"Found {len(devices)} device(s):\n")
    for d in devices:
        print(f"  ip={d.ip}  id={d.id}  version={d.version}")
    print("\nGet local keys with:  python3 -m tinytuya wizard")
    return 0


def _make_engine(a: argparse.Namespace) -> AmbientEngine:
    bulb_cfgs = load_bulbs(a.config)
    bopts = BulbOptions(rate=a.bulb_rate)
    bulbs = [Bulb(c, bopts) for c in bulb_cfgs]
    sampler = ScreenSampler(_capture_opts(a))
    engine = AmbientEngine(
        bulbs=bulbs, sampler=sampler,
        color_opts=_color_opts(a),
        engine_opts=EngineOptions(fps=a.fps, smooth=min(1.0, max(0.01, a.smooth)),
                                  min_delta=a.min_delta, cut=a.cut),
        zones=max(1, a.zones),
    )
    return engine


def cmd_once(a: argparse.Namespace) -> int:
    if a.mode in ("dominant", "vivid") and not color.HAVE_PIL:
        log.error("--mode dominant/vivid needs Pillow:  pip install pillow")
        return 2
    engine = _make_engine(a)
    engine.start_bulbs()
    try:
        print(f"Connecting to {len(engine.bulbs)} bulb(s)...")
        for name, rgb in engine.push_once():
            print(f"  {name}: #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
        threading.Event().wait(2.0)  # let worker threads deliver
    finally:
        engine.stop_bulbs()
        engine.sampler.close()
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    if a.mode in ("dominant", "vivid") and not color.HAVE_PIL:
        log.error("--mode dominant/vivid needs Pillow:  pip install pillow")
        return 2
    engine = _make_engine(a)
    stop = threading.Event()

    def _handle(_sig, _frm):
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    engine.start_bulbs()
    msg = f"for {a.seconds:.0f}s" if a.seconds > 0 else "Ctrl-C to stop"
    print(f"Streaming screen color to {len(engine.bulbs)} bulb(s) ({msg}). "
          f"zones={engine.zones} mode={a.mode}\n")
    try:
        engine.run(stop, seconds=a.seconds)
    finally:
        print("\nStopping.")
        engine.stop_bulbs()
        engine.sampler.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    resolve_preset(a)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(message)s",
    )
    try:
        if a.command == "discover":
            return cmd_discover()
        if a.command == "once":
            return cmd_once(a)
        return cmd_run(a)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

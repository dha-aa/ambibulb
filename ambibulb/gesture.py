"""Hand-gesture control of the bulbs via the webcam (MediaPipe + OpenCV).

Optional feature. Install the extra deps with:

    pip install "ambibulb[gesture]"

Gesture scheme (a small preview window shows the live state):

  pinch thumb+index,  double-tap            -> toggle SELECTED bulb on/off
  pinch thumb+index,  hold + move up/down   -> brightness of SELECTED bulb
  pinch thumb+middle, double-tap            -> toggle ALL bulbs on/off
  pinch thumb+middle, hold + move up/down   -> brightness of ALL bulbs
  RIGHT open hand, quick flick left/right   -> switch which bulb is selected
  LEFT  open hand, quick flick left/right   -> cycle color (warm/cool/R/G/B/white)

A pinch is the thumb tip touching the fingertip; the other fingers can be in
any position (an 'OK' sign works). Pinches work with either hand. Landmarks
are One-Euro filtered and brief detection dropouts are bridged. macOS asks for
Camera permission on first run; use --swap-hands if left/right read reversed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .bulb import Bulb, BulbOptions
from .config import load_bulbs

log = logging.getLogger("ambibulb.gesture")

# MediaPipe hand landmark indices (21 points per hand).
_TIPS = [8, 12, 16, 20]   # index, middle, ring, pinky fingertips
_PIPS = [6, 10, 14, 18]   # their PIP joints

#: color cycle for the left-hand swipe: label -> (hue, saturation) in 0..1
COLORS: list[tuple[str, tuple[float, float]]] = [
    ("warm", (0.08, 0.55)),
    ("cool", (0.55, 0.30)),
    ("red", (0.00, 1.00)),
    ("green", (0.33, 1.00)),
    ("blue", (0.66, 1.00)),
    ("white", (0.00, 0.00)),
]


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def finger_states(lm) -> tuple[bool, bool, bool, bool, bool]:
    """(thumb, index, middle, ring, pinky) extended? from 21 landmarks.

    Rotation-invariant: a finger is extended when its tip is clearly farther
    from the wrist than its PIP joint (works whether the hand points up,
    sideways, or tilted)."""
    if not lm or len(lm) < 21:
        return (False, False, False, False, False)
    w = lm[0]
    four = [_dist(lm[t], w) > _dist(lm[p], w) * 1.1 for t, p in zip(_TIPS, _PIPS)]
    thumb = _dist(lm[4], lm[5]) > _dist(lm[3], lm[5]) * 1.3
    return (thumb, four[0], four[1], four[2], four[3])


def hand_scale(lm) -> float:
    """Hand size (wrist -> middle-finger base). Dividing by this makes pinch
    measurements independent of how far the hand is from the camera."""
    return max(_dist(lm[0], lm[9]), 1e-6)


def pinch_ratio(lm, finger_tip: int) -> float:
    """Thumb-tip to `finger_tip` gap, in hand-size units (~0.1 touching,
    ~1.0+ fully spread)."""
    return _dist(lm[4], lm[finger_tip]) / hand_scale(lm)


def count_fingers(lm) -> int:
    return sum(finger_states(lm))


def pinch_distance(lm) -> float:
    """Normalized gap between thumb tip (4) and index tip (8)."""
    if not lm or len(lm) < 21:
        return 0.0
    return _dist(lm[4], lm[8])


def hand_x(lm) -> float:
    """Horizontal hand position (middle-finger MCP), 0..1."""
    return lm[9][0] if lm and len(lm) > 9 else 0.5


def is_fist(st) -> bool:
    return not any(st)


def is_open_palm(st) -> bool:
    return st[1] and st[2] and st[3] and st[4]


def is_thumbs_up(lm, st) -> bool:
    # Thumb extended, other four curled, thumb pointing up (tip above wrist).
    return st[0] and not any(st[1:]) and lm[4][1] < lm[0][1] - 0.1


def is_pinch_index_pose(st) -> bool:
    # Index extended, middle/ring/pinky curled (thumb+index pinch).
    return st[1] and not st[2] and not st[3] and not st[4]


def is_pinch_middle_pose(st) -> bool:
    # Middle extended, index/ring/pinky curled (thumb+middle pinch).
    return st[2] and not st[1] and not st[3] and not st[4]


_OTHER = {"Left": "Right", "Right": "Left"}


def assign_hands(detected: list[tuple[str, list]], swap: bool = False) -> dict:
    """Map detected hands to the user's real 'Left'/'Right'.

    The preview is mirrored (like a mirror: your right hand is on the right of
    the screen). MediaPipe's handedness label comes out inverted for this
    mirrored input, so:
      * two hands  -> trust screen position: larger x is the right hand;
      * one hand   -> use MediaPipe's label, inverted.
    `swap` flips the result for cameras that behave the other way."""
    out: dict[str, list] = {}
    # MediaPipe occasionally reports the same hand twice (two overlapping
    # detections). Drop a second hand whose palm centre sits within half a
    # hand-length of the first, or one real hand becomes a fake Left+Right pair.
    if len(detected) >= 2:
        (_, a), (_, b) = detected[0], detected[1]
        if _dist(a[9], b[9]) < 0.5 * max(hand_scale(a), hand_scale(b)):
            detected = detected[:1]
    if len(detected) >= 2:
        a, b = sorted(detected[:2], key=lambda d: hand_x(d[1]))
        out = {"Left": a[1], "Right": b[1]}
    elif len(detected) == 1:
        label, lm = detected[0]
        out = {_OTHER.get(label, "Right"): lm}
    if swap:
        out = {_OTHER[k]: v for k, v in out.items()}
    return out


class SwipeDetector:
    """Detects a fast horizontal swipe from a stream of (x, t) samples."""

    def __init__(self, min_dx: float = 0.12, window: float = 0.35,
                 cooldown: float = 0.6) -> None:
        self.min_dx = min_dx
        self.window = window
        self.cooldown = cooldown
        self._hist: list[tuple[float, float]] = []
        self._ready_at = 0.0

    def update(self, x: float, t: float) -> str | None:
        self._hist.append((t, x))
        self._hist = [(tt, xx) for tt, xx in self._hist if t - tt <= self.window]
        if t < self._ready_at or len(self._hist) < 2:
            return None
        dx = x - self._hist[0][1]
        if abs(dx) >= self.min_dx:
            self._ready_at = t + self.cooldown
            self._hist = [(t, x)]
            return "right" if dx > 0 else "left"
        return None

    def reset(self) -> None:
        self._hist = []


class OneEuro:
    """One-Euro filter (Casiez et al.): strong smoothing when the hand is still
    (kills jitter), little lag when it moves fast (keeps swipes responsive)."""

    def __init__(self, min_cutoff: float = 2.0, beta: float = 4.0,
                 d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x: float | None = None
        self._dx = 0.0
        self._t = 0.0

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2 * 3.141592653589793 * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._x is None:
            self._x, self._t = x, t
            return x
        dt = max(t - self._t, 1e-3)
        dx = (x - self._x) / dt
        self._dx += self._alpha(dt, self.d_cutoff) * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        self._x += self._alpha(dt, cutoff) * (x - self._x)
        self._t = t
        return self._x


class HandSmoother:
    """Per-hand landmark smoothing + dropout bridging.

    Each hand's 63 coordinates go through a One-Euro filter. If a hand vanishes
    for up to `hold` s (a dropped detection), its last pose is kept so an
    in-progress pinch or drag doesn't break."""

    def __init__(self, hold: float = 0.2) -> None:
        self.hold = hold
        self._filters: dict[str, list[OneEuro]] = {}
        self._last: dict[str, tuple[float, list]] = {}

    def update(self, hands: dict, t: float) -> dict:
        out = {}
        for label, lm in hands.items():
            fs = self._filters.setdefault(label, [OneEuro() for _ in range(63)])
            flat = [fs[i](v, t) for i, v in enumerate(c for p in lm for c in p)]
            out[label] = [tuple(flat[i:i + 3]) for i in range(0, 63, 3)]
            self._last[label] = (t, out[label])
        for label, (seen, lm) in list(self._last.items()):
            if label in out:
                continue
            if t - seen <= self.hold:
                out[label] = lm
            else:
                del self._last[label]
                self._filters.pop(label, None)
        return out


class HandLabeler:
    """assign_hands() plus stickiness: a single hand that stays in place keeps
    its label instead of flickering between Left and Right."""

    def __init__(self, swap: bool = False, near: float = 0.12,
                 memory: float = 0.6) -> None:
        self.swap, self.near, self.memory = swap, near, memory
        self._seen: dict[str, tuple[float, float]] = {}   # label -> (x, t)

    def _recent_near(self, label: str, x: float, t: float) -> bool:
        p = self._seen.get(label)
        return p is not None and abs(p[0] - x) < self.near and t - p[1] < self.memory

    def __call__(self, detected: list[tuple[str, list]], t: float) -> dict:
        out = assign_hands(detected, swap=self.swap)
        if len(out) == 1:
            (label, lm), = out.items()
            x, other = hand_x(lm), _OTHER[label]
            if self._recent_near(other, x, t) and not self._recent_near(label, x, t):
                out = {other: lm}
        for label, lm in out.items():
            self._seen[label] = (hand_x(lm), t)
        return out


class PinchGesture:
    """Thumb-to-finger pinch for one hand: double-tap and pinch-hold-drag.

    A pinch is the thumb tip touching the index or middle fingertip — any pose
    of the other fingers is fine (an 'OK' sign counts). Ratios are in hand-size
    units, so distance from the camera doesn't matter.

    Events returned by update():
      ("double", finger)            two brief pinches within MAX_GAP s
      ("drag_start", finger)        pinch held for HOLD s -> start dragging
      ("drag", finger, dy)          while held: hand moved dy up (screen units)
      ("drag_end", finger)          released after dragging
    """

    CLOSE, OPEN = 0.30, 0.50         # touch / release (hysteresis)
    MAX_TAP, MAX_GAP = 0.30, 0.50    # s: max tap length, max gap between taps
    HOLD = 0.30                      # s: pinch held this long becomes a drag

    def __init__(self) -> None:
        self.closed = False
        self.finger: str | None = None
        self.dragging = False
        self._close_t = 0.0
        self._start_y = 0.0
        self._taps = 0
        self._tap_finger: str | None = None
        self._last_release = -9.0

    def update(self, ri: float, rm: float, y: float, t: float) -> list[tuple]:
        ev: list[tuple] = []
        if not self.closed:
            r, finger = (ri, "index") if ri <= rm else (rm, "middle")
            if r < self.CLOSE:
                self.closed, self.finger, self.dragging = True, finger, False
                self._close_t = t
            return ev

        f = self.finger
        if (ri if f == "index" else rm) > self.OPEN:          # released
            self.closed = False
            if self.dragging:
                self.dragging = False
                ev.append(("drag_end", f))
            elif t - self._close_t <= self.MAX_TAP:             # a tap
                quick = (t - self._last_release <= self.MAX_GAP
                         and self._tap_finger == f)
                self._taps = self._taps + 1 if quick else 1
                self._tap_finger, self._last_release = f, t
                if self._taps >= 2:
                    self._taps = 0
                    ev.append(("double", f))
            else:                                               # held, no drag
                self._taps = 0
        else:                                                   # still pinched
            if not self.dragging and t - self._close_t >= self.HOLD:
                self.dragging, self._start_y = True, y
                self._taps = 0
                ev.append(("drag_start", f))
            if self.dragging:
                ev.append(("drag", f, self._start_y - y))
        return ev


@dataclass
class GestureOptions:
    config: str = "bulbs.json"
    camera: int = 0
    preview: bool = True
    swap_hands: bool = False
    bulb_rate: int = 12
    debug: bool = False
    record: str | None = None     # JSONL file of per-frame measurements


@dataclass
class _State:
    hsv: list[list[float]]
    on: list[bool]
    selected: int = 0
    color_idx: int = 0
    last_apply: list[float] = field(default_factory=list)


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class GestureController:
    """Holds bulb state and turns gesture events into bulb commands."""

    MIN_INTERVAL = 0.09  # s between continuous (brightness) sends per bulb

    def __init__(self, bulbs: list[Bulb]) -> None:
        self.bulbs = bulbs
        n = len(bulbs)
        self.s = _State(hsv=[[0.0, 0.0, 1.0] for _ in range(n)],
                        on=[True] * n, last_apply=[0.0] * n)

    # -- low level ---------------------------------------------------------- #
    def _send(self, i: int) -> None:
        h, sat, v = self.s.hsv[i]
        _bg(self.bulbs[i].apply_hsv, h, sat, v)

    def _send_rate_limited(self, i: int) -> None:
        now = time.time()
        if now - self.s.last_apply[i] >= self.MIN_INTERVAL:
            self.s.last_apply[i] = now
            self._send(i)

    # -- events ------------------------------------------------------------- #
    def set_selected_brightness(self, v: float) -> None:
        i = self.s.selected
        self.s.hsv[i][2] = max(0.05, min(1.0, v))
        if self.s.on[i]:
            self._send_rate_limited(i)

    def set_all_brightness(self, v: float) -> None:
        v = max(0.05, min(1.0, v))
        for i in range(len(self.bulbs)):
            self.s.hsv[i][2] = v
            if self.s.on[i]:
                self._send_rate_limited(i)

    def brightness(self, index: int | None) -> float:
        """Current brightness of one bulb, or the average of all."""
        if index is None:
            return sum(h[2] for h in self.s.hsv) / len(self.s.hsv)
        return self.s.hsv[index][2]

    def switch_selected(self, direction: int) -> int:
        self.s.selected = (self.s.selected + direction) % len(self.bulbs)
        return self.s.selected

    def cycle_color(self, direction: int) -> str:
        self.s.color_idx = (self.s.color_idx + direction) % len(COLORS)
        label, (h, sat) = COLORS[self.s.color_idx]
        i = self.s.selected
        self.s.hsv[i][0], self.s.hsv[i][1] = h, sat
        if self.s.on[i]:
            self._send(i)
        return label

    def power(self, index: int | None, on: bool) -> None:
        targets = range(len(self.bulbs)) if index is None else [index]
        for i in targets:
            self.s.on[i] = on
            if on:
                self._send(i)
            else:
                _bg(self.bulbs[i].off)

    def commit(self, index: int | None) -> None:
        """Send the current state now, bypassing the rate limit (end of a drag)."""
        targets = range(len(self.bulbs)) if index is None else [index]
        for i in targets:
            if self.s.on[i]:
                self._send(i)

    def toggle(self, index: int | None) -> bool:
        """Toggle one bulb, or all (all -> off if any on, else all on). Returns new state."""
        if index is None:
            new = not all(self.s.on)
            self.power(None, new)
            return new
        new = not self.s.on[index]
        self.power(index, new)
        return new


MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
MODEL_PATH = Path.home() / ".cache" / "ambibulb" / "hand_landmarker.task"


def ensure_model(path: Path = MODEL_PATH) -> Path | None:
    """Download the MediaPipe hand model once (~7.5 MB). Uses curl when present
    (resumable, and avoids python.org Python's missing-CA-cert issue)."""
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading hand model to {path} ...")
    if shutil.which("curl"):
        rc = subprocess.call(["curl", "-fsSL", "--retry", "5", "-C", "-",
                              "-o", str(path), MODEL_URL])
        if rc == 0 and path.exists():
            return path
    try:
        import urllib.request
        urllib.request.urlretrieve(MODEL_URL, path)
        return path
    except Exception as exc:
        log.error("could not download the hand model (%s). Download it "
                  "manually from %s to %s", exc, MODEL_URL, path)
        return None


def _draw_hand(cv2, frame, lm, connections) -> None:
    """Draw landmarks + bones on the preview frame."""
    h, w = frame.shape[:2]
    pts = [(int(x * w), int(y * h)) for x, y, _ in lm]
    for c in connections:
        cv2.line(frame, pts[c.start], pts[c.end], (224, 224, 224), 2)
    for p in pts:
        cv2.circle(frame, p, 3, (0, 0, 255), -1)


def run(opts: GestureOptions) -> int:
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
    except ImportError:
        log.error('gesture control needs extra deps:  pip install "ambibulb[gesture]"')
        return 2
    model = ensure_model()
    if model is None:
        return 2

    ctrl = GestureController([Bulb(c, BulbOptions(rate=opts.bulb_rate))
                              for c in load_bulbs(opts.config)])
    cap = cv2.VideoCapture(opts.camera)
    if not cap.isOpened():
        log.error("could not open camera %d — grant Camera permission in "
                  "System Settings > Privacy & Security.", opts.camera)
        return 2

    hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    ))
    connections = vision.HandLandmarksConnections.HAND_CONNECTIONS
    t0 = time.monotonic()
    last_logged = ""
    swipe = {"Right": SwipeDetector(), "Left": SwipeDetector()}
    pinch = {"Right": PinchGesture(), "Left": PinchGesture()}
    labeler = HandLabeler(swap=opts.swap_hands)
    smoother = HandSmoother()
    drag_base: dict[str, float] = {}
    last_sent = -1.0
    next_debug = 0.0
    DRAG_GAIN = 2.0   # moving the hand half the screen height = full range
    rec = open(opts.record, "a", buffering=1) if opts.record else None  # line-buffered

    print("Gesture control running.  (q to quit)")
    print("  pinch thumb+index : double-tap = toggle bulb,  pinch-hold + move up/down = brightness")
    print("  pinch thumb+middle: double-tap = toggle ALL,   pinch-hold + move up/down = ALL brightness")
    print("  RIGHT open hand flick = switch bulb    LEFT open hand flick = color")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ts = int((time.monotonic() - t0) * 1000)
            res = hands.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
            now = time.time()
            idle_status = f"bulb {ctrl.s.selected + 1}/{len(ctrl.bulbs)}  {COLORS[ctrl.s.color_idx][0]}"
            status = idle_status

            detected = [(h_cls[0].category_name, [(p.x, p.y, p.z) for p in h_lm])
                        for h_lm, h_cls in zip(res.hand_landmarks or [],
                                               res.handedness or [])]
            hands_lm = smoother.update(labeler(detected, now), now)
            if opts.preview:
                for label, lm in hands_lm.items():
                    _draw_hand(cv2, frame, lm, connections)
                    hgt, wid = frame.shape[:2]
                    cv2.putText(frame, label.upper(),
                                (int(lm[0][0] * wid) - 30, int(lm[0][1] * hgt) + 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 200, 0), 2)

            debug_parts = []
            bar = None   # (label, level) to draw while dragging
            frame_rec = {}
            for label, lm in hands_lm.items():
                st = finger_states(lm)
                ri, rm = pinch_ratio(lm, 8), pinch_ratio(lm, 12)
                ext = "".join(c if s else "." for c, s in zip("TIMRP", st))
                g = pinch[label]
                frame_rec[label] = {"ri": round(ri, 3), "rm": round(rm, 3),
                                    "x": round(hand_x(lm), 3),
                                    "y": round(lm[9][1], 3), "ext": ext}

                for ev in g.update(ri, rm, lm[9][1], now):
                    kind, f = ev[0], ev[1]
                    target = ctrl.s.selected if f == "index" else None
                    name = f"bulb {ctrl.s.selected + 1}" if target is not None else "ALL"
                    if kind == "double":
                        on = ctrl.toggle(target)
                        status = f"{name} {'ON' if on else 'OFF'}"
                    elif kind == "drag_start":
                        drag_base[label] = ctrl.brightness(target)
                        last_sent = -1.0
                        status = f"{name} dimmer: move hand up/down"
                    elif kind == "drag":
                        v = max(0.05, min(1.0, drag_base[label] + DRAG_GAIN * ev[2]))
                        if abs(v - last_sent) >= 0.02:
                            last_sent = v
                            if target is not None:
                                ctrl.set_selected_brightness(v)
                            else:
                                ctrl.set_all_brightness(v)
                            status = f"{name} bright {int(v * 100)}%"
                        bar = (name, v)
                    elif kind == "drag_end":
                        ctrl.commit(target)   # make sure the final value lands
                        status = f"{name} bright set {int(ctrl.brightness(target) * 100)}%"

                # Swipe: position tracked every frame (blur can't reset it);
                # fires only with the hand open-ish and not pinching.
                d = swipe[label].update(hand_x(lm), now)
                if d and not g.closed and sum(st[1:]) >= 2:
                    step = 1 if d == "right" else -1
                    if label == "Right":
                        ctrl.switch_selected(step)
                        status = f"switched -> bulb {ctrl.s.selected + 1}"
                    else:
                        status = f"color -> {ctrl.cycle_color(step)}"

                mode = (f"drag-{g.finger}" if g.dragging
                        else f"pinch-{g.finger}" if g.closed else "free")
                debug_parts.append(f"{label[0]} {ext} ri={ri:.2f} rm={rm:.2f} "
                                   f"x={hand_x(lm):.2f} {mode}")

            if rec is not None:
                rec.write(json.dumps({"t": round(now, 3), "hands": frame_rec,
                                      "event": status if status != idle_status else None})
                          + "\n")

            if opts.preview and bar is not None:
                hgt, wid = frame.shape[:2]
                x0, y0, y1 = wid - 50, int(hgt * 0.15), int(hgt * 0.85)
                cv2.rectangle(frame, (x0, y0), (x0 + 24, y1), (80, 80, 80), 2)
                fill = int(y1 - (y1 - y0) * bar[1])
                cv2.rectangle(frame, (x0 + 3, fill), (x0 + 21, y1 - 3), (0, 220, 255), -1)
                cv2.putText(frame, f"{int(bar[1] * 100)}%", (x0 - 20, y0 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)

            if opts.debug and now >= next_debug:
                next_debug = now + 0.25
                print("  dbg " + (" | ".join(debug_parts) or "no hands"), flush=True)

            if status != idle_status and status != last_logged:
                print(f"  {status}", flush=True)
            last_logged = status if status != idle_status else last_logged

            if opts.preview:
                cv2.putText(frame, status, (10, 44), cv2.FONT_HERSHEY_SIMPLEX,
                            0.9, (0, 255, 0), 2)
                for k, part in enumerate(debug_parts):
                    cv2.putText(frame, part, (10, 80 + 28 * k),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)
                cv2.imshow("ambibulb gesture (q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if opts.preview:
            cv2.destroyAllWindows()
        hands.close()
        if rec is not None:
            rec.close()
        for b in ctrl.bulbs:
            b.stop()
    return 0

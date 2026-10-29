"""Hand-gesture control of the bulbs via the webcam (MediaPipe + OpenCV).

Optional feature. Install the extra deps with:

    pip install "ambibulb[gesture]"

Gesture scheme (a small preview window shows the live state):

  Right thumb+index  hold & spread                  -> brightness of SELECTED bulb
  Right thumb+index  double-tap                      -> toggle SELECTED bulb on/off
  Right thumb+middle hold & spread                  -> brightness of ALL bulbs
  Right thumb+middle double-tap                      -> toggle ALL bulbs on/off
  Right open-palm swipe left/right                  -> switch which bulb is selected
  Left  open-palm swipe left/right                  -> cycle color (warm/cool/R/G/B/white)

A quick double-tap (fingers touch twice fast) toggles power; a held, spread
pinch sets brightness — the two don't collide. macOS asks for Camera permission
on first run. Handedness can read mirrored on some cameras — use --swap-hands if
left/right feel reversed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

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
    """(thumb, index, middle, ring, pinky) extended? from 21 landmarks."""
    if not lm or len(lm) < 21:
        return (False, False, False, False, False)
    four = [lm[t][1] < lm[p][1] - 0.02 for t, p in zip(_TIPS, _PIPS)]
    thumb = _dist(lm[4], lm[5]) > _dist(lm[3], lm[5]) * 1.3
    return (thumb, four[0], four[1], four[2], four[3])


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


class SwipeDetector:
    """Detects a fast horizontal swipe from a stream of (x, t) samples."""

    def __init__(self, min_dx: float = 0.22, window: float = 0.5,
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


class TapDetector:
    """Detects a double-tap from a stream of (distance, t): two quick 'closes'
    (distance dips below `close` then rises above `open_`) within `max_gap`."""

    def __init__(self, close: float = 0.05, open_: float = 0.09,
                 max_gap: float = 0.6) -> None:
        self.close = close
        self.open = open_
        self.max_gap = max_gap
        self._closed = False
        self._last_tap = -1.0
        self._taps = 0

    def update(self, distance: float, t: float) -> bool:
        if not self._closed and distance < self.close:
            self._closed = True
            self._taps = self._taps + 1 if (t - self._last_tap) <= self.max_gap else 1
            self._last_tap = t
            if self._taps >= 2:
                self._taps = 0
                return True
        elif self._closed and distance > self.open:
            self._closed = False
        return False


@dataclass
class GestureOptions:
    config: str = "bulbs.json"
    camera: int = 0
    preview: bool = True
    swap_hands: bool = False
    bulb_rate: int = 12


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

    def toggle(self, index: int | None) -> bool:
        """Toggle one bulb, or all (all -> off if any on, else all on). Returns new state."""
        if index is None:
            new = not all(self.s.on)
            self.power(None, new)
            return new
        new = not self.s.on[index]
        self.power(index, new)
        return new


def run(opts: GestureOptions) -> int:
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        log.error('gesture control needs extra deps:  pip install "ambibulb[gesture]"')
        return 2
    if not (hasattr(mp, "solutions") and hasattr(mp.solutions, "hands")):
        log.error("this mediapipe build has no solutions.hands API.")
        return 2

    ctrl = GestureController([Bulb(c, BulbOptions(rate=opts.bulb_rate))
                              for c in load_bulbs(opts.config)])
    cap = cv2.VideoCapture(opts.camera)
    if not cap.isOpened():
        log.error("could not open camera %d — grant Camera permission in "
                  "System Settings > Privacy & Security.", opts.camera)
        return 2

    hands = mp.solutions.hands.Hands(max_num_hands=2,
                                     min_detection_confidence=0.7,
                                     min_tracking_confidence=0.5)
    draw = mp.solutions.drawing_utils
    swipe = {"Right": SwipeDetector(), "Left": SwipeDetector()}
    tap_index = TapDetector()      # thumb+index double-tap -> toggle selected
    tap_middle = TapDetector()     # thumb+middle double-tap -> toggle all
    suppress_brightness_until = 0.0

    def handed(label: str) -> str:
        if opts.swap_hands:
            return "Left" if label == "Right" else "Right"
        return label

    print("Gesture control running.  (q to quit)")
    print("  R thumb+index : hold/spread = brightness,  double-tap = toggle bulb")
    print("  R thumb+middle: hold/spread = ALL brightness, double-tap = toggle ALL")
    print("  R open-palm swipe = switch bulb      L open-palm swipe = color")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            now = time.time()
            status = f"bulb {ctrl.s.selected + 1}/{len(ctrl.bulbs)}  {COLORS[ctrl.s.color_idx][0]}"

            hands_lm = {}
            if res.multi_hand_landmarks and res.multi_handedness:
                for h_lm, h_cls in zip(res.multi_hand_landmarks, res.multi_handedness):
                    lm = [(p.x, p.y, p.z) for p in h_lm.landmark]
                    label = handed(h_cls.classification[0].label)
                    hands_lm[label] = lm
                    if opts.preview:
                        draw.draw_landmarks(frame, h_lm,
                                            mp.solutions.hands.HAND_CONNECTIONS)

            for label, lm in hands_lm.items():
                st = finger_states(lm)
                if label == "Right":
                    di = pinch_distance(lm)         # thumb-index gap
                    dm = _dist(lm[4], lm[12])        # thumb-middle gap
                    if is_pinch_index_pose(st) and tap_index.update(di, now):
                        on = ctrl.toggle(ctrl.s.selected)
                        suppress_brightness_until = now + 0.6
                        status = f"bulb {ctrl.s.selected+1} {'ON' if on else 'OFF'}"
                    elif is_pinch_middle_pose(st) and tap_middle.update(dm, now):
                        on = ctrl.toggle(None)
                        suppress_brightness_until = now + 0.6
                        status = f"ALL {'ON' if on else 'OFF'}"
                    elif is_pinch_index_pose(st):
                        swipe["Right"].reset()
                        if now >= suppress_brightness_until:
                            v = (di - 0.03) / 0.22
                            ctrl.set_selected_brightness(v)
                            status = f"bulb {ctrl.s.selected+1} bright {int(max(0.05,min(1,v))*100)}%"
                    elif is_pinch_middle_pose(st):
                        swipe["Right"].reset()
                        if now >= suppress_brightness_until:
                            v = (dm - 0.03) / 0.22
                            ctrl.set_all_brightness(v)
                            status = f"ALL bright {int(max(0.05,min(1,v))*100)}%"
                    elif is_open_palm(st):
                        d = swipe["Right"].update(hand_x(lm), now)
                        if d:
                            ctrl.switch_selected(1 if d == "right" else -1)
                            status = f"switched -> bulb {ctrl.s.selected+1}"
                else:  # Left hand -> color cycle
                    if is_open_palm(st):
                        d = swipe["Left"].update(hand_x(lm), now)
                        if d:
                            c = ctrl.cycle_color(1 if d == "right" else -1)
                            status = f"color -> {c}"

            if opts.preview:
                cv2.putText(frame, status, (10, 44), cv2.FONT_HERSHEY_SIMPLEX,
                            0.9, (0, 255, 0), 2)
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
        for b in ctrl.bulbs:
            b.stop()
    return 0

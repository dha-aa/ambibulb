"""Hand-gesture control of the bulbs via the webcam (MediaPipe + OpenCV).

Optional feature. Install the extra deps with:

    pip install "ambibulb[gesture]"

Shows a small webcam preview with the detected finger count; raise a number of
fingers to trigger an action. macOS asks for Camera permission on first run.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .bulb import Bulb, BulbOptions
from .config import load_bulbs

log = logging.getLogger("ambibulb.gesture")

# MediaPipe hand landmark indices (21 points per hand).
_TIPS = [8, 12, 16, 20]   # index, middle, ring, pinky fingertips
_PIPS = [6, 10, 14, 18]   # their PIP joints

RGB = tuple[int, int, int]


def count_fingers(lm: list[tuple[float, float, float]]) -> int:
    """Count extended fingers from 21 normalized (x, y, z) hand landmarks.

    Image y grows downward, so a finger is 'up' when its tip sits above its PIP
    joint. The thumb is counted as extended when its tip is clearly farther from
    the index-finger base than its own IP joint (orientation-independent).
    """
    if not lm or len(lm) < 21:
        return 0
    up = 0
    for tip, pip in zip(_TIPS, _PIPS):
        if lm[tip][1] < lm[pip][1] - 0.02:
            up += 1

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    if dist(lm[4], lm[5]) > dist(lm[3], lm[5]) * 1.3:
        up += 1
    return up


#: finger count -> (label, kind, value)
ACTIONS: dict[int, tuple[str, str, object]] = {
    0: ("off", "off", None),
    1: ("red", "color", (255, 0, 0)),
    2: ("green", "color", (0, 255, 0)),
    3: ("blue", "color", (0, 0, 255)),
    4: ("warm white", "white", (100, 0)),    # (brightness%, temp%)
    5: ("on / white", "white", (100, 50)),
}


def _apply(bulbs: list[Bulb], kind: str, value) -> None:
    """Fire the action at every bulb in parallel (non-blocking)."""
    def work(b: Bulb):
        if kind == "off":
            b.off()
        elif kind == "color":
            b.apply_color(value)
        elif kind == "white":
            b.apply_white(value[0], value[1])
    for b in bulbs:
        threading.Thread(target=work, args=(b,), daemon=True).start()


@dataclass(frozen=True)
class GestureOptions:
    config: str = "bulbs.json"
    camera: int = 0
    hold: int = 6          # frames a gesture must persist before it fires
    preview: bool = True
    bulb_rate: int = 12


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

    bulbs = [Bulb(c, BulbOptions(rate=opts.bulb_rate))
             for c in load_bulbs(opts.config)]
    cap = cv2.VideoCapture(opts.camera)
    if not cap.isOpened():
        log.error("could not open camera %d — grant Camera permission in "
                  "System Settings > Privacy & Security.", opts.camera)
        return 2

    hands = mp.solutions.hands.Hands(max_num_hands=1,
                                     min_detection_confidence=0.7,
                                     min_tracking_confidence=0.5)
    draw = mp.solutions.drawing_utils

    last_fired: int | None = None
    candidate: int | None = None
    streak = 0

    print("Gesture control running. Show your hand to the camera.")
    print("  0=off  1=red  2=green  3=blue  4=warm white  5=on/white   (q to quit)")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)  # mirror, so it feels natural
            res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

            fingers = None
            if res.multi_hand_landmarks:
                hand = res.multi_hand_landmarks[0]
                lm = [(p.x, p.y, p.z) for p in hand.landmark]
                fingers = count_fingers(lm)
                if opts.preview:
                    draw.draw_landmarks(frame, hand,
                                        mp.solutions.hands.HAND_CONNECTIONS)

            # Debounce: require `hold` identical readings, fire once on change.
            if fingers is not None and fingers in ACTIONS:
                if fingers == candidate:
                    streak += 1
                else:
                    candidate, streak = fingers, 1
                if streak == opts.hold and candidate != last_fired:
                    label, kind, value = ACTIONS[candidate]
                    print(f"  -> {candidate}: {label}")
                    _apply(bulbs, kind, value)
                    last_fired = candidate

            if opts.preview:
                label = ACTIONS[fingers][0] if fingers in ACTIONS else "no hand"
                shown = fingers if fingers is not None else "-"
                cv2.putText(frame, f"{shown}  {label}", (10, 44),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)
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
        for b in bulbs:
            b.stop()
    return 0

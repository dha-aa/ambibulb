"""Unit tests for the pure logic (no bulbs, no network)."""

import json

import pytest

from ambibulb.color import (
    ColorOptions,
    HueStabilizer,
    average_color,
    p3_to_srgb,
    shape_hsv,
    trim_letterbox,
    zone_colors,
)

RED = (230, 20, 20)
GREEN = (20, 230, 20)
from ambibulb.config import ConfigError, load_bulbs

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


# -- color shaping ---------------------------------------------------------- #
def test_shape_hsv_hue_preserved_and_fixed_brightness():
    h, s, v = shape_hsv((255, 0, 0), ColorOptions())
    assert h == pytest.approx(0.0)
    assert s == pytest.approx(1.0)
    assert v == pytest.approx(1.0)  # fixed bright by default


def test_shape_hsv_dark_color_still_full_brightness():
    # Dark navy: hue preserved, brightness lifted to fixed (accuracy fix).
    h, s, v = shape_hsv((10, 20, 60), ColorOptions())
    assert 0.6 < h < 0.72          # blue-ish hue
    assert v == 1.0


def test_shape_hsv_follow_brightness_floor():
    h, s, v = shape_hsv((2, 2, 2), ColorOptions(follow_brightness=True,
                                                brightness_floor=15))
    assert v == pytest.approx(0.15)


def test_shape_hsv_saturation_scale():
    _, s_lo, _ = shape_hsv((200, 100, 100), ColorOptions(saturation=0.5))
    _, s_hi, _ = shape_hsv((200, 100, 100), ColorOptions(saturation=1.0))
    assert s_lo < s_hi


# -- extraction ------------------------------------------------------------- #
def test_average_color_pure_python():
    px = [(0, 0, 255, 255), (0, 255, 0, 255), (255, 0, 0, 255), (255, 255, 255, 255)]
    buf = bytes(b for p in px for b in p)
    assert average_color(buf, 2, 2, step=1) == (127, 127, 127)


def _frame_with_bars():
    img = Image.new("RGB", (320, 180), (0, 0, 0))
    for x in range(320):
        for y in range(40, 140):
            col = (255, 0, 0) if x < 107 else (0, 255, 0) if x < 213 else (0, 0, 255)
            img.putpixel((x, y), col)
    return img


def test_trim_letterbox_removes_bars():
    trimmed = trim_letterbox(_frame_with_bars())
    assert trimmed.size[1] < 140  # the 40px black bars are gone


def test_zone_colors_left_center_right():
    zc = zone_colors(trim_letterbox(_frame_with_bars()), 3, method="vibrance")
    (lr, lg, lb), (cr, cg, cb), (rr, rg, rb) = zc
    assert lr > 200 and lg < 60 and lb < 60      # left ~red
    assert cg > 200 and cr < 60 and cb < 60      # center ~green
    assert rb > 200 and rr < 60 and rg < 60      # right ~blue


def test_vivid_locks_onto_subject_not_grey():
    # Anime-style frame: mostly dark, with a small vivid cyan subject.
    img = Image.new("RGB", (320, 180), (12, 12, 14))
    for x in range(130, 190):
        for y in range(70, 110):
            img.putpixel((x, y), (0, 230, 230))      # cyan subject
    avg = zone_colors(img, 1, method="average")[0]
    vivid = zone_colors(img, 1, method="vivid")[0]
    # Plain average stays dark/grey; vivid locks onto the cyan.
    assert max(avg) < 60
    assert vivid[1] > 140 and vivid[2] > 140 and vivid[0] < 90


def test_vivid_greyscale_falls_back():
    img = Image.new("RGB", (160, 90), (100, 100, 100))
    assert zone_colors(img, 1, method="vivid")[0] == (100, 100, 100)


# -- P3 -> sRGB ------------------------------------------------------------- #
def test_p3_neutral_axis_unchanged():
    # Grey/white sit on the neutral axis -> conversion must leave them intact.
    for v in (0, 64, 128, 200, 255):
        r, g, b = p3_to_srgb((v, v, v))
        assert abs(r - v) <= 1 and abs(g - v) <= 1 and abs(b - v) <= 1


def test_p3_saturated_green_pulls_in_gamut():
    # P3 green is outside sRGB; converting should reduce its green dominance
    # or bleed into other channels (never produce out-of-range values).
    r, g, b = p3_to_srgb((0, 255, 0))
    assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255


def test_p3_gamut_flag_changes_midtone():
    img = Image.new("RGB", (160, 90), (255, 128, 0))  # vivid orange
    raw = zone_colors(img, 1, method="average", gamut="none")[0]
    p3 = zone_colors(img, 1, method="average", gamut="p3")[0]
    assert raw != p3


# -- hue stability ---------------------------------------------------------- #
def _hue_of(rgb):
    import colorsys
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]


def test_hue_stabilizer_ignores_single_blip():
    s = HueStabilizer(hold=3)
    for _ in range(4):
        s.update(RED)
    out = s.update(GREEN)          # one green frame in a sea of red
    assert _hue_of(out) == pytest.approx(_hue_of(RED), abs=0.05)


def test_hue_stabilizer_adopts_sustained_change():
    s = HueStabilizer(hold=3)
    for _ in range(4):
        s.update(RED)
    for _ in range(3):             # green holds for `hold` frames
        out = s.update(GREEN)
    assert _hue_of(out) == pytest.approx(_hue_of(GREEN), abs=0.05)


def test_hue_stabilizer_passes_brightness_through():
    s = HueStabilizer(hold=3)
    s.update((230, 20, 20))
    out = s.update((120, 10, 10))  # same hue, dimmer
    assert max(out) < 160          # value tracked down immediately


# -- gesture finger counting ------------------------------------------------ #
def _hand(fingers_up: bool, thumb_out: bool):
    """Build 21 synthetic landmarks for an upright hand (y grows downward)."""
    lm = [(0.5, 0.9, 0.0)] * 21          # default near wrist height
    lm[0] = (0.5, 0.95, 0.0)             # wrist
    tip_y, pip_y = (0.2, 0.5) if fingers_up else (0.6, 0.5)  # up: tip above pip
    for mcp, pip, dip, tip in [(5, 6, 7, 8), (9, 10, 11, 12),
                               (13, 14, 15, 16), (17, 18, 19, 20)]:
        lm[mcp] = (0.4, 0.55, 0.0)
        lm[pip] = (0.4, pip_y, 0.0)
        lm[tip] = (0.4, tip_y, 0.0)
    # thumb: landmarks 2 (mcp), 3 (ip), 4 (tip); index base is 5 at x=0.4
    lm[2] = (0.45, 0.6, 0.0)
    if thumb_out:
        lm[3] = (0.40, 0.58, 0.0)
        lm[4] = (0.15, 0.55, 0.0)        # tip far from index base (5)
    else:
        lm[3] = (0.42, 0.58, 0.0)
        lm[4] = (0.43, 0.57, 0.0)        # tip tucked near index base
    return lm


def test_count_fingers_fist():
    from ambibulb.gesture import count_fingers
    assert count_fingers(_hand(fingers_up=False, thumb_out=False)) == 0


def test_count_fingers_open_palm():
    from ambibulb.gesture import count_fingers
    assert count_fingers(_hand(fingers_up=True, thumb_out=True)) == 5


def test_is_open_palm_and_fist():
    from ambibulb.gesture import finger_states, is_open_palm, is_fist
    assert is_open_palm(finger_states(_hand(True, True)))
    assert is_fist(finger_states(_hand(False, False)))


def test_swipe_detector_direction():
    from ambibulb.gesture import SwipeDetector
    s = SwipeDetector(min_dx=0.2, window=1.0)
    assert s.update(0.2, 0.0) is None
    assert s.update(0.6, 0.1) == "right"      # moved +0.4 fast
    s2 = SwipeDetector(min_dx=0.2, window=1.0)
    s2.update(0.8, 0.0)
    assert s2.update(0.3, 0.1) == "left"       # moved -0.5


def test_finger_states_rotation_invariant():
    from ambibulb.gesture import finger_states
    up = _hand(True, True)
    # rotate the whole hand 90 degrees around the wrist: pointing sideways
    wx, wy, _ = up[0]
    sideways = [(wx + (y - wy), wy - (x - wx), z) for x, y, z in up]
    assert finger_states(sideways)[1:] == finger_states(up)[1:] == (True,) * 4


# -- config ----------------------------------------------------------------- #
def test_temp_percent_mapping():
    from ambibulb.cli import TEMP_PERCENT
    assert TEMP_PERCENT["warm"] == 0        # warmest
    assert TEMP_PERCENT["cool"] == 100      # coolest
    assert TEMP_PERCENT["warm"] < TEMP_PERCENT["neutral"] < TEMP_PERCENT["cool"]


def test_load_bulbs_ok(tmp_path):
    p = tmp_path / "bulbs.json"
    p.write_text(json.dumps({"bulbs": [
        {"name": "A", "id": "x", "key": "k", "ip": "Auto", "version": 3.5}]}))
    bulbs = load_bulbs(p)
    assert len(bulbs) == 1 and bulbs[0].version == 3.5


def test_load_bulbs_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_bulbs(tmp_path / "nope.json")


def test_load_bulbs_missing_key(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"bulbs": [{"name": "A", "id": "x"}]}))
    with pytest.raises(ConfigError):
        load_bulbs(p)


def test_load_bulbs_duplicate_id(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"bulbs": [
        {"id": "x", "key": "k"}, {"id": "x", "key": "k2"}]}))
    with pytest.raises(ConfigError):
        load_bulbs(p)


def test_gesture_cli_args_match_options(monkeypatch):
    # cmd_gesture must build GestureOptions from parsed args without TypeError.
    import ambibulb.gesture as g
    from ambibulb import cli
    seen = {}
    monkeypatch.setattr(g, "run", lambda opts: seen.setdefault("opts", opts) and 0)
    a = cli.build_parser().parse_args(["gesture", "--swap-hands", "--no-preview"])
    cli.resolve_preset(a)
    cli.cmd_gesture(a)
    assert seen["opts"].swap_hands is True and seen["opts"].preview is False


def _at_x(x):
    # 21 landmarks with the middle-finger base (index 9) at screen x
    return [(x, 0.5, 0.0)] * 21


def test_assign_hands_two_hands_by_screen_position():
    from ambibulb.gesture import assign_hands
    # MediaPipe labels are wrong here on purpose; position must win.
    out = assign_hands([("Right", _at_x(0.25)), ("Left", _at_x(0.75))])
    assert out["Right"][9][0] == 0.75 and out["Left"][9][0] == 0.25


def test_assign_hands_single_hand_inverts_mediapipe_label():
    from ambibulb.gesture import assign_hands
    assert list(assign_hands([("Left", _at_x(0.7))])) == ["Right"]
    assert list(assign_hands([("Right", _at_x(0.3))])) == ["Left"]


def test_assign_hands_swap_flag():
    from ambibulb.gesture import assign_hands
    assert list(assign_hands([("Left", _at_x(0.7))], swap=True)) == ["Left"]


def _run_pinch(seq, fps=30):
    """Feed (ri, rm, y, seconds) segments to a PinchGesture; collect events."""
    from ambibulb.gesture import PinchGesture
    g, t, evs = PinchGesture(), 0.0, []
    for ri, rm, y, dur in seq:
        for _ in range(max(1, int(dur * fps))):
            evs += g.update(ri, rm, y, t)
            t += 1 / fps
    return evs


def test_pinch_double_tap():
    evs = _run_pinch([(1.0, 1.2, .5, .3), (.1, .9, .5, .12), (1.0, 1.2, .5, .15),
                      (.1, .9, .5, .12), (1.0, 1.2, .5, .3)])
    assert [e for e in evs if e[0] == "double"] == [("double",)]


def test_pinch_double_tap_counts_even_if_finger_reading_flips():
    # Recorded behaviour: fingers bunch up, so tap 1 reads as index and tap 2
    # as middle. It must still be one double-tap.
    evs = _run_pinch([(1.0, 1.0, .5, .3), (.1, .9, .5, .2), (1.0, 1.0, .5, .2),
                      (.9, .1, .5, .2), (1.0, 1.0, .5, .3)])
    assert [e for e in evs if e[0] == "double"] == [("double",)]


def test_pinch_one_frame_jitter_does_not_split_a_hold():
    # a held pinch with a single noisy frame above OPEN stays one drag
    evs = _run_pinch([(1.0, 1.2, .5, .3), (.1, .9, .5, .6), (.6, .9, .5, 1 / 30),
                      (.1, .9, .4, .5), (1.0, 1.2, .4, .3)])
    kinds = [e[0] for e in evs]
    assert kinds.count("drag_start") == 1 and kinds.count("drag_end") == 1


def test_pinch_hold_becomes_drag_not_tap():
    evs = _run_pinch([(1.0, 1.2, .50, .3), (.1, .9, .50, .6),   # hold -> drag
                      (.1, .9, .30, .3),                        # move hand up
                      (1.0, 1.2, .30, .2)])                     # release
    kinds = [e[0] for e in evs]
    assert "double" not in kinds
    assert kinds.count("drag_start") == 1 and kinds.count("drag_end") == 1
    drags = [e[1] for e in evs if e[0] == "drag"]
    assert drags[-1] > 0.15            # moved up by ~0.2 screen units


def test_pinch_slow_taps_do_not_double():
    evs = _run_pinch([(1.0, 1.2, .5, .3), (.1, .9, .5, .12), (1.0, 1.2, .5, 1.5),
                      (.1, .9, .5, .12), (1.0, 1.2, .5, .3)])
    assert not [e for e in evs if e[0] == "double"]


def test_one_euro_smooths_jitter_but_follows_motion():
    from ambibulb.gesture import OneEuro
    f, t, out = OneEuro(), 0.0, []
    for i in range(60):                      # still hand with +-0.01 jitter
        out.append(f(0.5 + (0.01 if i % 2 else -0.01), t)); t += 1 / 30
    assert max(out[20:]) - min(out[20:]) < 0.012     # jitter reduced
    for _ in range(15):                      # then a fast move to 0.9
        v = f(0.9, t); t += 1 / 30
    assert v > 0.85                          # caught up within 0.5 s


def test_hand_smoother_bridges_brief_dropout():
    from ambibulb.gesture import HandSmoother
    s, lm = HandSmoother(hold=0.2), [(0.5, 0.5, 0.0)] * 21
    assert "Right" in s.update({"Right": lm}, 0.0)
    assert "Right" in s.update({}, 0.1)      # 100 ms gap: still there
    assert "Right" not in s.update({}, 0.5)  # long gap: gone


def test_hand_labeler_sticky_single_hand():
    from ambibulb.gesture import HandLabeler
    lab = HandLabeler()
    hand = [(0.7, 0.5, 0.0)] * 21
    assert list(lab([("Left", hand)], 0.0)) == ["Right"]   # inverted label
    # MediaPipe flips its label for one frame; the hand hasn't moved
    assert list(lab([("Right", hand)], 0.03)) == ["Right"]


def test_assign_hands_drops_duplicate_detection():
    from ambibulb.gesture import assign_hands
    def hand(x):
        lm = [(x, 0.5, 0.0)] * 21
        lm[0] = (x, 0.7, 0.0)            # wrist 0.2 below palm centre -> scale 0.2
        return lm
    # same hand reported twice, 0.002 apart -> one hand, not a Left+Right pair
    out = assign_hands([("Left", hand(0.540)), ("Right", hand(0.538))])
    assert len(out) == 1
    # genuinely two hands far apart -> both kept
    assert len(assign_hands([("Left", hand(0.3)), ("Right", hand(0.75))])) == 2


class _FakeBulb:
    def __init__(self):
        self.calls = []
    def apply_hsv(self, h, s, v):
        self.calls.append(("hsv", h, s, v))
    def off(self):
        self.calls.append(("off",))


def _ctrl(n=3):
    from ambibulb.gesture import GestureController
    return GestureController([_FakeBulb() for _ in range(n)])


def test_selection_starts_on_all_and_cycles():
    c = _ctrl()
    assert c.s.selected is None                       # ALL by default
    order = [c.switch_selected(1) for _ in range(4)]
    assert order == [0, 1, 2, None]                   # bulb1, bulb2, bulb3, ALL
    assert c.switch_selected(-1) == 2                 # backwards wraps


def test_actions_apply_to_selection():
    c = _ctrl()
    c.set_brightness(c.s.selected, 0.4)               # ALL
    assert [h[2] for h in c.s.hsv] == [0.4, 0.4, 0.4]
    c.s.selected = 1                                  # just bulb 2
    c.set_brightness(c.s.selected, 0.9)
    assert [h[2] for h in c.s.hsv] == [0.4, 0.9, 0.4]
    c.cycle_color(1)
    hues = [h[:2] for h in c.s.hsv]
    assert hues[1] != hues[0] and hues[0] == hues[2]  # only bulb 2 recoloured


def test_toggle_selection_and_all():
    c = _ctrl()
    assert c.toggle(None) is False and c.s.on == [False, False, False]
    assert c.toggle(0) is True and c.s.on == [True, False, False]
    assert c.toggle(None) is False                    # any on -> all off
    assert c.s.on == [False, False, False]
    assert c.name(None) == "ALL" and c.name(2) == "bulb 3"

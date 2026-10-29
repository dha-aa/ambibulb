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


def test_count_fingers_four_no_thumb():
    from ambibulb.gesture import count_fingers
    assert count_fingers(_hand(fingers_up=True, thumb_out=False)) == 4


def test_gesture_action_map_covers_0_to_5():
    from ambibulb.gesture import ACTIONS
    assert set(ACTIONS) == {0, 1, 2, 3, 4, 5}


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

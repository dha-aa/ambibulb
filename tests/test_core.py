"""Unit tests for the pure logic (no bulbs, no network)."""

import json

import pytest

from ambibulb.color import (
    ColorOptions,
    average_color,
    shape_hsv,
    trim_letterbox,
    zone_colors,
)
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


# -- config ----------------------------------------------------------------- #
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

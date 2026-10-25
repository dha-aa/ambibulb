# ambibulb

**Drive Wipro / Tuya smart bulbs from your screen color, live — all local.**

ambibulb is a small ambient-lighting engine ("Ambilight for smart bulbs"). It
samples your screen many times a second, extracts the color, and streams it to
one or more smart bulbs over your Wi-Fi. Control is **100% local** — no cloud is
contacted while it runs, so it works offline and keeps your screen private.

Built for Wipro Wi-Fi bulbs (they run Tuya firmware) and works with any Tuya
Wi-Fi color bulb.

---

## Features

- **Local LAN control** via [`tinytuya`](https://github.com/jasonacox/tinytuya) — no cloud at runtime.
- **Multiple bulbs**, each on its own worker thread — one slow bulb never stalls the others.
- **Video-aware sampling** — auto-crops letterbox bars, vibrance weighting, scene-cut snapping.
- **Anime / content presets** — one flag tunes extraction + reaction for animation, film, or games.
- **Zones** — split the picture left→right and map bulbs across it for an immersive wrap.
- **Accurate color** — drives HSV directly with decoupled brightness (dark scenes keep true hue).
- **Resilient** — auto-reconnect and IP re-discovery survive Wi-Fi drops and DHCP changes.

## How it works

```
screen grab → crop black bars → extract color (per zone) → ease / snap → shape to HSV → bulbs
   (mss)        (letterbox)       (average/vibrance/vivid)   (engine)      (color)    (tinytuya, LAN)
```

Each stage is an independent, testable module (see [Layout](#layout)).

## Install

```bash
pip install -e .            # from this directory (gives the `ambibulb` command)
# or just the runtime deps:
pip install mss pillow tinytuya
```

Requires Python 3.10+.

## Setup — one time (get your bulbs' local keys)

Tuya bulbs need a secret **local key** before they'll accept local commands.
You read it once, then never touch the cloud again.

1. Pair the bulbs in the **Wipro / Smart Life** app (you've likely done this).
2. Run the tinytuya wizard (needs a free Tuya IoT account linked to your app
   account — the only cloud step):
   ```bash
   python3 -m tinytuya wizard        # writes devices.json
   ```
3. Create your config from the example and fill in each bulb:
   ```bash
   cp bulbs.example.json bulbs.json
   ```
   Find each bulb's IP any time with:
   ```bash
   ambibulb discover
   ```

`bulbs.json` and `devices.json` hold secrets and are **git-ignored** — never commit them.

## Usage

```bash
ambibulb run                           # stream whole-screen color to all bulbs
ambibulb run --preset anime            # vivid + snappy, tuned for animation
ambibulb run --preset movie --zones 3  # live-action film, per-zone (L/C/R)
ambibulb once                          # push the current color once (quick test)
ambibulb discover                      # scan the LAN for Tuya devices
python3 -m ambibulb run ...            # same, without the console script
```

### Presets

`--preset` sets a whole tuning bundle. Any explicit flag still overrides it.

| Preset | Tuned for | Sets |
|---|---|---|
| `anime` | animation — flat vivid colors, fast cuts | `vivid` mode, high saturation, snappy, low cut, rate 15, fps 24 |
| `movie` | live-action film | `vibrance` mode, gentle smoothing, truer color |
| `game` | games | vivid + snappy, slightly calmer saturation |
| `default` | general desktop | plain average, calm |

### Options

| Option | Meaning |
|---|---|
| `--preset anime\|movie\|game` | tuning bundle (above) |
| `--mode average\|dominant\|vivid` | `dominant` = vibrance-weighted (video); `vivid` = dominant hue (anime) |
| `--zones N` | split picture into N zones, map bulbs left→right |
| `--saturation F` | `<1` softer, `>1` punchier |
| `--brightness N` | fixed bulb brightness % (default 100) |
| `--follow-brightness` | brightness tracks the scene (moodier) |
| `--bulb-rate N` | commands/sec per bulb (firmware tops out ~12–15) |
| `--smooth F` | easing toward the new color (lower = calmer) |
| `--cut N` | per-channel jump that snaps instantly (scene cut) |
| `--no-letterbox` | don't auto-crop black movie bars |
| `--seconds N` | run N seconds then stop (0 = until Ctrl-C) |

`--zones 3` assumes your bulbs are placed left / center / right; config order
(bulb #1, #2, #3) maps left→right. Otherwise use `--zones 1`.

### Preview without bulbs

```bash
ambibulb-follow --mode vivid       # on-screen panel that follows your screen color
```

## Layout

```
ambibulb/
  config.py     bulb config: load + validation
  color.py      extraction (average/vibrance/vivid) + HSV shaping — pure, tested
  capture.py    screen sampling (mss) → per-zone colors
  bulb.py       one bulb: connection + worker thread + reconnect/rediscover
  engine.py     the live loop — zones, smoothing, scene cuts
  discovery.py  LAN scan
  cli.py        command-line entry point (presets, signals, logging)
  follower.py   standalone preview panel
tests/          unit tests for the pure logic
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Notes & limits

- These are **Wi-Fi** bulbs — no usable BLE control channel. Local control needs
  a Wi-Fi network, but **not internet**: firewall the bulbs off the WAN and it
  still works.
- Tuya bulbs render ~12–15 color changes/sec and don't reproduce sRGB perfectly.
  Hue is accurate; exact shade-matching needs a colorimeter. For genuinely
  strobe-fast sync, use an addressable LED strip instead of bulbs.
- Lowest latency comes from a real router (not a phone hotspot) with the bulbs
  on a clean 2.4 GHz channel.

## License

[MIT](LICENSE)

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

## Quick start

```bash
git clone https://github.com/dha-aa/ambibulb.git && cd ambibulb
./setup.sh                          # installs deps; builds bulbs.json if possible
```

`setup.sh` automates everything it can. The **one manual step** — unavoidable,
for any tool — is reading your bulbs' **local keys** (needs a free Tuya account
linked to your Wipro/Smart Life app). `setup.sh` tells you when to run:

```bash
python3 -m tinytuya wizard          # writes devices.json (your keys)
./setup.sh                          # re-run: turns devices.json into bulbs.json
```

Then:

```bash
ambibulb run --preset anime
```

See [Setup](#setup--one-time-get-your-bulbs-local-keys) for the detailed
key-reading walkthrough, and [HomeKit & Siri](#homekit--siri) for voice control.

## Install (manual)

```bash
pip install -e .            # from this directory (gives the `ambibulb` command)
# or just the runtime deps:
pip install mss pillow tinytuya
```

Requires Python 3.10+.

## Setup — one time (get your bulbs' local keys)

Every Tuya/Wipro bulb has a secret **local key** that it checks before accepting
local commands. The key lives in the bulb and in Tuya's cloud — so you read it
out **once** through a free Tuya developer account, then never touch the cloud
again. After this, everything runs 100% on your LAN.

> **Why a cloud account at all?** The key isn't printed anywhere and isn't in
> the app's files without rooting your phone. The supported way to read it is
> the Tuya IoT portal, which the `tinytuya wizard` automates. It's ~10 minutes,
> once.

### Step 1 — Pair the bulbs in the app

Make sure all your bulbs are already added and working in the **Wipro** app (or
**Smart Life** — same Tuya platform). Note which app/account they're in.

### Step 2 — Create a free Tuya IoT cloud project

1. Go to **<https://iot.tuya.com>** and sign up / log in.
2. **Cloud → Development → Create Cloud Project.**
   - Name: anything.
   - **Data Center: pick the one for your region** — for Wipro in India, choose
     **India**. (Wrong data center is the #1 cause of "no devices found" later.)
   - Industry / Development Method: leave the defaults.
3. Open the project → **Authorization** tab. Copy and keep:
   - **Access ID / Client ID**
   - **Access Secret / Client Secret**
4. Open the **Service API** tab and make sure **IoT Core** and **Authorization**
   are in the list (they're enabled by default; if there's an "Authorize"
   button, click it).

### Step 3 — Link your app account (reuses your existing bulbs)

This imports the bulbs you already paired — you don't re-pair anything.

1. In the project: **Devices → Link App Account → Add App Account.** A **QR
   code** appears on screen.
2. On your phone, open the **Wipro app → Me / Profile → the scan icon (top
   corner)** and scan that QR. Your bulbs now show under **Devices**.

> **If the Wipro app's scan doesn't work** (some builds hide the scanner): open
> the **Smart Life** app instead, sign in **with the same phone number/email as
> your Wipro account**, and scan from **Smart Life → Me → scan**. Same Tuya
> account, same bulbs. Make sure the account you link is the one that actually
> owns the bulbs.

### Step 4 — Run the wizard to pull the keys

```bash
python3 -m tinytuya wizard
```

It asks for four things:

| Prompt | What to enter |
|---|---|
| **API Key** | your **Access ID** (from Step 2.3) |
| **API Secret** | your **Access Secret** |
| **Device ID** | *any one* of your bulb IDs (just text — **not** your phone; the wizard lists devices, copy one ID) |
| **Region** | your data center code — **`in`** for India (`us`, `eu`, `cn` otherwise) |

It writes **`devices.json`** containing the `id` + `local key` + version for
every bulb. If it reports **0 devices** or error **1106 / 28841105**, it's
almost always the wrong **region** or a different linked account — fix that and
re-run.

### Step 5 — Build your config

```bash
cp bulbs.example.json bulbs.json      # then fill in id / key / ip / version
```

The wizard's `devices.json` has the `id`, `key`, and `version`; get each bulb's
current **IP** with:

```bash
ambibulb discover
```

Match each bulb by `id`, drop its `ip` into `bulbs.json`, and you're done. (IP
can be `"Auto"` to auto-locate at startup, but a real IP is faster. Better yet,
set a static DHCP reservation per bulb in your router.)

> **Secrets:** `bulbs.json` and `devices.json` contain your local keys and are
> **git-ignored** — never commit or share them.

## Usage

```bash
ambibulb run                           # stream whole-screen color to all bulbs
ambibulb run --preset anime            # vivid + snappy, tuned for animation
ambibulb run --preset movie --zones 3  # live-action film, per-zone (L/C/R)
ambibulb studio                        # steady warm white (normal lighting, no sync)
ambibulb studio --temp neutral --brightness 70   # neutral white at 70%
ambibulb identify                      # blink each bulb in turn to see which is which
ambibulb identify --name "Bedroom"     # blink just one bulb
ambibulb once                          # push the current color once (quick test)
ambibulb discover                      # scan the LAN for Tuya devices
python3 -m ambibulb run ...            # same, without the console script
```

### Presets

`--preset` sets a whole tuning bundle. Any explicit flag still overrides it.

| Preset | Tuned for | Sets |
|---|---|---|
| `anime` | animation — flat vivid colors, fast cuts | `vivid` mode, high saturation, snappy, low cut, rate 15, fps 24, hue-stability, dims on dark scenes |
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
| `--follow-brightness` / `--fixed-brightness` | dim on dark scenes vs. always full brightness (presets follow by default) |
| `--bulb-rate N` | commands/sec per bulb (firmware tops out ~12–15) |
| `--smooth F` | easing toward the new color (lower = calmer) |
| `--cut N` | per-channel jump that snaps instantly (scene cut) |
| `--hue-hold N` | frames a new hue must persist before switching (anti-flicker; 0 = off) |
| `--no-letterbox` | don't auto-crop black movie bars |
| `--gamut none\|p3` | `p3` converts wide-gamut (Display P3) capture to sRGB for truer hue on macOS P3 screens |
| `--seconds N` | run N seconds then stop (0 = until Ctrl-C) |
| `--temp warm\|neutral\|cool` | white temperature for `studio` mode (default warm) |

### Studio mode

`ambibulb studio` sets all bulbs to a steady white — normal room light, no
screen sync. Use `--temp warm|neutral|cool` and `--brightness N`. It applies
once and exits; the bulbs hold that state until you change it.

### Identify which bulb is which

`ambibulb identify` blinks each bulb bright-white in turn, printing its name, so
you can see which physical bulb maps to which config entry — handy for ordering
`bulbs.json` left→right for `--zones`. Use `--name "<bulb>"` to blink just one.
(Smart bulbs have no positioning hardware, so blinking is the reliable way to
locate them — distance/RSSI can't be measured from these.)

### Wide-gamut (P3) displays

macOS wide-gamut screens (MacBook Air/Pro, Studio Display) capture pixels in
**Display P3**. By default ambibulb uses them as-is (colors read slightly
punchier). Add `--gamut p3` to convert P3 → sRGB first for truer hue:

```bash
ambibulb run --preset anime --gamut p3
```

Grey/white are unchanged; saturated colors are mapped into sRGB. It's opt-in
because the bulbs are uncalibrated anyway, so the difference is subtle.

`--zones 3` assumes your bulbs are placed left / center / right; config order
(bulb #1, #2, #3) maps left→right. Otherwise use `--zones 1`.

### Preview without bulbs

```bash
ambibulb-follow --mode vivid       # on-screen panel that follows your screen color
```

## HomeKit & Siri

Bridge the bulbs into Apple HomeKit (via Homebridge) to control them with
**Siri and the Home app** — locally, using the same keys. One command sets it
up:

```bash
./scripts/setup-homekit.sh
```

Then pair on an iPhone/iPad (Apple requires an iOS device to *add* a HomeKit
accessory — the Mac can only control it afterward) and use "Hey Siri" on the
Mac. Full guide, pairing steps, persistence, and a no-Apple-device alternative:
**[docs/homekit.md](docs/homekit.md)**.

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

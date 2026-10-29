# HomeKit & Siri control

Control your bulbs with **Siri and the Apple Home app** by bridging them into
HomeKit with [Homebridge](https://homebridge.io) and the
[`homebridge-tuya-local`](https://github.com/ayushsharma82/homebridge-tuya-local)
plugin. Control stays **local** (Homebridge talks to the bulbs over your LAN,
using the same local keys ambibulb uses).

> **One hard requirement:** Apple only lets you **add** a HomeKit accessory from
> an **iPhone, iPad, or iPod touch** — *not* from a Mac. You pair once on an iOS
> device; after that the **Mac Home app and "Hey Siri" on the Mac control the
> bulbs**. If you have no Apple mobile device, use the
> [Mac-only alternative](#no-apple-device-mac-only-voice-control) below instead.

---

## Prerequisites

- Node.js 18+ and npm (`node -v`).
- Your bulbs already set up for ambibulb — a working `bulbs.json` with each
  bulb's `id`, `key`, `ip`, `version` (see the main README's setup section).
- An iPhone/iPad to pair (once).

## Automated setup

From the repo root:

```bash
./scripts/setup-homekit.sh
```

This:
1. Installs Homebridge, its web UI, and `homebridge-tuya-local` (global npm).
2. Patches a known plugin/Homebridge-2.x incompatibility
   (`hap.Accessory.Categories` → `hap.Categories`).
3. Generates `~/.homebridge/config.json` from your `bulbs.json` — one
   `RGBTWLight` per bulb, protocol `3.5`, with the Tuya datapoints
   (power 20, mode 21, brightness 22, color-temp 23, color 24).

It prints your **HomeKit pairing code** and the UI address at the end.

Re-running is safe — it preserves your existing pairing code.

## Start Homebridge

Foreground (to test):

```bash
homebridge
```

You should see, for each bulb:

```
[TuyaLan] Connected to <bulb>
[TuyaLan] Ready to handle <bulb> (RGBTWLight:3.5)
[TuyaLan] Color format ... identified as HSB (length: 12)
```

Make it **permanent** (auto-start on login/boot):

```bash
sudo hb-service install --user "$USER"
```

The web UI for managing it lives at **http://localhost:8581**.

## Pair with HomeKit (on an iPhone/iPad)

1. Make sure the iOS device and the Mac are signed into the **same Apple
   Account** and on the **same Wi-Fi**.
2. On the iPhone/iPad: **Home app → ＋ → Add Accessory**.
3. Tap **"More options…"** (Homebridge appears as an uncertified accessory),
   choose **Homebridge Ambibulb**, then **Add Anyway**.
4. Enter the **pairing code** the setup script printed (format `XXX-XX-XXX`).
5. Your bulbs appear in the Home app.

## Use it from the Mac

Once paired on iOS, on the Mac:

- **System Settings → [Apple Account] → iCloud → Home: on.**
- Open the **Home app** on the Mac — the bulbs are there to click.
- **"Hey Siri"** on the Mac: *"turn on the batten", "set the bulb to blue",
  "dim the lights to 30%", "turn off the lights".*

> For control **away from home** and for automations, HomeKit needs a **Home
> Hub** (a HomePod or Apple TV). On your home Wi-Fi it works without one.

## Important: Homebridge vs. ambibulb

Tuya bulbs accept only a couple of simultaneous local connections. Homebridge
holds **persistent** connections, so running `ambibulb run` (screen sync) at the
same time can cause drops. Use them **one at a time**:

- Everyday on/off/color/dim by voice → Homebridge + Siri.
- Screen sync (anime/movie) → stop Homebridge first
  (`sudo hb-service stop`, or quit the foreground process), then `ambibulb run`.

## No Apple device? Mac-only voice control

If you don't have an iPhone/iPad, you can't use HomeKit — but **Siri on the Mac
can still control the bulbs** through **Shortcuts** that call `ambibulb`:

1. Open the **Shortcuts** app on the Mac → **＋** new shortcut.
2. Add a **"Run Shell Script"** action, e.g.:
   ```
   cd /path/to/this/repo && /path/to/ambibulb studio --temp cool
   ```
3. Name the shortcut (e.g. "Cool white"). Then say **"Hey Siri, cool white".**

Make shortcuts for: `ambibulb run --preset anime`, `ambibulb studio --temp warm`,
`ambibulb off`, etc. No HomeKit, no iPhone required.

## Troubleshooting

- **"Cannot read properties of undefined (reading 'LIGHTBULB')"** — the plugin
  isn't patched for Homebridge 2.x. Re-run `./scripts/setup-homekit.sh` (it
  applies the patch), or downgrade isn't needed.
- **A bulb shows "Failed to discover … will connect via <ip>"** — harmless; the
  plugin falls back to the configured IP. Keep IPs current in `bulbs.json`
  (`ambibulb discover`), or set static DHCP reservations.
- **Bulb offline in Home app** — its IP changed (DHCP). Update `bulbs.json` and
  re-run the config generator, or give it a static reservation.
- **Can't find "Add Accessory" on the Mac** — expected; you must add from an
  iOS device. The Mac can only control, not add.
- **Color looks off** — the plugin auto-detects HSB (12-char) for these bulbs;
  if a model differs, adjust `dpColor`/`colorFunction` in `~/.homebridge/config.json`.

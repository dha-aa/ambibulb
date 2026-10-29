#!/usr/bin/env python3
"""Generate ~/.homebridge/config.json for the ambibulb bulbs.

Reads a bulbs.json (the same file ambibulb uses) and writes a Homebridge
config with the homebridge-tuya-local (TuyaLan) platform, one RGBTWLight per
bulb, wired with the known Tuya datapoints and protocol v3.5.

An existing bridge block (name/username/pin) is preserved, so re-running keeps
your HomeKit pairing code stable.

Usage:
    python3 scripts/gen_homebridge_config.py [path/to/bulbs.json]
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~/.homebridge"))
CONFIG = HOME / "config.json"
UI_PORT = 8581

# The datapoints we confirmed for these Wipro/Tuya color bulbs.
DP = {"dpPower": 20, "dpMode": 21, "dpBrightness": 22,
      "dpColorTemperature": 23, "dpColor": 24}


def rand_pin() -> str:
    return f"{random.randint(0, 999):03d}-{random.randint(0, 99):02d}-{random.randint(0, 999):03d}"


def rand_username() -> str:
    # Locally-administered MAC (starts with 0E).
    return "0E:" + ":".join(f"{random.randint(0, 255):02X}" for _ in range(5))


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("bulbs.json")
    if not src.exists():
        print(f"error: {src} not found. Set up your bulbs first "
              "(ambibulb discover + tinytuya wizard).", file=sys.stderr)
        return 1
    bulbs = json.loads(src.read_text()).get("bulbs", [])
    if not bulbs:
        print(f"error: no bulbs in {src}", file=sys.stderr)
        return 1

    # Preserve an existing bridge so the pairing code doesn't change on re-run.
    bridge = {"name": "Homebridge Ambibulb", "username": rand_username(),
              "port": 51826, "pin": rand_pin()}
    if CONFIG.exists():
        try:
            old = json.loads(CONFIG.read_text())
            if isinstance(old.get("bridge"), dict) and old["bridge"].get("pin"):
                bridge = old["bridge"]
        except Exception:
            pass

    devices = [{
        "name": str(b.get("name") or b["id"][:8]).strip(),
        "type": "RGBTWLight",
        "id": b["id"],
        "key": b["key"],
        "ip": b.get("ip", "Auto"),
        "version": str(b.get("version", "3.5")),  # plugin compares to '3.5'
        **DP,
    } for b in bulbs]

    config = {
        "bridge": bridge,
        "accessories": [],
        "platforms": [
            {"platform": "config", "name": "Config", "port": UI_PORT},
            {"platform": "TuyaLan", "devices": devices},
        ],
    }
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=2))
    print(f"Wrote {CONFIG} with {len(devices)} bulb(s).")
    print(f"HomeKit pairing code: {bridge['pin']}")
    print(f"Homebridge UI:        http://localhost:{UI_PORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build bulbs.json from the tinytuya wizard's devices.json.

The wizard (`python3 -m tinytuya wizard`) writes devices.json with each bulb's
id, key, ip and version. This turns that into the bulbs.json ambibulb uses, so
you never hand-edit config.

Usage:
    python3 scripts/make-bulbs-json.py [devices.json] [bulbs.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("devices.json")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("bulbs.json")
    if not src.exists():
        print(f"error: {src} not found — run:  python3 -m tinytuya wizard",
              file=sys.stderr)
        return 1
    try:
        devices = json.loads(src.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: {src} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    bulbs = []
    for d in devices:
        if not d.get("id") or not d.get("key"):
            continue
        bulbs.append({
            "name": (d.get("name") or d["id"][:8]).strip(),
            "id": d["id"],
            "key": d["key"],
            "ip": d.get("ip") or "Auto",
            "version": float(d.get("version", 3.3)),
        })
    if not bulbs:
        print(f"error: no devices with keys found in {src}", file=sys.stderr)
        return 1

    if out.exists():
        print(f"note: {out} exists and will be overwritten.", file=sys.stderr)
    out.write_text(json.dumps({"bulbs": bulbs}, indent=2))
    print(f"Wrote {out} with {len(bulbs)} bulb(s):")
    for b in bulbs:
        print(f"  {b['name']}  ({b['ip']}, v{b['version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

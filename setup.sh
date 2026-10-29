#!/usr/bin/env bash
#
# ambibulb one-shot setup.
#
# Clone the repo, run ./setup.sh, follow the prompts. It installs the Python
# package and its dependencies, and builds bulbs.json. The ONLY manual step is
# reading your bulbs' local keys with the Tuya wizard (that needs your own free
# Tuya account — nothing can automate it). See the README "Setup" section.
#
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "==> ambibulb setup"
command -v python3 >/dev/null || { echo "  python3 is required: https://www.python.org"; exit 1; }

echo "==> Installing ambibulb + dependencies (mss, pillow, tinytuya)"
if ! python3 -m pip install -e . ; then
  echo
  echo "  pip install failed. If you saw 'externally-managed-environment', use a venv:"
  echo "      python3 -m venv .venv && source .venv/bin/activate && ./setup.sh"
  exit 1
fi

if [ ! -f bulbs.json ]; then
  if [ -f devices.json ]; then
    echo "==> Found devices.json — building bulbs.json from it"
    python3 scripts/make-bulbs-json.py devices.json bulbs.json
  else
    cat <<'EOF'

==> Next: get your bulbs' local keys (the one manual step).

  Your bulbs must already be paired in the Wipro / Smart Life app. Then read
  their keys once via a free Tuya IoT account (details in README "Setup"):

      python3 -m tinytuya wizard        # writes devices.json

  Then re-run:  ./setup.sh
  (it will turn devices.json into bulbs.json automatically).
EOF
    exit 0
  fi
fi

echo "==> Checking the bulbs are reachable on your network"
python3 -m ambibulb discover || true

cat <<'EOF'

==> Ready. Try it:
    ambibulb run --preset anime      # screen -> bulbs, tuned for animation
    ambibulb studio --temp cool      # steady white (normal lighting)
    ambibulb identify                # blink each bulb to see which is which

Optional HomeKit/Siri:  ./scripts/setup-homekit.sh   (see docs/homekit.md)
EOF

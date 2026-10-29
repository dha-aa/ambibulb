#!/usr/bin/env bash
#
# Automated HomeKit / Siri setup for the ambibulb bulbs, via Homebridge.
#
# Installs Homebridge + its web UI + the Tuya local plugin, applies a known
# compatibility patch, and generates the Homebridge config from bulbs.json.
#
# After this you still PAIR on an iPhone/iPad (Apple does not allow adding
# HomeKit accessories from a Mac). See docs/homekit.md.
#
# Usage:
#   ./scripts/setup-homekit.sh [path/to/bulbs.json]
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_SRC="${1:-$HERE/bulbs.json}"

echo "==> Checking prerequisites"
command -v node >/dev/null || { echo "  Node.js is required: https://nodejs.org"; exit 1; }
command -v npm  >/dev/null || { echo "  npm is required (comes with Node.js)"; exit 1; }
command -v python3 >/dev/null || { echo "  python3 is required"; exit 1; }
if [ ! -f "$CONFIG_SRC" ]; then
  echo "  No bulbs config at: $CONFIG_SRC"
  echo "  Set up your bulbs first:  ambibulb discover  +  python3 -m tinytuya wizard"
  exit 1
fi
echo "  node $(node -v), npm $(npm -v)"

echo "==> Installing Homebridge + UI + Tuya local plugin (global npm)"
npm install -g homebridge homebridge-config-ui-x homebridge-tuya-local

# homebridge-tuya-local (<=3.1.5) reads Categories from hap.Accessory.Categories,
# which Homebridge 2.x removed (now hap.Categories). Patch that one reference.
echo "==> Applying Homebridge 2.x compatibility patch to homebridge-tuya-local"
PLUGIN="$(npm root -g)/homebridge-tuya-local/index.js"
if [ -f "$PLUGIN" ] && grep -q 'Accessory: {Categories}' "$PLUGIN"; then
  cp "$PLUGIN" "$PLUGIN.bak"
  sed 's/Accessory: {Categories}/Categories/' "$PLUGIN.bak" > "$PLUGIN"
  if node -c "$PLUGIN" 2>/dev/null; then
    echo "  patched (backup: $PLUGIN.bak)"
  else
    echo "  patch produced invalid JS — restoring backup"; cp "$PLUGIN.bak" "$PLUGIN"; exit 1
  fi
else
  echo "  not needed (already patched or newer plugin)"
fi

echo "==> Generating ~/.homebridge/config.json from $CONFIG_SRC"
python3 "$HERE/scripts/gen_homebridge_config.py" "$CONFIG_SRC"

cat <<'EOF'

==> Setup complete. Next steps:

  1. Start Homebridge (foreground, to test):
         homebridge
     …or install it as an auto-start service (survives reboot):
         sudo hb-service install --user "$USER"

  2. Pair it — REQUIRES an iPhone/iPad (Apple does not allow pairing from a Mac):
         Home app  ->  +  ->  Add Accessory  ->  enter the pairing code above.

  3. After pairing, control the bulbs from the Mac Home app and "Hey Siri"
     on the Mac. See docs/homekit.md for details and troubleshooting.

  Note: Homebridge holds persistent connections to the bulbs, which can
  contend with `ambibulb run` (screen sync). Use one at a time.
EOF

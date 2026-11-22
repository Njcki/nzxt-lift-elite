#!/usr/bin/env bash
# Manual install of the CoolerControl plugin from a checkout (the PKGBUILD does the same).
# Usage:  sudo ./cc-plugin/install.sh      (needs python-grpcio and python-grpcio-tools)
set -euo pipefail
cd "$(dirname "$0")"
ID=nzxt_lift_elite
DEST=/var/lib/coolercontrol/plugins/$ID
LIB=/usr/lib/nzxt-lift-elite/coolercontrol
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
python3 -c 'import nzxt_lift_elite' 2>/dev/null || {
  echo "The nzxt_lift_elite library is not installed system-wide (install the package or: pip install .)"; exit 1; }

systemctl stop coolercontrold 2>/dev/null || true
install -d -m 755 "$LIB"
rm -rf "$LIB/gen"
./build_stubs.sh "$LIB/gen" >/dev/null
install -m 644 server.py "$LIB/server.py"
[ -d "$DEST" ] || install -d -m 755 "$DEST"
[ -d "$DEST/ui" ] || install -d -m 755 "$DEST/ui"
install -m 644 manifest.toml "$DEST/manifest.toml"
install -m 755 nzxt_lift_elite "$DEST/$ID"
install -m 644 ui/index.html "$DEST/ui/index.html"
systemctl start coolercontrold 2>/dev/null || true
echo "Installed to $DEST (settings kept). Logs: journalctl -u coolercontrold -u cc-plugin-$ID -f"

#!/usr/bin/env bash
# Removes a manual install of the plugin (not needed with the package: use pacman -R).
set -euo pipefail
ID=nzxt_lift_elite
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
systemctl stop coolercontrold 2>/dev/null || true
rm -rf "/var/lib/coolercontrol/plugins/$ID" /usr/lib/nzxt-lift-elite/coolercontrol
systemctl start coolercontrold 2>/dev/null || true
echo "Removed."

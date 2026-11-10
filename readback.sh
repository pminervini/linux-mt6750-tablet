#!/bin/bash
# After the tablet is back on stock: collect the previous boot's ramoops records.
# usage: ./readback.sh <tag>  -> logs/<tag>-<time>/
set -euo pipefail
cd "$(dirname "$0")"
H=${TABLET:-mediatek.vpn}
D=logs/${1:?tag}-$(date +%Y%m%dT%H%M%S)
mkdir -p "$D"
ssh "$H" 'mountpoint -q /mnt/pstore || { sudo mkdir -p /mnt/pstore; sudo mount -t pstore pstore /mnt/pstore; }
  sudo ls -la /mnt/pstore >&2; sudo tar cf - -C /mnt/pstore .' > "$D/pstore.tar" 2> "$D/ls.txt" || true
tar xf "$D/pstore.tar" -C "$D" 2>/dev/null || true
ssh "$H" 'cat /proc/uptime; sudo chroot /proc/1/root /system/bin/getprop ro.boot.bootreason' > "$D/stock-state.txt" 2>&1 || true
ls -la "$D"
for f in "$D"/console-ramoops*; do [ -f "$f" ] && { echo "== $f"; tail -n 40 "$f"; }; done

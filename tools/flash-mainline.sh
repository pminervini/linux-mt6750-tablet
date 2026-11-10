#!/bin/bash
# Flash release/boot-mainline-<tag>.img into `recovery` from the running mainline Debian
# (over ssh tablet-usb), verify, reboot, wait for it to come back.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${1:?tag}; IMG=release/boot-mainline-$TAG.img
SUM=$(sha256sum $IMG | cut -d' ' -f1); SZ=$(stat -c %s $IMG)
scp -q $IMG tablet-usb:/tmp/boot.img
ssh tablet-usb "set -e; P=/dev/disk/by-partlabel/recovery; sudo blockdev --setrw \$P
  sudo dd if=/tmp/boot.img of=\$P bs=1M conv=fsync status=none; sudo blockdev --setro \$P
  [ \"\$(sudo head -c $SZ \$P | sha256sum | cut -d' ' -f1)\" = $SUM ] && echo 'flashed+verified $TAG'
  echo 1 | sudo tee /sys/kernel/mt6755_pmic/boot_recovery >/dev/null || true   # one-shot: a bad kernel falls back to Android
  sudo systemctl reboot" || true
sleep 15
for i in $(seq 60); do ping -c1 -W1 192.168.77.2 >/dev/null 2>&1 && break; python3 tools/tab.py up >/dev/null 2>&1; sleep 2; done
for i in $(seq 30); do ssh -o BatchMode=yes -o ConnectTimeout=3 tablet-usb true 2>/dev/null && break; sleep 3; done
ssh tablet-usb 'echo "MAINLINE $(uname -r) up $(cut -d" " -f1 /proc/uptime)s"'

#!/bin/bash
# Write a test image to the tablet's `recovery` partition over SSH (boot stays stock+Magisk)
# and reboot into it.  usage: ./test-recovery.sh release/boot-mainline-<tag>.img [--no-reboot]
set -euo pipefail
cd "$(dirname "$0")"
IMG=${1:?image}
H=${TABLET:-mediatek.vpn}
P=/dev/block/platform/mtk-msdc.0/11230000.msdc0/by-name/recovery
SZ=$(stat -c %s "$IMG")
SHA=$(sha256sum "$IMG" | cut -d' ' -f1)
[ "$SZ" -le $((16 << 20)) ] || { echo "too big"; exit 1; }
if adb get-state >/dev/null 2>&1; then
  echo "via adb"
  adb push "$IMG" /data/local/tmp/test.img
  adb shell "su -c 'dd if=/data/local/tmp/test.img of=$P bs=1048576 && sync && head -c $SZ $P | sha256sum'" | grep -q "^$SHA" \
    && echo 'recovery written + verified' || { echo 'VERIFY FAILED'; exit 1; }
  [ "${2:-}" = --no-reboot ] && exit 0
  echo "rebooting into recovery at $(date +%T)"; adb reboot recovery; exit 0
fi
scp -q "$IMG" "$H:/tmp/test.img"
ssh "$H" "set -e
  echo \"$SHA  /tmp/test.img\" | sha256sum -c --quiet
  sudo dd if=/tmp/test.img of=$P bs=1048576 conv=fsync status=none
  got=\$(sudo head -c $SZ $P | sha256sum | cut -d' ' -f1)
  [ \"\$got\" = $SHA ] && echo 'recovery written + verified' || { echo 'VERIFY FAILED'; exit 1; }"
[ "${2:-}" = --no-reboot ] && exit 0
echo "rebooting into recovery at $(date +%T)"
ssh "$H" 'sync; sudo chroot /proc/1/root /system/bin/reboot recovery' || true

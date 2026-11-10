#!/bin/bash
# Unattended test cycle, no mtkclient in the normal path:
#  flash release/boot-mainline-<tag>.img to `recovery`, boot it; the test kernel reboots itself
#  (mtk_wdt restart power-cycles the eMMC), stock Android comes back, and we pull our log from
#  stock's /sys/fs/pstore (same ramoops layout). Fallback if Android does not return in time:
#  catch preloader/BROM with mtkclient, dump RAM, VEMC power-cycle + reset.
# usage: ./run-test.sh <tag> [return-timeout-seconds]
set -uo pipefail
cd "$(dirname "$0")"
TAG=${1:?tag}; TMO=${2:-360}
IMG=release/boot-mainline-$TAG.img
A="adb"
LOG=logs/$TAG; mkdir -p $LOG
up() { [ "$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; }
timeout 120 bash -c "until [ \"\$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')\" = 1 ]; do sleep 2; done" \
  || { echo "tablet not in Android"; exit 1; }
BAT=$($A shell dumpsys battery | awk '/level:/{print $2}' | tr -d '\r')
echo "battery $BAT%"; [ "${BAT:-0}" -ge 30 ] || { echo "battery low, not testing"; exit 1; }
./test-recovery.sh "$IMG" | grep -v records || exit 1
T0=$SECONDS
sleep 20   # let it leave Android
until up || [ $((SECONDS - T0)) -ge $TMO ]; do sleep 3; done
if up; then
  echo "back in Android after $((SECONDS - T0))s"
  $A shell 'su -c "cd /sys/fs/pstore && for f in *; do echo == \$f; cat \$f; done"' > $LOG/pstore.txt 2>&1
  $A shell 'su -c "cat /proc/pl_lk"' > $LOG/pl_lk.txt 2>&1
  grep -a "STAGE\|Linux version" $LOG/pstore.txt | head -3
  echo "log: $LOG/pstore.txt ($(wc -l < $LOG/pstore.txt) lines)"
  exit 0
fi
echo "NOT back after ${TMO}s, falling back to mtkclient recovery"
S=${TAG^^}
{ echo "da peek 0x44410000 0xe0000 --filename out/$TAG-ramoops.bin"
  echo "da peek 0xb00c0000 0x20000 --filename out/$TAG-marker-ramcon.bin"
  echo "da peek 0x7ff80000 0x80000 --filename out/$TAG-logstore.bin"
  cat diag/recover-tail.txt; } > diag/session-$S.txt
rm -f diag/run-$S.log
diag/wait-mtk.sh $S 1800
sudo chown -R "${SUDO_USER:-$USER}" diag/out
timeout 150 bash -c "until [ \"\$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')\" = 1 ]; do sleep 2; done" \
  && echo "recovered to Android" || echo "WARNING: still not in Android"
exit 2

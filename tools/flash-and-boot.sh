#!/bin/bash
# Wait for stock Android, flash release/boot-mainline-<tag>.img to recovery, boot it, bring up the USB link.
cd "$(dirname "$0")/.."
TAG=${1:?tag}; A="adb"
echo "waiting for Android $(date +%T)"
while [ "$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != 1 ]; do sleep 3; done
echo "ANDROID $(date +%T)"
BAT=$($A shell dumpsys battery | awk '/level:/{print $2}' | tr -d '\r')
echo "battery ${BAT}%"
VOLT=$($A shell dumpsys battery | awk '/voltage:/{print $2}' | tr -d '\r')
echo "battery ${VOLT} mV"
# the MTK gauge lags badly after a deep discharge, so trust the voltage too
[ "${BAT:-0}" -ge 40 ] || [ "${VOLT:-0}" -ge 3850 ] || { echo "battery too low (<40% and <3.85 V), not booting mainline"; exit 3; }
./test-recovery.sh release/boot-mainline-$TAG.img 2>&1 | grep -v records | tail -1
for i in $(seq 100); do ping -c1 -W1 192.168.77.2 >/dev/null 2>&1 && break; python3 tools/tab.py up >/dev/null 2>&1; sleep 2; done
ping -c1 -W1 192.168.77.2 >/dev/null 2>&1 && echo "LINK $(date +%T)" || { echo "NO LINK"; exit 1; }

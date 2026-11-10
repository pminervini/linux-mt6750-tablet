#!/bin/bash
# Wait for stock Android, reboot into recovery (mainline Debian), wait for ssh tablet-usb.
cd "$(dirname "$0")/.."
A="adb"
timeout ${1:-400} bash -c "until [ \"\$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')\" = 1 ]; do sleep 3; done" || { echo "no Android"; exit 1; }
echo "ANDROID $(date +%T)"; $A reboot recovery
for i in $(seq 60); do ping -c1 -W1 192.168.77.2 >/dev/null 2>&1 && break; python3 tools/tab.py up >/dev/null 2>&1; sleep 2; done
for i in $(seq 30); do ssh -o BatchMode=yes -o ConnectTimeout=3 tablet-usb true 2>/dev/null && break; sleep 3; done
ssh tablet-usb 'echo "MAINLINE $(uname -r) up $(cut -d" " -f1 /proc/uptime)s"'

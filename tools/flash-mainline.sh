#!/bin/bash
# Flash release/boot-mainline-<tag>.img into `recovery` from the running mainline Debian, verify
# the write, reboot, and check that the tablet came back on exactly that kernel.
#   tools/flash-mainline.sh <tag>                     over the USB link (ssh host tablet-usb)
#   TABLET=mediatek tools/flash-mainline.sh <tag>     over Wi-Fi/VPN
# Run it only after mainline-default.service has finished (>= 60 s uptime): before that, the
# reboot falls back to Android (the fail-safe, not a failure).
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${1:?usage: $0 <tag>}; IMG=release/boot-mainline-$TAG.img
H=${TABLET:-tablet-usb}
[ -f "$IMG" ] || { echo "no $IMG"; exit 1; }
SUM=$(sha256sum "$IMG" | cut -d' ' -f1); SZ=$(stat -c %s "$IMG")
# the kernel's banner, as /proc/version will print it if the new kernel is the one running
WANT=$(python3 -I - "$IMG" <<'EOF'
import struct, sys, zlib
d = open(sys.argv[1], 'rb').read()
assert d[:8] == b'ANDROID!', 'not an Android boot image'
ksz, page = struct.unpack_from('<I', d, 8)[0], struct.unpack_from('<I', d, 36)[0]
k = zlib.decompressobj(31).decompress(d[page:page + ksz])   # Image.gz; the appended DTB is ignored
import re   # the full banner (with "#<build> SMP ... <date>"), not the build-salt copy without them
print(re.search(rb'Linux version [^\n\0]* #\d+ [^\n\0]*', k).group().decode())
EOF
)
echo "image  $IMG  sha256 $SUM"
echo "kernel $WANT"

ssh() { command ssh -o BatchMode=yes -o ConnectTimeout=5 "$@"; }
BOOT0=$(ssh "$H" cat /proc/sys/kernel/random/boot_id)
up=$(ssh "$H" cut -d. -f1 /proc/uptime)
[ "$up" -ge 65 ] || { echo "tablet up only ${up}s: wait for mainline-default.service (>= 60 s), else the reboot lands in Android"; exit 1; }
scp -q "$IMG" "$H:/tmp/boot.img"

# Write + verify; only a verified image gets the boot flag and the reboot. The remote side prints
# REBOOTING right before asking for the reboot: after that, a dropped connection is expected.
set +e
OUT=$(ssh "$H" "set -eu; P=/dev/disk/by-partlabel/recovery
  [ \"\$(sha256sum /tmp/boot.img | cut -d' ' -f1)\" = $SUM ] || { echo 'COPY CORRUPT'; exit 3; }
  sudo blockdev --setrw \$P
  sudo dd if=/tmp/boot.img of=\$P bs=1M conv=fsync status=none
  sudo blockdev --setro \$P
  sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
  [ \"\$(sudo head -c $SZ \$P | sha256sum | cut -d' ' -f1)\" = $SUM ] || { echo 'VERIFY FAILED'; exit 4; }
  echo 'flashed+verified $TAG'
  echo 1 | sudo tee /sys/kernel/mt6755_pmic/boot_recovery >/dev/null   # one-shot: a bad kernel falls back to Android
  rm -f /tmp/boot.img
  echo REBOOTING
  sudo systemctl reboot" 2>&1)
RC=$?
set -e
echo "$OUT"
if ! grep -q '^REBOOTING$' <<<"$OUT"; then
	echo "FLASH FAILED (rc=$RC): not rebooted; recovery may hold a partial image, do not reboot the tablet before reflashing"
	exit 1
fi

echo "waiting for the tablet..."
sleep 20
ok=
for i in $(seq 60); do
	if [ "$H" = tablet-usb ]; then
		ping -c1 -W1 192.168.77.2 >/dev/null 2>&1 || { python3 tools/tab.py up >/dev/null 2>&1 || true; }
	fi
	if BOOT1=$(ssh -o ConnectTimeout=3 "$H" cat /proc/sys/kernel/random/boot_id 2>/dev/null); then ok=1; break; fi
	sleep 3
done
[ -n "$ok" ] || { echo "TABLET DID NOT COME BACK on $H (Android fallback? check adb)"; exit 1; }
[ "$BOOT1" != "$BOOT0" ] || { echo "SAME BOOT: the tablet did not reboot"; exit 1; }
HAVE=$(ssh "$H" cat /proc/version)
if [ "$HAVE" != "$WANT" ]; then
	echo "WRONG KERNEL: running"; echo "  $HAVE"; echo "expected"; echo "  $WANT"; exit 1
fi
echo "MAINLINE $TAG running: $(ssh "$H" 'uname -r; cut -d" " -f1 /proc/uptime' | paste -sd' ') s up"

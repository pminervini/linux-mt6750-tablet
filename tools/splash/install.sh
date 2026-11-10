#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Install a boot splash on the tablet over ssh (run on the build machine, needs Pillow).
#
#   install.sh default            generated cyberpunk splash: lk logo + boot animation
#   install.sh logo IMAGE         boot loader logo (static; any image, fitted to 1280x800 landscape)
#   install.sh anim SRC           boot animation (GIF/APNG, PNG directory, single image, or .splash);
#   install.sh anim none            "none" removes it (the logo then stays up until the login prompt)
#   install.sh lk-patch           remove lk's Orange State screen + its 5 s delay (4-byte patch)
#   install.sh capture OUT.png    what lk showed at this boot (kernel snapshot), in view orientation
#   install.sh restore PART FILE  write a saved partition image back (PART: logo or lk)
#
# TABLET=user@host selects the tablet (default tablet-usb). Every partition is saved to
# $BACKUP_DIR (default tools/splash/backups/) before it is written; writes are read back and
# compared. Partition changes take effect at the next boot.
set -euo pipefail
cd "$(dirname "$0")"
T=${TABLET:-tablet-usb}
BK=${BACKUP_DIR:-backups}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BK"

read_part() {   # read_part NAME LOCALFILE
  ssh "$T" "sudo cat /dev/disk/by-partlabel/$1" > "$2"
  [ -s "$2" ] || { echo "could not read $1"; exit 1; }
}

save_part() {   # keep a copy of the partition as it is now
  local f="$BK/$1-$(date +%Y%m%dT%H%M%S).bin"
  read_part "$1" "$f"
  echo "saved $1 -> $f ($(sha256sum "$f" | cut -c1-16))"
}

write_part() {  # write_part NAME FILE: write, read back, compare
  local sum sz
  sum=$(sha256sum "$2" | cut -d' ' -f1); sz=$(stat -c %s "$2")
  scp -q "$2" "$T:/tmp/part.img"
  ssh "$T" "set -e; P=/dev/disk/by-partlabel/$1
    [ \$(sudo blockdev --getsize64 \$P) -ge $sz ]
    sudo blockdev --setrw \$P
    sudo dd if=/tmp/part.img of=\$P bs=64k conv=fsync status=none
    sudo blockdev --flushbufs \$P
    [ \"\$(sudo head -c $sz \$P | sha256sum | cut -d' ' -f1)\" = $sum ] && echo 'wrote $1, read back OK'
    rm -f /tmp/part.img"
}

do_logo() {
  save_part logo
  read_part logo "$TMP/logo.bin"
  python3 -I splash.py logo-pack "$TMP/logo.bin" "$1" "$TMP/logo-new.bin"
  write_part logo "$TMP/logo-new.bin"
}

do_anim() {
  if [ "$1" = none ]; then
    ssh "$T" "sudo systemctl disable -q tablet-splash.service 2>/dev/null; sudo rm -f /usr/local/share/tablet-splash/boot.splash"
    echo "boot animation removed"; return
  fi
  local f="$1"
  case "$f" in
    *.splash) ;;
    *) python3 -I splash.py anim-pack "$f" "$TMP/boot.splash"; f="$TMP/boot.splash" ;;
  esac
  scp -q tablet-splash tablet-splash.service splash-getty.conf "$f" "$T:/tmp/"
  ssh "$T" "set -e; sudo install -m 755 /tmp/tablet-splash /usr/local/bin/tablet-splash
    sudo install -D -m 644 /tmp/$(basename "$f") /usr/local/share/tablet-splash/boot.splash
    sudo install -m 644 /tmp/tablet-splash.service /etc/systemd/system/tablet-splash.service
    sudo install -D -m 644 /tmp/splash-getty.conf /etc/systemd/system/getty@tty1.service.d/splash.conf
    rm -f /tmp/tablet-splash /tmp/tablet-splash.service /tmp/splash-getty.conf /tmp/$(basename "$f")
    sudo systemctl daemon-reload; sudo systemctl enable -q tablet-splash.service
    echo 'boot animation installed'"
}

do_lk_patch() {
  save_part lk
  read_part lk "$TMP/lk.bin"
  python3 -I splash.py lk-patch "$TMP/lk.bin" "$TMP/lk-new.bin"
  if cmp -s "$TMP/lk.bin" "$TMP/lk-new.bin"; then echo "lk unchanged"; return; fi
  [ "$(cmp -l "$TMP/lk.bin" "$TMP/lk-new.bin" | wc -l)" -le 4 ] || { echo "patch changes more than 4 bytes, refusing"; exit 1; }
  write_part lk "$TMP/lk-new.bin"
}

do_capture() {
  # mt6755-bringup copies lk's screen to simple-framebuffer base + 2 * size at boot
  ssh "$T" "sudo python3 -c \"
import mmap, os, sys
base = size = None
for l in open('/proc/iomem'):
    if l.strip().endswith(': simplefb'):
        a, b = l.split(':')[0].strip().split('-'); base, size = int(a, 16), int(b, 16) - int(a, 16) + 1
f = os.open('/dev/mem', os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(f, size, mmap.MAP_SHARED, mmap.PROT_READ, offset=base + 2 * size)
sys.stdout.buffer.write(m[:])\"" > "$TMP/fb.raw"
  python3 -I splash.py fb2png "$TMP/fb.raw" "$1"
  echo "lk screen of this boot -> $1"
}

case "${1:-}" in
  default)
    python3 -I splash.py cyber "$TMP/cyber"
    do_logo "$TMP/cyber/logo.png"
    do_anim "$TMP/cyber/frames" ;;
  logo) do_logo "${2:?image}" ;;
  anim) do_anim "${2:?animation or none}" ;;
  lk-patch) do_lk_patch ;;
  capture) do_capture "${2:?output png}" ;;
  restore)
    case "${2:-}" in logo|lk) ;; *) echo "restore logo|lk FILE"; exit 1 ;; esac
    save_part "$2"; write_part "$2" "${3:?file}" ;;
  *) sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

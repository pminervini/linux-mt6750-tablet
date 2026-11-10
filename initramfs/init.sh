#!/bin/busybox sh
# Stage-4 init: eMMC (userdata rw, rest ro), USB CDC-Ethernet, then switch_root into
# /data/linux/debian with /run/tablet-init as PID 1 (sshd + telnet fallback).
# Stay in this initramfs instead: create /data/linux/mainline-stay (or debian missing).
# Logs go to /dev/kmsg (-> ramoops -> stock /sys/fs/pstore after reboot).
/bin/busybox mkdir -p /bin /sbin /usr/bin /usr/sbin /proc /sys /dev /mnt /tmp /pstore
/bin/busybox --install -s
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t debugfs debugfs /sys/kernel/debug
mount -t pstore pstore /pstore
mount -t tmpfs tmpfs /tmp
mkdir -p /dev/pts && mount -t devpts devpts /dev/pts
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
log() { echo "<5>STAGE2: $*" > /dev/kmsg; }
# progress on the screen, except on a quiet (splash) boot: any text there ends the boot logo
say() { grep -qw quiet /proc/cmdline || echo -e "\r$*" > /dev/tty0; }
logf() { while IFS= read -r l; do log "$1 $l"; done; }

log "init up: $(cat /proc/version)"
log "cmdline: $(cat /proc/cmdline)"
log "cpus online: $(cat /sys/devices/system/cpu/online)"

# feed the hardware watchdog (else WATCHDOG_OPEN_TIMEOUT resets us)
[ -e /dev/watchdog ] && watchdog -t 5 -T 31 /dev/watchdog && log "watchdog daemon started"

# --- eMMC, strictly read-only ---
i=0; while [ ! -b /dev/mmcblk0 ] && [ $i -lt 20 ]; do sleep 1; i=$((i+1)); done
if [ -b /dev/mmcblk0 ]; then
  UD0=$(grep -l "PARTNAME=userdata" /sys/class/block/mmcblk0p*/uevent 2>/dev/null | head -1)
  UDN=/dev/$(basename $(dirname $UD0))
  # Default-boot handshake (BCB command field at offset 0 of "para"; lk boots recovery =
  # mainline only on "boot-recovery"). Consume it now: if this boot hangs or crashes before
  # userspace reports healthy (mainline-default.service re-arms it), the watchdog reset
  # lands in Android instead of looping on a broken mainline.
  PA=$(grep -l "PARTNAME=para$" /sys/class/block/mmcblk0p*/uevent 2>/dev/null | head -1)
  if [ -n "$PA" ]; then
    PAN=/dev/$(basename $(dirname $PA))
    BCB=$(dd if=$PAN bs=32 count=1 2>/dev/null | tr -d '\000')
    if [ "$BCB" = "boot-recovery" ]; then
      printf 'mainline-trying\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000' |
        dd of=$PAN bs=32 count=1 conv=notrunc,fsync 2>/dev/null
      log "BCB: boot-recovery consumed -> mainline-trying"
    else
      log "BCB: '$BCB' (left alone)"
    fi
  fi
  # NB: a read-only flag on the whole-disk node propagates to every partition, so only flag partitions
  for b in /dev/mmcblk0p* /dev/mmcblk0boot* /dev/mmcblk0rpmb; do [ -b "$b" ] && [ "$b" != "$UDN" ] && blockdev --setro "$b"; done
  log "mmcblk0 present after ${i}s, all but $UDN set read-only"
  cat /sys/kernel/debug/mmc0/ios 2>/dev/null | logf "ios:"
  for f in name cid csd date fwrev manfid oemid; do log "mmc0 $f=$(cat /sys/class/mmc_host/mmc0/mmc0:*/$f 2>/dev/null)"; done
  grep mmcblk /proc/partitions | logf "part:"
  dd if=/dev/mmcblk0 bs=512 skip=1 count=1 2>/dev/null | hexdump -C | head -2 | logf "lba1:"
  UD=$(grep -l "PARTNAME=userdata" /sys/class/block/mmcblk0p*/uevent 2>/dev/null | head -1)
  if [ -n "$UD" ]; then
    D=/dev/$(basename $(dirname $UD))
    log "userdata is $D"
    if mount -t ext4 -o rw,noatime $D /mnt || mount -t ext4 -o ro,noload $D /mnt; then
      log "userdata mounted: $(grep " /mnt " /proc/mounts)"
      ls /mnt/linux/debian | tr '\n' ' ' | logf "debian rootfs:"
      cat /mnt/linux/debian/etc/os-release 2>/dev/null | head -2 | logf "os-release:"
      cat /mnt/linux/debian/etc/hostname 2>/dev/null | logf "hostname:"
    else
      log "mount userdata FAILED"
    fi
  else
    log "no userdata partition found"
  fi
  dmesg | grep -iE "mmc|msdc" | tail -20 | logf "dmesg-mmc:"
else
  log "NO mmcblk0 after 20s"
  dmesg | grep -iE "mmc|msdc" | tail -30 | logf "dmesg-mmc:"
fi

# --- USB gadget networking ---
i=0; while ! ip link show usb0 >/dev/null 2>&1 && [ $i -lt 10 ]; do sleep 1; i=$((i+1)); done
if ip link show usb0 >/dev/null 2>&1; then
  ifconfig lo 127.0.0.1 up
  ifconfig usb0 192.168.77.2 netmask 255.255.255.0 up
  ( while :; do telnetd -F -l /bin/sh -p 23 2>&1 | logf "telnetd:"; sleep 2; done ) &
  LOOPS="$!"
  ( while :; do nc -l -p 24 -e /bin/sh 2>&1 | logf "nc:"; sleep 2; done ) &
  LOOPS="$LOOPS $!"
  log "usb0 up 192.168.77.2, telnetd on :23"
else
  log "NO usb0"
fi
dmesg | grep -iE "mtu3|ssusb|tphy|gadget|usb" | tail -25 | logf "dmesg-usb:"

# --- USB PHY fix-ups, cumulative, as stock mu3phy/mt6755 phy_init_soc does; stop once the host talks to us ---
pw_idle() { n=0; while [ $(( ($(devmem 0x1000D0A4 32) >> 16) & 7 )) -ne 0 ] && [ $n -lt 1000 ]; do n=$((n+1)); done; }
pw_w() { pw_idle; devmem 0x1000D0A0 32 $(( (1<<31) | (($1>>1)<<16) | $2 )); pw_idle; }
pw_r() { pw_idle; devmem 0x1000D0A0 32 $(( ($1>>1)<<16 )); n=0; while [ $(( ($(devmem 0x1000D0A4 32) >> 16) & 7 )) -ne 6 ] && [ $n -lt 1000 ]; do n=$((n+1)); done; v=$(( $(devmem 0x1000D0A4 32) & 0xffff )); devmem 0x1000D0A8 32 1; printf 0x%04x $v; }
rmw() { v=$(( ($(devmem $1 32) & ~$2) | $3 )); devmem $1 32 $v; }
UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
udc_state() { cat /sys/class/udc/$UDC/state 2>/dev/null || echo none; }
reconnect() { echo disconnect > /sys/class/udc/$UDC/soft_connect 2>/dev/null; sleep 1; echo connect > /sys/class/udc/$UDC/soft_connect 2>/dev/null; sleep 6; }
dumpu() { echo "acr6=$(devmem 0x11290818 32) acr4=$(devmem 0x11290820 32) dtm0=$(devmem 0x11290868 32) dtm1=$(devmem 0x1129086c 32) misc=$(devmem 0x11271c84 32) pm=$(devmem 0x11273404 32) u2c=$(devmem 0x11280750 32) vusb33=$(pw_r 0x0a86)"; }
U0=$(dumpu); log "usb regs before: $U0 udc=$UDC state=$(udc_state)"
# only worth trying with a computer on the port: VBUS present (PMIC CHRDET) and not host mode
# (keyboard on an OTG adapter, VBUS from ourselves); else each step just waits 7 s
usb_peer() {
  grep -q "chrdet=1" /sys/kernel/mt6755_pmic/charging 2>/dev/null || return 1
  ! grep -qx host /sys/class/usb_role/*/role 2>/dev/null
}
usb_peer || log "usb: no computer on the port ($(cat /sys/kernel/mt6755_pmic/charging 2>/dev/null), role $(cat /sys/class/usb_role/*/role 2>/dev/null)), PHY steps skipped"
STEP=0
for st in 3 4 5; do
  usb_peer || break
  [ "$(udc_state)" != "not attached" ] && [ "$(udc_state)" != "none" ] && break
  case $st in
    1) v=$(pw_r 0x0a86); pw_w 0x0a86 $(( v | 2 )); sleep 1 ;;           # MT6353 VUSB33 LDO on
    2) rmw 0x11290818 $(( (1<<31) | (1<<23) )) $(( 1<<20 )) ;;         # ISO_EN off, BC11 off, OTG_VBUSCMP on
    3) rmw 0x1129086c $(( 1<<4 )) $(( (1<<13)|(1<<12)|(1<<11)|(1<<10)|(1<<9)|(1<<5)|(1<<3)|(1<<2)|(1<<1) )) ;;  # force vbus/session/device
    4) rmw 0x11271c84 0 3 ;;                                           # MAC: VBUS_FRC_EN|VBUS_ON
    5) rmw 0x11290868 $(( 1<<18 )) 0 ;;                                # release FORCE_SUSPENDM
  esac
  STEP=$st; reconnect
  log "usb step $st -> state=$(udc_state) $(dumpu)"
  say "USB step $st state=$(udc_state)"
done
U1=$(dumpu)
i=0; while ! ip link show usb0 >/dev/null 2>&1 && [ $i -lt 5 ]; do sleep 1; i=$((i+1)); done
ifconfig usb0 192.168.77.2 netmask 255.255.255.0 up 2>/dev/null
ifconfig lo 127.0.0.1 up
dmesg | grep -iE "error|fail|warn" | tail -20 | logf "dmesg-err:"

# one-line summary, repeated with every heartbeat so it is visible on screen
MMC=$( [ -b /dev/mmcblk0 ] && echo "mmc OK $(($(cat /sys/class/block/mmcblk0/size)/2048))MB" || echo "mmc NONE")
MNT=$(grep -q " /mnt " /proc/mounts && echo "userdata ro-mounted, debian:$( [ -d /mnt/linux/debian/etc ] && echo yes || echo no)" || echo "userdata not mounted")
NET=$(ip link show usb0 >/dev/null 2>&1 && echo "usb0 up" || echo "usb0 MISSING")
REGS="sts1=$(devmem 0x11280710 32) sts2=$(devmem 0x11280714 32) cfg7=$(devmem 0x100000b0 32) ista2=$(devmem 0x100010ac 32) ista0=$(devmem 0x10001090 32)"
SUM="$MMC | $NET | udc=$(udc_state) step=$STEP | before: $U0 | after: $U1"
log "SUMMARY $SUM"

# --- switch into Debian ---
DEB=/mnt/linux/debian
if grep -q " /mnt ext4 rw" /proc/mounts && [ -x $DEB/bin/bash ] && [ ! -e /mnt/linux/mainline-stay ]; then
  log "switching root to $DEB"
  say "MAINLINE: switching to Debian..."
  mkdir -p /newroot
  mount --bind $DEB /newroot
  mount -t tmpfs -o mode=755,size=64m tmpfs /newroot/run
  cp /bin/busybox /newroot/run/busybox
  cp /tablet-init /newroot/run/tablet-init; chmod 755 /newroot/run/tablet-init
  echo "$SUM" > /newroot/run/mainline-summary
  kill $LOOPS 2>/dev/null; sleep 0.2; killall telnetd nc 2>/dev/null   # respawn loops first, else they spin without busybox
  # systemd if installed (and not vetoed by /data/linux/mainline-simple-init), else the minimal bash init
  INIT=tablet-init
  [ -x /newroot/lib/systemd/systemd ] && [ ! -e /mnt/linux/mainline-simple-init ] && INIT=systemd
  # Hand the hardware watchdog to systemd (RuntimeWatchdogSec in system.conf.d/watchdog.conf):
  # busybox's feeder would otherwise keep running under systemd and keep a hung userspace alive.
  # SIGKILL, not SIGTERM: on SIGTERM busybox does the magic close and stops the timer; killed,
  # the close is "unexpected" ("watchdog did not stop!"), the timer keeps running with its 31 s
  # timeout, and systemd (which opens the device about 2 s later) takes over without a gap.
  # The minimal-init and stay-in-initramfs paths keep the busybox feeder. Before the mount
  # --move below: killall/pidof need /proc, log needs /dev/kmsg.
  if [ $INIT = systemd ] && [ -e /newroot/etc/systemd/system.conf.d/watchdog.conf ]; then
    killall -KILL watchdog 2>/dev/null
    i=0; while pidof watchdog >/dev/null && [ $i -lt 20 ]; do sleep 0.1; i=$((i+1)); done  # else EBUSY
    pidof watchdog >/dev/null && log "watchdog: busybox feeder did not exit" || log "watchdog handed over to systemd"
  fi
  log "exec $INIT"
  for m in dev proc sys; do mount --move /$m /newroot/$m; done
  [ $INIT = systemd ] && exec switch_root /newroot /lib/systemd/systemd --log-target=kmsg
  exec switch_root /newroot /bin/bash /run/tablet-init
fi
log "staying in initramfs"

# --- stay up until /tmp/stop (a reboot ends in BROM/power-off anyway) ---
END=$(( $(cut -d. -f1 /proc/uptime) + 864000 ))
while [ "$(cut -d. -f1 /proc/uptime)" -lt "$END" ]; do
  [ -f /tmp/extend_min ] && { END=$((END + 60 * $(cat /tmp/extend_min))); rm /tmp/extend_min; }
  [ -f /tmp/stop ] && break
  sleep 10
  log "alive up=$(cut -d' ' -f1 /proc/uptime)"
  echo -e "\rMAINLINE up $(cut -d' ' -f1 /proc/uptime)s: $SUM" > /dev/tty0
done
log "rebooting"
sync
reboot -f

#!/bin/bash
# PID 1 inside Debian on the mainline kernel (no init system installed yet).
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
log() { echo "<5>DEBIAN-INIT: $*" > /dev/kmsg; }
log "PID $$ in Debian root, kernel $(uname -r)"
mountpoint -q /dev/pts || { mkdir -p /dev/pts; mount -t devpts -o gid=5,mode=620 devpts /dev/pts; }
mkdir -p /dev/shm && mount -t tmpfs tmpfs /dev/shm
mount -o remount,rw / 2>/dev/null
[ -L /run/lock ] || mkdir -p /run/lock
hostname "$(cat /etc/hostname)"
ip link set lo up
ip addr replace 192.168.77.2/24 dev usb0; ip link set usb0 up
ip route replace default via 192.168.77.1 dev usb0
mkdir -p /run/sshd && /usr/sbin/sshd -E /run/sshd.log && log "sshd started"
/run/busybox telnetd -p 23 -l /bin/bash && log "telnetd fallback on :23"
log "up: $(nproc) cpus, $(grep MemTotal /proc/meminfo)"
SUM="$(cat /run/mainline-summary)"
n=0
while :; do
  while wait -n 2>/dev/null; do :; done   # reap
  sleep 10; n=$((n+10))
  [ -f /run/poweroff ] && { log "poweroff requested"; sync; /run/busybox poweroff -f; }
  echo -e "\rDEBIAN on $(uname -r) up ${n}s, $(nproc) cpus, ssh 192.168.77.2 | $SUM" > /dev/tty0
done

#!/bin/sh
# Minimal Android chroot for MediaTek's mnld: stock system/vendor/nvdata read-only.
set -e
R=/srv/android
mkdir -p $R/system $R/vendor $R/nvdata $R/dev $R/proc $R/sys $R/data/misc/gps $R/data/agps_supl $R/data/gps_mnl
for p in system vendor nvdata; do mountpoint -q $R/$p || mount -o ro /dev/disk/by-partlabel/$p $R/$p; done
mountpoint -q $R/vendor/nvdata || mount --bind $R/nvdata $R/vendor/nvdata
for m in dev proc sys; do mountpoint -q $R/$m || mount --rbind /$m $R/$m; done
mkdir -p $R/dev/socket

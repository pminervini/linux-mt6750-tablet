#!/bin/sh
# Build /dev/__properties__ for the mnld chroot from the stock build.prop files plus the
# properties Android's init/WMT loader would set at runtime (needs android-chroot.sh first).
R=/srv/android; T=$(mktemp)
cat $R/system/build.prop $R/vendor/build.prop $R/vendor/default.prop | grep -E "^[a-zA-Z0-9._-]+=" |
  awk -F= 'length($2)<91 && length($1)<90' > $T
cat >> $T <<P
ro.hardware=mt6755
ro.boot.hardware=mt6755
gps.clock.type=21
persist.mtk.wcn.combo.chipid=0x6755
service.nvram_init=Ready
vendor.service.nvram_init=Ready
P
python3 - "$T" <<'PY'
import sys, subprocess
d = {}
for l in open(sys.argv[1]):
    l = l.rstrip("\n")
    if "=" in l:
        k, v = l.split("=", 1); d[k] = v
subprocess.run(["python3", "/root/gps/mkprops.py", "/dev/__properties__"] + [f"{k}={v}" for k, v in d.items()], check=True)
PY
chmod 644 /dev/__properties__; rm -f $T

#!/system/bin/sh
# Magisk service.d (Android side): mainline Debian is the power-on default, Android boots
# started with `tablet-android` (BCB "android-once") are one-shot -> re-arm "boot-recovery".
# A BCB of "mainline-trying" means mainline failed to boot: leave Android as the default.
P=/dev/block/by-name/para
[ -b $P ] || P=$(ls /dev/block/platform/*/by-name/para /dev/block/platform/*/*/by-name/para 2>/dev/null | head -1)
until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 5; done
c=$(dd if=$P bs=32 count=1 2>/dev/null | tr -d '\000')
if [ "$c" = android-once ]; then
  # build the 32-byte field in a file: dd on a pipe may write a short block (toybox dd has no
  # iflag=fullblock/conv=; a block device is never truncated)
  T=/dev/mainline-bcb.$$
  { printf 'boot-recovery'; head -c 19 /dev/zero; } > $T
  dd if=$T of=$P bs=32 count=1 2>/dev/null
  rm -f $T
  sync
  log -t mainline-default "BCB android-once -> boot-recovery (next power-on: mainline)"
else
  log -t mainline-default "BCB '$c' left alone"
fi

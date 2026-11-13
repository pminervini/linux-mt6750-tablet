#!/bin/sh
# OFFLINE ONLY. Writes only the owned evidence/build tree and scratch tests.
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KERNEL=${KERNEL_SOURCE:?Set KERNEL_SOURCE to the matching Linux checkout}
BASE=${KERNEL_OUTPUT:?Set KERNEL_OUTPUT to its prepared output}
EVIDENCE=${BUILD_OUTPUT:?Set BUILD_OUTPUT to a separate owned build directory}
PWRKEY_SNAPSHOT=${PWRKEY_SNAPSHOT:?Set PWRKEY_SNAPSHOT to the matching controller snapshot}
VOLUME_TESTS=${VOLUME_TESTS:?Set VOLUME_TESTS to the existing button/volume test suite}
OUT=$EVIDENCE/kernel-out
mkdir -p "$EVIDENCE"
if [ ! -e "$OUT" ]; then cp -a --reflink=auto "$BASE" "$OUT"; fi
# SOUND must be modular too: otherwise sound_class is an unresolved built-in.
"$KERNEL/scripts/config" --file "$OUT/.config" --module SOUND --module SND \
    --module SND_PCM --module SND_TIMER --module SND_SOC
make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    LOCALVERSION= -j2 olddefconfig > "$EVIDENCE/kernel-config.log" 2>&1
make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    LOCALVERSION= -j2 modules_prepare > "$EVIDENCE/kernel-prepare.log" 2>&1
# Linux 7.3 supports separate external-module output (MO). Kernel source is read-only.
make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    LOCALVERSION= -j2 M="$KERNEL/sound" MO="$EVIDENCE/sound-modules" \
    modules > "$EVIDENCE/sound-modules-build.log" 2>&1
make -C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    LOCALVERSION= -j2 M="$SOURCE" MO="$EVIDENCE/module-build" \
    KBUILD_EXTRA_SYMBOLS="$EVIDENCE/sound-modules/Module.symvers" \
    modules > "$EVIDENCE/module-build.log" 2>&1
python3 -B "$SOURCE/test_driver_offline.py" "$EVIDENCE/host-tests" "$KERNEL" > "$EVIDENCE/driver-tests.log" 2>&1
python3 -B "$SOURCE/test_audio.py" > "$EVIDENCE/helper-tests.log" 2>&1
mkdir -p "$EVIDENCE/volume-regression"
cp "$SOURCE/tablet-audio" "$EVIDENCE/volume-regression/tablet-audio"
cp "$PWRKEY_SNAPSHOT" "$EVIDENCE/volume-regression/tablet-pwrkey"
cp "$VOLUME_TESTS" "$EVIDENCE/volume-regression/test_volume.py"
python3 -B "$EVIDENCE/volume-regression/test_volume.py" > "$EVIDENCE/volume-tests.log" 2>&1
printf '%s\n' 'OFFLINE build and tests passed. No modules loaded or device APIs called.'

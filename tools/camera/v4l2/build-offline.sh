#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# OFFLINE ONLY. No target connection, device access, module load or installation.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC=${KERNEL_SOURCE:?Set KERNEL_SOURCE to the matching Linux checkout}
BASE=${KERNEL_OUTPUT:?Set KERNEL_OUTPUT to its prepared output}
E=${BUILD_OUTPUT:?Set BUILD_OUTPUT to a separate owned build directory}
LEGACY=${LEGACY_CAMERA_SNAPSHOT:?Set LEGACY_CAMERA_SNAPSHOT to the preserved unguarded tablet-camera}
OUT=$E/out
export PYTHONDONTWRITEBYTECODE=1
[ "$(git -C "$SRC" rev-parse HEAD)" = 976d4857a991174852ef3423262619723781d86e ]
[ -z "$(git -C "$SRC" status --porcelain)" ]
python3 -B -c 'import hashlib,sys; assert hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest() == "d18cfea64d6f7fcbaea7d800ca5473b6c612a6051a3107f6c6e862d887ff0a45"' "$BASE/.config"
mkdir -p "$E/module"
if [ ! -d "$OUT" ]; then
    mkdir "$OUT"
    cp -a "$BASE/." "$OUT/"
fi
# A separate root Kconfig selects hidden vb2 symbols without changing Linux.
python3 -B -c 'from pathlib import Path; import sys; s=Path(sys.argv[1]).read_text(); Path(sys.argv[2]).write_text(s+"\nconfig MT6750_CAMERA_CANDIDATE\n\ttristate\n\tdefault m\n\tselect VIDEOBUF2_VMALLOC\n\tselect VIDEOBUF2_V4L2\n")' "$SRC/Kconfig" "$E/Kconfig"
"$SRC/scripts/config" --file "$OUT/.config" -m MEDIA_SUPPORT -e MEDIA_SUPPORT_FILTER \
    -e MEDIA_CAMERA_SUPPORT -d MEDIA_SUBDRV_AUTOSELECT -d MEDIA_ANALOG_TV_SUPPORT \
    -d MEDIA_DIGITAL_TV_SUPPORT -d MEDIA_RADIO_SUPPORT -d MEDIA_SDR_SUPPORT \
    -d MEDIA_TEST_SUPPORT -d MEDIA_PLATFORM_SUPPORT -m VIDEO_DEV -e MEDIA_CONTROLLER \
    -e VIDEO_V4L2_SUBDEV_API
make_kernel() {
    make -C "$SRC" O="$OUT" KBUILD_KCONFIG="$E/Kconfig" ARCH=arm64 \
        CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION= -j2 "$@"
}
make_kernel olddefconfig > "$E/configure.log" 2>&1
make_kernel vmlinux Image.gz dtbs modules > "$E/kernel-build.log" 2>&1
for name in Kbuild mt6750-camera.c mtk-capture.h; do
    cp "$HERE/$name" "$E/module/$name"
done
python3 -B "$HERE/generate_tables.py" --output "$E/module/board-tables.h"
make_kernel M="$E/module" W=1 modules > "$E/module-build.log" 2>&1
"$SRC/scripts/diffconfig" "$BASE/.config" "$OUT/.config" > "$E/config-diff.txt"
python3 -B "$HERE/run_tests.py" --kernel-source "$SRC" --legacy-helper "$LEGACY" --output "$E/tests" > "$E/tests.log" 2>&1
printf '%s\n' "Built $E/module/mt6750_camera.ko and media modules; offline tests passed. NOT deployed."

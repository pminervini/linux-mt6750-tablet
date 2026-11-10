#!/bin/bash
# Build the stage-0 initramfs and the boot image (Android v0, MTK lk) for testing via `recovery`.
# usage: ./mkimage.sh <tag>  -> release/boot-mainline-<tag>.img
set -euo pipefail
cd "$(dirname "$0")"
TAG=${1:?tag}
R=initramfs/root
rm -rf $R && mkdir -p $R/{bin,proc,sys,dev,pstore,mnt,tmp}
cp dl/busybox/x/usr/bin/busybox $R/bin/busybox
install -m 755 initramfs/init.sh $R/init
install -m 755 initramfs/tablet-init.sh $R/tablet-init
(cd $R && find . | cpio -o -H newc --owner=0:0 --quiet | gzip -9) > initramfs/initramfs.cpio.gz
mkdir -p release
python3 -I check-dtb.py out/arch/arm64/boot/dts/mediatek/mt6750-k50v1-tablet.dtb || { echo "DTB REJECTED, not packing"; exit 1; }
# Reuse the stock boot.img header (addresses, page size, "bootopt=64S3,32N2,64N2" cmdline);
# repack.py keeps the base ramdisk, so swap it afterwards.
python3 -I - out/Image.gz-dtb initramfs/initramfs.cpio.gz ../images/boot-stock.img release/boot-mainline-$TAG.img <<'EOF'
import struct, hashlib, sys
kern, rd, base, out = sys.argv[1:5]
d = open(base, 'rb').read()
assert d[:8] == b'ANDROID!'
ks, ka, rs, ra, ss, sa, ta, ps = struct.unpack('<8I', d[8:40])
kernel = open(kern, 'rb').read(); ramdisk = open(rd, 'rb').read()
pad = lambda b: b + b'\0' * (-len(b) % ps)
hdr = bytearray(d[:ps])
struct.pack_into('<I', hdr, 8, len(kernel))
struct.pack_into('<I', hdr, 16, len(ramdisk))
struct.pack_into('<I', hdr, 24, 0)  # no second
sha = hashlib.sha1()
for blob in (kernel, ramdisk, b''):
    sha.update(blob); sha.update(struct.pack('<I', len(blob)))
hdr[576:608] = sha.digest().ljust(32, b'\0')
img = bytes(hdr) + pad(kernel) + pad(ramdisk)
assert len(img) <= 16 << 20, f'image too big: {len(img)}'
open(out, 'wb').write(img)
print(f'{out}: kernel {len(kernel)} ramdisk {len(ramdisk)} total {len(img)} cmdline {hdr[64:576].split(bytes(1))[0]}')
EOF
sha256sum release/boot-mainline-$TAG.img

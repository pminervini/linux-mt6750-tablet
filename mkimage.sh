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
# Pack exactly the DTB that passes the check: rebuild Image.gz + DTB here (build.sh does the same,
# but a DTB rebuilt on its own would otherwise be checked while an older copy got shipped).
DTB=out/arch/arm64/boot/dts/mediatek/mt6750-k50v1-tablet.dtb
python3 -I check-dtb.py $DTB || { echo "DTB REJECTED, not packing"; exit 1; }
cat out/arch/arm64/boot/Image.gz $DTB > out/Image.gz-dtb
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
# and the DTB inside the image is the one that was checked
python3 -I - release/boot-mainline-$TAG.img $DTB <<'EOF' || { rm -f release/boot-mainline-$TAG.img; echo "PACKED DTB MISMATCH, image removed"; exit 1; }
import struct, sys, zlib
d = open(sys.argv[1], 'rb').read(); dtb = open(sys.argv[2], 'rb').read()
ksz, page = struct.unpack_from('<I', d, 8)[0], struct.unpack_from('<I', d, 36)[0]
z = zlib.decompressobj(31); z.decompress(d[page:page + ksz])   # Image.gz; what follows it is the DTB
sys.exit(0 if z.unused_data == dtb else 1)
EOF
sha256sum release/boot-mainline-$TAG.img

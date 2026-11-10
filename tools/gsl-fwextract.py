#!/usr/bin/python3
"""Cut the touchscreen firmware (Silead GSL3670, config entry 0 as the stock driver uses) out of the
tablet's stock Android boot image, for the mainline silead driver (same {u32 offset, u32 value}
record format). Nothing is redistributed: the firmware comes from the device's own boot partition.

usage: gsl-fwextract.py backup/parts/boot.bin /lib/firmware/silead/gsl3670-k50v1.fw

Stock kernel layout (found by disassembling gsl_sw_init with the kernel's own /proc/kallsyms):
config table at VA 0xffffffc0012025d8, 128-byte entries {u64 fw, u32 count, ...}; kernel VA base
0xffffffc000080000 = offset 0 of the decompressed Image.
"""
import hashlib, struct, sys, zlib

SHA256 = "da4c76356b67af0f9376f08b8f253681886531fc1ca1a61cd9a3be13e505db71"
BASE, TABLE = 0xffffffc000080000, 0xffffffc0012025d8

boot = open(sys.argv[1], "rb").read()
i = boot.find(b"\x1f\x8b\x08")
img = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(boot[i:])
fw, n = struct.unpack_from("<QI", img, TABLE - BASE)
data = img[fw - BASE:fw - BASE + 8 * n]
if hashlib.sha256(data).hexdigest() != SHA256:
    sys.exit("unexpected firmware (different stock kernel?): sha256 %s" % hashlib.sha256(data).hexdigest())
open(sys.argv[2], "wb").write(data)
print("%d records (%d bytes) -> %s" % (n, len(data), sys.argv[2]))

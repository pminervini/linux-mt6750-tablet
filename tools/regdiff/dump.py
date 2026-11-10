#!/usr/bin/python3
"""Dump SoC pad/GPIO/clock and MT6353 PMIC registers (works on stock Android chroot and mainline).
usage: dump.py > file ; then diff two dumps."""
import mmap, os, struct, glob, time

MMIO = [(0x10005000, 0x1000, "gpio"), (0x10002000, 0xc00, "iocfg"), (0x10001000, 0x200, "infra_ao"),
        (0x11007000, 0x100, "i2c0"), (0x11008000, 0x100, "i2c1")]
fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
for base, size, name in MMIO:
    m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
    for o in range(0, size, 4):
        print(f"{name} {base + o:08x} {struct.unpack_from('<I', m, o)[0]:08x}")

def pmic_rd_factory():
    p = "/sys/kernel/mt6755_pmic/reg"
    if os.path.exists(p):
        def rd(a):
            open(p, "w").write(f"0x{a:x}"); return int(open(p).read().split()[-1], 16)
        return rd
    for p in glob.glob("/sys/devices/platform/*/pmic_access") + glob.glob("/sys/devices/*/*/pmic_access"):
        def rd(a, p=p):
            open(p, "w").write(f"{a:x}"); return int(open(p).read().strip().split()[-1], 16)
        return rd
rd = pmic_rd_factory()
if rd:
    for a in range(0, 0x0e00, 2):
        try:
            print(f"pmic {a:04x} {rd(a):04x}")
        except Exception as e:
            print(f"pmic {a:04x} ERR")

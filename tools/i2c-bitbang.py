#!/usr/bin/python3
"""Bit-banged I2C probe on MT6755 GPIOs (bypasses the I2C controller).

usage: i2c-bitbang.py <sda_gpio> <scl_gpio> <addr>...   e.g. 92 93 0x3e 0x5d
Open-drain is emulated with the direction register (DO = 0; DIR=1 drives low,
DIR=0 releases to the pull-up). Pins are switched to GPIO mode for the probe and
restored afterwards. Needs root (/dev/mem, IO range only).
"""
import mmap, os, struct, sys, time

BASE = 0x10005000


class Gpio:
    def __init__(self):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)

    def rd(self, off):
        return struct.unpack_from("<I", self.m, off)[0]

    def wr(self, off, v):
        struct.pack_into("<I", self.m, off, v)

    def mode_loc(self, p):
        i = p % 10
        return 0x300 + (p // 10) * 0x10, (i * 3 if i < 5 else 16 + (i - 5) * 3)

    def get_mode(self, p):
        reg, sh = self.mode_loc(p)
        return (self.rd(reg) >> sh) & 7

    def set_mode(self, p, m):
        reg, sh = self.mode_loc(p)
        self.wr(reg, (self.rd(reg) & ~(7 << sh)) | (m << sh))

    def bank(self, p):
        return (p // 32) * 0x10, 1 << (p % 32)

    def low(self, p):       # drive low: DO=0, DIR=out
        b, bit = self.bank(p)
        self.wr(0x100 + b + 8, bit)
        self.wr(0x000 + b + 4, bit)

    def release(self, p):   # input (pull-up takes the line high)
        b, bit = self.bank(p)
        self.wr(0x000 + b + 8, bit)

    def get(self, p):
        b, bit = self.bank(p)
        return 1 if self.rd(0x200 + b) & bit else 0


def main():
    sda, scl = int(sys.argv[1]), int(sys.argv[2])
    g = Gpio()
    saved = (g.get_mode(sda), g.get_mode(scl))
    d = lambda: time.sleep(0.00005)
    try:
        g.release(sda); g.release(scl)
        g.set_mode(sda, 0); g.set_mode(scl, 0)
        print(f"idle SDA={g.get(sda)} SCL={g.get(scl)} (modes were {saved})")
        for a in sys.argv[3:]:
            addr = int(a, 0)
            # START
            g.release(sda); g.release(scl); d(); g.low(sda); d(); g.low(scl); d()
            byte = (addr << 1) | 1
            for i in range(7, -1, -1):
                (g.release if byte >> i & 1 else g.low)(sda); d()
                g.release(scl); d(); g.low(scl); d()
            g.release(sda); d(); g.release(scl); d()
            ack = g.get(sda) == 0
            g.low(scl); d()
            # STOP
            g.low(sda); d(); g.release(scl); d(); g.release(sda); d()
            print(f"0x{addr:02x}: {'ACK' if ack else 'nack'}")
    finally:
        g.set_mode(sda, saved[0]); g.set_mode(scl, saved[1])
        g.release(sda); g.release(scl)


main()

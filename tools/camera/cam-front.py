#!/usr/bin/python3
"""Front camera (GC2365, 1 lane on CSI1 -> SENINF2) bring-up test. cam-up.py up must have run
(LDOs, ISP domain, clocks). usage: cam-front.py SENINF2_CFG INIT_LIST"""
import sys
exec(open("/tmp/cam-up.py").read().split('{"up": up')[0])
sen, ana = Blk(0x15008000), Blk(0x10218000, 0x100)
gpio_out(107, 0)                                          # rear sensor off
sen.wr(0x604, 0x00010001); sen.wr(0x600, 0xa0000001)     # TG2: MCLK on CMMCLK1 (24 MHz)
gpio_out(108, 0); time.sleep(0.005)                       # GC2365 PWDN (active high) released
gpio_out(110, 0); time.sleep(0.002); gpio_out(110, 1); time.sleep(0.02)
ana.wr(0x000, 0x9009)                                     # data lane 0 on (as CSI0's lanes)
ana.wr(0x010, 0xa009)                                     # clock lane on
ana.wr(0x020, (ana.rd(0x020) & ~0xff) | 0x03)
ana.wr(0x024, ana.rd(0x024) | 1)
for l in open(sys.argv[1]):
    o, v = (int(x, 16) for x in l.split())
    sen.wr(o, v)


def w8(a, r, v):
    start(); ok = wbyte(a << 1) and wbyte(r) and wbyte(v); stop(); return ok


def r8(a, r):
    start()
    if not wbyte(a << 1):
        stop(); return None
    wbyte(r); start(); wbyte((a << 1) | 1); v = rbyte(True); stop(); return v


saved = {p: (gpio.rd(mode_loc(p)[0]) >> mode_loc(p)[1]) & 7 for p in (SCL, SDA)}
for p in (SCL, SDA):
    set_mode(p, 0)
line(SCL, 1); line(SDA, 1)
nack = 0
try:
    print("id", r8(0x37, 0xf0), r8(0x37, 0xf1))
    for l in open(sys.argv[2]):
        a, b = l.split()
        if a == "delay":
            time.sleep(int(b) / 1000)
        elif not w8(0x37, int(a, 16), int(b, 16)):
            nack += 1
    print("init done, NACKs", nack)
finally:
    for p in (SCL, SDA):
        set_mode(p, saved[p])
for i in range(3):
    print("seninf2 live:", " ".join(f"{o:#x}={sen.rd(o):08x}" for o in (0x4d0, 0x4d8, 0x548, 0x550, 0x7d0)),
          "| seninf1 0x3d0", hex(sen.rd(0x3d0)))
    time.sleep(0.5)

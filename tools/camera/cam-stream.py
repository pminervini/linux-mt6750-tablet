#!/usr/bin/python3
"""Replay test: Android's CSI0 analog + SENINF settings, the stock OV8856 init + preview mode over
bit-banged I2C, then watch SENINF's live counters. Run cam-up.py up first.
usage: cam-stream.py CSI0ANA_CFG SENINF_CFG INIT_LIST PREVIEW_LIST"""
import sys
exec(open("/tmp/cam-up.py").read().split('{"up": up')[0])


def load(f):
    return [tuple(int(x, 16) for x in l.split()) for l in open(f) if l.strip()]


def write16(addr, reg, val):
    start()
    ok = wbyte(addr << 1) and wbyte(reg >> 8) and wbyte(reg & 0xff) and wbyte(val)
    stop()
    return ok


ana, sen = Blk(0x10217000, 0x100), Blk(0x15008000)
for o, v in load(sys.argv[1]):
    ana.wr(o, v)
for o, v in load(sys.argv[2]):
    sen.wr(o, v)
saved = {p: (gpio.rd(mode_loc(p)[0]) >> mode_loc(p)[1]) & 7 for p in (SCL, SDA)}
for p in (SCL, SDA):
    set_mode(p, 0)
line(SCL, 1); line(SDA, 1)
fails = 0
try:
    for f in sys.argv[3:5]:
        for reg, val in load(f):
            if not write16(0x36, reg, val):
                fails += 1
            if reg == 0x0103:
                time.sleep(0.01)      # software reset
        print(f"{f}: done, {fails} NACKs so far")
    print("0x0100 =", read16(0x36, 0x0100, 1), " 0x3018 =", read16(0x36, 0x3018, 1))
finally:
    for p in (SCL, SDA):
        set_mode(p, saved[p])
for i in range(3):
    print("seninf live:", " ".join(f"{o:#x}={sen.rd(o):08x}" for o in (0xd0, 0xd8, 0xe0, 0x148, 0x150, 0x3d0)))
    time.sleep(0.5)

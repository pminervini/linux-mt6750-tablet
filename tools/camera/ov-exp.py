#!/usr/bin/python3
"""ov-exp.py LINES GAIN: set OV8856 exposure (in lines, 0x3500-0x3502 hold lines<<4) and analog
gain (0x3508/0x3509, 0x80 = 1x) over bit-banged I2C2."""
import sys
exec(open("/tmp/cam-up.py").read().split('{"up": up')[0])
lines, gain = int(sys.argv[1]), int(sys.argv[2], 0)


def write16(addr, reg, val):
    start()
    ok = wbyte(addr << 1) and wbyte(reg >> 8) and wbyte(reg & 0xff) and wbyte(val)
    stop()
    return ok


saved = {p: (gpio.rd(mode_loc(p)[0]) >> mode_loc(p)[1]) & 7 for p in (SCL, SDA)}
for p in (SCL, SDA):
    set_mode(p, 0)
line(SCL, 1); line(SDA, 1)
try:
    e = lines << 4
    for r, v in ((0x3500, (e >> 16) & 0xff), (0x3501, (e >> 8) & 0xff), (0x3502, e & 0xff),
                 (0x3508, (gain >> 8) & 0xff), (0x3509, gain & 0xff)):
        write16(0x36, r, v)
    print("exposure", read16(0x36, 0x3500, 3), "gain", read16(0x36, 0x3508, 2))
finally:
    for p in (SCL, SDA):
        set_mode(p, saved[p])

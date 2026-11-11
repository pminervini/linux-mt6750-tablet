#!/usr/bin/python3
"""Rear camera (OV8856) power-up on mainline by hand, as stock Android does it, and a chip-ID
read over bit-banged I2C. Needs root (/dev/mem, /sys/kernel/mt6755_pmic/reg).

  cam-up.py up      LDOs, ISP power domain, camera clocks, sensor MCLK, PDN/RST high
  cam-up.py id      read OV8856 chip ID (0x300a..0x300c = 00 88 5a) at 7-bit 0x10 on I2C2 pins
  cam-up.py down    reverse (MCLK off, pins low, LDOs off; ISP domain left on)

Board facts (stock DT / Android register dumps, tools/regdiff/and-cam-*):
  supplies  MT6353 VCAMIO 1.8 V (0xA54 b1, VOSEL 0xAD6[9:8]=3), VCAMA 2.8 V (0xA50 b1,
            VOSEL 0xAA0[9:8]=3), VCAMD 1.22 V (0xA58 b1, VOSEL 0xACA[10:8]=3)
  pins      PDN GPIO107, RST GPIO110 (shared with the front camera), MCLK GPIO161 (CMMCLK0,
            mode 1), I2C2 SCL GPIO112 / SDA GPIO113 (mode 1)
  clocks    camtg_sel (topck 0x60[1:0]) = univpll_d26 48 MHz; SENINF TG1 divides it by 2
  ISP power stock spm_mtcmos_ctrl_isp(): SPM 0x308, status bit 5
"""
import mmap, os, struct, sys, time

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)


class Blk:
    def __init__(self, base, size=0x1000):
        self.m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)

    def rd(self, o):
        return struct.unpack_from("<I", self.m, o)[0]

    def wr(self, o, v):
        struct.pack_into("<I", self.m, o, v & 0xffffffff)

    def setb(self, o, b):
        self.wr(o, self.rd(o) | b)

    def clrb(self, o, b):
        self.wr(o, self.rd(o) & ~b)


PMIC = "/sys/kernel/mt6755_pmic/reg"


def prd(a):
    open(PMIC, "w").write(f"{a:#x}")
    return int(open(PMIC).read().split()[1], 16)


def pwr(a, v):
    open(PMIC, "w").write(f"{a:#x} {v:#x}")


def pfield(a, mask, val):
    pwr(a, (prd(a) & ~mask) | val)


spm, top, gpio = Blk(0x10006000), Blk(0x10000000), Blk(0x10005000)
PDN, RST, SCL, SDA = 107, 110, 112, 113


def gpio_out(p, v):
    bank = (p // 32) * 0x10
    bit = 1 << (p % 32)
    gpio.wr(0x000 + bank + (4 if True else 8), bit)          # DIR set: output
    gpio.wr(0x100 + bank + (4 if v else 8), bit)             # DO set / clr


def isp_on():
    spm.wr(0x000, (0xb16 << 16) | 1)                         # SPM register write enable
    c = 0x308
    spm.setb(c, 1 << 2)
    spm.setb(c, 1 << 3)
    for _ in range(1000):
        if spm.rd(0x180) & 0x20 and spm.rd(0x184) & 0x20:
            break
        time.sleep(0.001)
    else:
        sys.exit(f"ISP power ack timeout: con {spm.rd(c):#x} sta {spm.rd(0x180):#x}/{spm.rd(0x184):#x}")
    spm.clrb(c, 1 << 4)                                      # CLK_DIS
    spm.clrb(c, 1 << 1)                                      # ISO
    spm.setb(c, 1 << 0)                                      # RST_B
    spm.clrb(c, 1 << 8)
    spm.clrb(c, 1 << 9)                                      # SRAM_PDN
    for _ in range(1000):
        if not spm.rd(c) & (3 << 12):
            break
        time.sleep(0.001)
    print(f"ISP domain on: con {spm.rd(c):#x}")


def up():
    # supplies: IO, analog, core (OV8856 power-up order), voltages first
    pfield(0xAD6, 0x300, 0x300)
    pfield(0xAA0, 0x300, 0x300)
    pfield(0xACA, 0x700, 0x300)
    for a in (0xA54, 0xA50, 0xA58):
        pfield(a, 0x2, 0x2)
        time.sleep(0.002)
    print("LDOs  vcamio %#06x vcama %#06x vcamd %#06x" % (prd(0xA54), prd(0xA50), prd(0xA58)))
    gpio_out(PDN, 0)
    gpio_out(RST, 0)
    if not spm.rd(0x180) & 0x20:
        isp_on()
    img = Blk(0x15000000, 0x100)
    img.wr(0x8, (1 << 0) | (0x1f << 5))                      # IMG_CG_CLR: LARB2, CAM_SMI..CAM_SV
    print(f"imgsys CG {img.rd(0):#x}  camtg {top.rd(0x60):#x}")
    sen = Blk(0x15008000)
    sen.wr(0x204, 0x00010001)                                # TG1 SEN_CK, as Android (48/2 = 24 MHz)
    sen.wr(0x200, 0xa0000001)                                # TG1 PH_CNT: clock enable, as Android
    print(f"seninf TG1 {sen.rd(0x200):#x} {sen.rd(0x204):#x}")
    time.sleep(0.005)
    gpio_out(PDN, 1)
    time.sleep(0.002)
    gpio_out(RST, 1)
    time.sleep(0.02)
    print("PDN/RST high")


def down():
    gpio_out(RST, 0)
    gpio_out(PDN, 0)
    if spm.rd(0x180) & 0x20:
        Blk(0x15008000).wr(0x200, 0)
    for a in (0xA58, 0xA50, 0xA54):
        pfield(a, 0x2, 0)
    print("down")


# ---- bit-banged I2C on SCL/SDA (open drain through the direction register) ----
def mode_loc(p):
    return 0x300 + (p // 10) * 0x10, (p % 10 % 5) * 3 + (16 if p % 10 >= 5 else 0)


def set_mode(p, m):
    """through the MODE CLR/SET aliases: rewriting the whole register re-muxes its other pins, and
    0x3b0 also holds the connsys F2W_DATA/F2W_CK/WB_RSTB pins (GPIO117-119) - doing that while the
    Wi-Fi radio runs makes its firmware assert (rlm_phy.c #1961) and the CONSYS chip reset"""
    r, s = mode_loc(p)
    gpio.wr(r + 8, 7 << s)
    if m:
        gpio.wr(r + 4, m << s)


def line(p, high):
    bank, bit = (p // 32) * 0x10, 1 << (p % 32)
    gpio.wr(0x100 + bank + 8, bit)                           # DO = 0
    gpio.wr(0x000 + bank + (8 if high else 4), bit)          # release (input) / drive low


def get(p):
    return (gpio.rd(0x200 + (p // 32) * 0x10) >> (p % 32)) & 1


D = 0.00002


def start():
    line(SDA, 1); line(SCL, 1); time.sleep(D); line(SDA, 0); time.sleep(D); line(SCL, 0)


def stop():
    line(SDA, 0); time.sleep(D); line(SCL, 1); time.sleep(D); line(SDA, 1); time.sleep(D)


def wbyte(b):
    for i in range(7, -1, -1):
        line(SDA, (b >> i) & 1); time.sleep(D); line(SCL, 1); time.sleep(D); line(SCL, 0)
    line(SDA, 1); time.sleep(D); line(SCL, 1); time.sleep(D)
    ack = get(SDA) == 0
    line(SCL, 0)
    return ack


def rbyte(last):
    line(SDA, 1)
    v = 0
    for _ in range(8):
        line(SCL, 1); time.sleep(D); v = (v << 1) | get(SDA); line(SCL, 0); time.sleep(D)
    line(SDA, 1 if last else 0); line(SCL, 1); time.sleep(D); line(SCL, 0); line(SDA, 1)
    return v


def read16(addr, reg, n):
    start()
    if not wbyte(addr << 1):
        stop()
        return None
    wbyte(reg >> 8); wbyte(reg & 0xff)
    start()
    wbyte((addr << 1) | 1)
    out = [rbyte(i == n - 1) for i in range(n)]
    stop()
    return out


def chip_id():
    saved = [(p, *mode_loc(p)) for p in (SCL, SDA)]
    old = {p: (gpio.rd(r) >> s) & 7 for p, r, s in saved}
    for p in (SCL, SDA):
        set_mode(p, 0)
    line(SCL, 1); line(SDA, 1)
    try:
        for a in (0x10, 0x36):
            v = read16(a, 0x300a, 3)
            print(f"addr {a:#04x}: " + ("no ACK" if v is None else " ".join(f"{x:02x}" for x in v)))
    finally:
        for p in (SCL, SDA):
            set_mode(p, old[p])


{"up": up, "down": down, "id": chip_id}[sys.argv[1]]()

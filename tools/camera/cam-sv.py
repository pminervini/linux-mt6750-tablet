#!/usr/bin/python3
"""Raw capture experiment through CAMSV (camera side path: TG -> pack -> IMGO DMA), following the
MT8365 ISP3.0 CAMSV driver's sequence (BayLibre, "Add Mediatek ISP3.0" v8), on MT6755 offsets
(CAMSV 0x15009000: top +0, IMGO +0x208, TG +0x400). The sensor must already be streaming into
SENINF1 (cam-up.py up; cam-stream.py ...). Needs the physbuf module loaded.

usage: cam-sv.py MUX W H OUT.raw      MUX = SENINF mux to route SENINF1 into (2, 3 or 4)
"""
import sys
exec(open("/tmp/cam-up.py").read().split('{"up": up')[0])

mux, W, H, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
BPL = W * 10 // 8
phys = int(open("/sys/module/physbuf/parameters/phys").read())
sen, sv = Blk(0x15008000), Blk(0x15009000)
TG, IMGO = 0x400, 0x208

# SENINF: mux N takes SENINF1, with mux 1's settings
mb = 0x120 + (mux - 1) * 0x400
for o in range(0, 0x20, 4):
    sen.wr(mb + o, sen.rd(0x120 + o))
top = sen.rd(0x008)
sen.wr(0x008, (top & ~(0xf << ((mux - 1) * 4))) | (0 << ((mux - 1) * 4)))
print(f"seninf top_mux {top:#x} -> {sen.rd(0x008):#x}, mux{mux} ctrl {sen.rd(mb):#x}")

# CAMSV setup (mtk_camsv_setup + fbc_init + cmos_vf_hw_enable)
sv.wr(0x01c, 0)                                       # FBC off while configuring
sv.wr(TG + 0x10, 0)                                   # SEN_MODE: single pixel per clock (as Android's TG)
sv.wr(TG + 0x18, W << 16)                             # GRAB_PXL: end W, start 0
sv.wr(TG + 0x1c, H << 16)                             # GRAB_LIN
sv.wr(TG + 0x20, 0x1000)                              # PATH_CFG: YUV_U2S_DIS
sv.wr(0x010, 1 << 2); sv.wr(0x010, 0); sv.wr(0x010, 1 << 0)   # SW_RST, then IMGO reset
for _ in range(100):
    if sv.rd(0x010) == 3:
        break
    time.sleep(0.001)
sv.wr(0x010, 0)
sv.wr(0x008, 1 << 10)                                 # INT_EN: PASS1_DON
sv.wr(0x000, (1 << 30) | (1 << 0) | (1 << 2))         # MODULE_EN: DB, TG, PAK
sv.wr(0x004, (1 << 24) | 0x1)                         # FMT_SEL: IMGO bus 16 bit (reset value), TG1 RAW10
sv.wr(0x03c, 0x2)                                     # PAK: RAW10, one pixel per clock
sv.wr(IMGO + 0x08, BPL - 1)                           # XSIZE (bytes - 1)
sv.wr(IMGO + 0x0c, H - 1)                             # YSIZE
sv.wr(IMGO + 0x10, (1 << 19) | (1 << 16) | BPL)       # STRIDE: bus size 16 + stride
sv.wr(IMGO + 0x14, (8 << 28) | 0x80)                  # CON: burst 8, FIFO 0x80
sv.wr(IMGO + 0x18, (2 << 8) | (2 << 16))              # CON2: FIFO priority thresholds
sv.wr(IMGO + 0x00, phys)                              # BASE_ADDR
sv.wr(TG + 0x10, sv.rd(TG + 0x10) | 1)                # CMOS_EN
sv.wr(0x000, sv.rd(0x000) | (1 << 4))                 # IMGO_EN
sv.wr(0x01c, (1 << 14) | (1 << 16))                   # FBC: EN, FB_NUM 1
sv.wr(0x01c, sv.rd(0x01c) | (1 << 11)); sv.wr(0x01c, sv.rd(0x01c) & ~(1 << 11))  # RCNT_INC
sv.wr(0x020, (1 << 0) | (1 << 2) | (1 << 15))         # CLK_EN: TG, PAK, DMA
mem = mmap.mmap(fd, BPL * H, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=phys)
mem[0:BPL * H] = b"\x55" * (BPL * H)                  # marker, to see what the DMA overwrote
sv.wr(TG + 0x14, sv.rd(TG + 0x14) | 1)                # VF_CON: VFDATA_EN -> go

st = 0
for i in range(20):
    time.sleep(0.05)
    st |= sv.rd(0x00c)
print(f"CAMSV int_status (accumulated) {st:#x}  tg_inter_st {sv.rd(TG + 0x4c):#x}  fbc {sv.rd(0x01c):#x}"
      f"  module_en {sv.rd(0):#x}")
sv.wr(TG + 0x14, sv.rd(TG + 0x14) & ~1)               # stop
data = bytes(mem[0:BPL * H])
changed = sum(1 for k in range(0, len(data), 4096) if data[k:k + 16] != b"\x55" * 16)
print(f"buffer: {changed} of {len(data) // 4096 + 1} 4K pages written; first bytes {data[:16].hex()}")
open(out, "wb").write(data)

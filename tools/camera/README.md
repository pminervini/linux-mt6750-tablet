# Cameras on mainline (MT6750 tablet)

Both cameras work with `tablet-camera`: no kernel camera driver yet, the capture pipeline is
programmed from userspace through `/dev/mem`, with the register values stock Android uses.

| | rear | front |
|---|---|---|
| sensor | OmniVision OV8856, 8 MP | GalaxyCore GC2365, 2 MP |
| I2C (I2C2, GPIO112/113) | 0x36, 16-bit registers | 0x37, 8-bit registers |
| MIPI | 2 lanes, CSI0 -> SENINF1 | 1 lane, CSI1 -> SENINF2 |
| power-down / reset | GPIO107 (high = on) / GPIO110 | GPIO108 (low = on) / GPIO110 (shared) |
| clock | CMMCLK0 (GPIO161), 24 MHz from SENINF TG1 | CMMCLK1 (GPIO109), same TG1 |
| modes | 1632x1224 (default), 3264x2448 (`--full`) | 1600x1200 |
| Bayer | BGGR, image flipped vertically | BGGR |

Supplies (MT6353): VCAMIO 1.8 V, VCAMA 2.8 V, VCAMD 1.22 V (shared by both sensors).

## Use

    sudo /usr/local/bin/tablet-camera -o rear.jpg
    sudo /usr/local/bin/tablet-camera --full -o rear-8mp.jpg
    sudo /usr/local/bin/tablet-camera --front -o front.jpg
    sudo /usr/local/bin/tablet-camera --raw frame.raw ...   # also keep the packed RAW10 frame

5-17 s per photo (auto exposure in a few frames, then demosaic in numpy). sudo's PATH on this
system has no /usr/local/bin: use the full path.

Install (on the tablet; `physbuf.ko` must be built against the running kernel: `make -C ../../out
M=$PWD/physbuf ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules`):

    install -m755 tablet-camera /usr/local/bin/
    mkdir -p /usr/local/share/tablet-camera /usr/local/lib/tablet-camera
    cp data/* /usr/local/share/tablet-camera/
    cp physbuf/physbuf.ko /usr/local/lib/tablet-camera/

## Pipeline

    sensor --MIPI--> CSI analog (0x10217000 / 0x10218000) --> SENINF (0x15008000)
      --mux 3--> CAMSV (0x15009000: TG +0x400, RAW10 packer, IMGO DMA +0x208)
      --> CMA buffer (physbuf.ko) --> unpack / demosaic / AE / grey-world AWB / gamma

- Power: the three LDOs, then the ISP power domain (stock `spm_mtcmos_ctrl_isp`: SPM 0x308,
  status bit 5), imgsys clock gates (`IMG_CG_CLR` 0x15000008), and larb2's IOMMU bypass
  (`0x15001fc0 = 0`: Android sends the camera ports through the M4U, mainline has none, so
  physical addresses are used; with the M4U bit left set the DMA writes go nowhere).
- SENINF / CSI analog: the values Android programs (raw dumps kept locally in `tools/regdiff/and-cam-*`, not versioned; the
  config words in `data/*-seninf.txt`, `data/*-csi*.txt`; the sensor clock generator TG1 is
  part of it). SENINF's top mux selects SENINF1 (rear) or SENINF2 (front) for mux 3, which feeds
  CAMSV.
- CAMSV: sequence of the MT8365 ISP3.0 CAMSV driver submitted upstream (BayLibre, "Add Mediatek
  ISP3.0" v8; same register layout on MT6755), but one pixel per clock (TG `SEN_MODE` 0, PAK
  without the 2-pixel mode) and a 16-bit IMGO bus (`FMT_SEL` bit 24, stride bus size 16): the
  MT8365 settings write every second pixel as zero / only half of each 32-bit word.
- RAW10 in memory is MediaTek's packing: a little-endian bit stream, 4 pixels in 5 bytes.
- Sensor register lists: `data/ov8856-*.txt` from MediaTek's GPL MT6755 OV8856 driver (init,
  preview, capture 30 fps), `data/gc2365-*.txt` from the GPL GC2365 driver (init, stream on).

## Pitfalls found

- **Never rewrite a whole GPIO MODE register for the I2C2 pins**: 0x10005_3b0 also holds the
  connsys F2W_DATA/F2W_CK/WB_RSTB pins (GPIO117-119); re-muxing them, even to the same values,
  makes the Wi-Fi firmware assert (`wifi/mgmt/mt6571/rlm_phy.c #1961`) ~4 s later and the whole
  CONSYS chip (Wi-Fi, BT, GPS) reset - repeatedly while it goes on. Use the MODE SET/CLR aliases
  (+4/+8). `tools/i2c-bitbang.py` had the same bug.
- The sensors answer on I2C only with their clock running (SENINF config incl. `TOP_CTRL` 0xc00
  and TG1).
- OV8856 at 7-bit 0x36 (not MediaTek's other candidate 0x10).

## Not done

No V4L2 driver (no `/dev/video*`, so no standard camera apps); no ISP features (lens shading,
denoise, sharpening, autofocus: the rear focus motor on VLDO28 is not driven, so focus is fixed);
no video; image orientation is the sensor's (rear flipped vertically to undo the mirroring).
The I2C2 controller has no DT node: I2C is bit-banged from userspace.

`cam-up.py`, `cam-stream.py`, `cam-sv.py`, `cam-front.py`, `ov-exp.py`, `raw2png.py` are the
bring-up experiments, kept for reference.

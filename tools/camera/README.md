# Cameras on mainline (MT6750 tablet)

`tablet-camera` uses the native driver in [`v4l2/`](v4l2/README.md) when
`mt6750_camera` is loaded, otherwise the retained guarded `/dev/mem` path.
The native driver release passed real scene/pattern and bounded stream/lifecycle
checks for rear preview, rear full and front on 2026-11-14. JPEG/PNG capture,
automatic exposure, fixed exposure/gain, cancellation and reopen also passed.
This is software-developed RAW, not hardware ISP, full-rate video or general app support.
Both paths hold the same cooperative lock; native capture never falls back to raw
MMIO while the kernel owner exists. Never run old unguarded experiments concurrently.

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

The following are capture commands, not idle checks. Use them only with capture
approval. A loaded kernel owner selects V4L2 without opening `/dev/mem`.

    sudo /usr/local/bin/tablet-camera -o rear.jpg
    sudo /usr/local/bin/tablet-camera --full -o rear-8mp.jpg
    sudo /usr/local/bin/tablet-camera --front -o front.jpg
    sudo /usr/local/bin/tablet-camera --raw frame.raw ...   # also keep the packed RAW10 frame

Native auto exposure meters all four Bayer positions in sampled 2x2 cells and
uses at most eight attempts, each V4L2 child bounded to 30 s; full resolution takes
longer. Both paths preserve measured samples during demosaic, accumulate white
balance in float64, and smooth chroma over 3x3 pixels while retaining display luma
before gamut clipping. Chroma edges can soften by one pixel; luminance grain
remains. Native `--raw` preserves
the documented MediaTek packed10 file format by repacking LE16 capture data.
sudo's PATH on this system has no /usr/local/bin: use the full path.

Legacy fallback installation (on the tablet; `physbuf.ko` must be built against the running kernel: `make -C ../../out
M=$PWD/physbuf ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules`):

    install -m755 tablet-camera /usr/local/bin/
    mkdir -p /usr/local/share/tablet-camera /usr/local/lib/tablet-camera
    cp data/* /usr/local/share/tablet-camera/
    cp physbuf/physbuf.ko /usr/local/lib/tablet-camera/

## Legacy pipeline (native DMA/buffering is described in v4l2/)

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

Bounded native capture and BGGR are accepted; standard-app/libcamera compliance,
full-rate video and colour calibration are not. Hardware ISP features and
autofocus are unavailable; the photo command does not drive the rear actuator.
Keep VLDO28 unchanged because touch shares that supply. Practical output defaults
are front unchanged, rear vertical flip,
and no extra horizontal mirror. Exact physical chirality/rear upright proof remains unclaimed.
The helper uses the I2C2 controller when its DT node exists (current s7x+ kernels), with a
userspace bit-bang fallback on older kernels. The candidate requires the real controller.

`cam-up.py`, `cam-stream.py`, `cam-sv.py`, `cam-front.py`, `ov-exp.py`, `raw2png.py` are the
bring-up experiments, kept for reference.

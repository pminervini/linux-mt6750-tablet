# GPS

> **Moved:** the open-source GPS engine now lives in its own repository,
> [pminervini/opengps-mtk](https://github.com/pminervini/opengps-mtk) (GPL-2.0-or-later).
> This directory is the development history; the tablet runs the new repo
> (`/usr/local/lib/opengps`, `opengps.service` -> gpsd).

**2026-11-09: GPS WORKS on mainline - position fix in ~60 s at a window** (image s6w; linux c66f03af3 +
88bd14223, both in drivers/soc/mediatek/mt6755-pmic.c). Two PMIC settings were missing:

1. **VTCXO24 off** -> the GPS core runs but receives nothing. The stock DT has `ldo_vtcxo24`
   `regulator-boot-on` (no consumer), i.e. always on; on this board it feeds the GPS RF front end.
   Mainline has no MT6353 regulator driver, so nobody enabled it. Now: 0x0a06 b4 + 0x0a36 b1 at init.
   Window test, same spot: stock Android fix in 31 s; mainline without VTCXO24 0 satellites in 30 min;
   with it satellites after 2.4 s, fix after 59-107 s (5 SVs, 24-34 dB-Hz).
2. **AUXADC MDRT detection off** -> the GPS core never starts (details below).

Test: `gpstest.sh N <timeout> <secs>` (scratch) / `fakehal.py <secs>` prints satellites with signal
("tracked, best SNR"), "in view" (EPO predictions, no signal) and "FIX". Assistance data: copy
/data/misc/gps/{EPO,QEPO,MTKNAV}.DAT + mtkgps.dat from Android userdata into /srv/android/data/misc/gps.

**MDRT fix (GPS core start)** (linux c66f03af3, mt6755-pmic `auxadc_hw_init()`).
Cause: the MT6353 AUXADC "MDRT" (modem real-time) periodic conversion was off on mainline
(AUXADC_MDRT_0 0x0f1e = 0x0008 instead of 0x800c). PMIC_WRAP's ADC channel (ADC_CMD_ADDR 0x0a02 /
ADC_RDATA_ADDR 0x0e40 = AUXADC_ADC_OUT_MDRT) hands that sample to the connsys GPS core for co-TSX
(DCXO crystal temperature, mnld's "coTMS"); without a ready sample the GPS core never finishes its
start handshake (A-die 0xa00 bit1 / 0xc0 bit0 stay set, no 0xFE42). The stock 3.18 kernel enables it
in PMIC_INIT_SETTING_V1 (pmic/mt6353/pmic_initial_setting.c: TRIM_SEL, START_SHADE_EN, MDBG/MDRT_DET_PRD,
MDRT_DET_EN, MDRT_DET_WKUP_EN) - the modem is not involved. Found by diffing the PMIC_WRAP block
(GPS_STA 0x1000d1e8 active on 3.18, idle on mainline) and then the PMIC AUXADC block (0x0e40 = 0 on mainline).
Verified on a clean boot with nothing else changed: `FE42: 26 acks: 44`; NMEA flows to fakehal.
Not needed (tested): PMIC_WRAP DCXO_CONN handshake (0x18c..0x1ac), DCXO CW00 XO_EXTBUF2 mode,
MODEM_TEMP_SHARE (0x10018000), EMI MPU (mainline has only the ATF region; tools/emimpu reads it via SMC).
`fakehal.py` now prints max satellites in view per constellation and the time to first fix.

The history below is the investigation log.


Status: chip side works on mainline (WMT GPS function on/off, LNA GPIO114, `/dev/stpgps`
with the stock MT6755 ioctl set). Android's stock `mnld` (32-bit, vendor partition) runs in a
minimal chroot (`android-chroot.sh`, `run-mnld.py`) and is driven by `fakehal.py`, which speaks
the HAL2MNL v1 protocol (abstract datagram sockets `@mtk_hal2mnl` / `@mtk_mnl2hal`, padded
to the full sun_path; init=101 start=102 stop=103).

Where it stops: mnld downloads the GPS firmware (~28 x 1.4 KB, msg 0xFE0C, every chunk acked
0xFE32), sends the final 0xFE05 command, gets no answer, and after ~4 s asks WMT for a firmware
assert (ioctl 12) -> whole-chip reset (WiFi recovers by itself).

Difference to stock Android (same mnld, captured with `hal2mnl` 101/102 on Android):
stock mnld first queries ioctl 11 (co-clock flag) and sets `gps.clock.type=21`; under the
chroot it never issues ioctl 11, even with a property area providing gps.clock.type=21
(`mkprops.py`), so it presumably configures the GPS core for a TCXO the board does not have.
Next step if resumed: find what triggers the ioctl-11 path in mnld/libmnl (disassembly), or
patch the clock type into the engine config.

## 2026-11-08 follow-up (option 2: clock configuration)

- mnld only takes the co-clock path (ioctl 11) when `persist.mtk.wcn.combo.chipid=0x6755`;
  it reads the GPS calibration from NVRAM (APCFG/APRDEB/GPS: /dev/stpgps, 26 MHz, type 0xff)
  only when `service.nvram_init=Ready` and `ro.hardware=mt6755` are set. `mkprops-stock.sh`
  builds the property area from the stock build.prop files plus these.
- With that, everything mnld sends to /dev/stpgps (0xFE08 config, 44 firmware chunks, 0xFE05
  start) is byte-identical to stock Android (strace of stock mnld via the Debian chroot on
  Android, triggered with a send-only hal2mnl 101/102).
- Difference is chip-side: on stock the GPS core sends its first 0xFE42 data packet ~1 ms after
  the last chunk ack, and mnld then sends a 185-byte init batch; on mainline 0xFE42 never comes,
  mnld waits ~6 s and resets the chip. So the GPS core in CONSYS does not start under the
  mainline connsys port (power/clock/EMI side), not a mnld/config problem.

## 2026-11-08 dig: why the GPS core does not start (all negative so far)

Confirmed at the STP level (`echo "0xa 0x4" > /proc/driver/wmt_dbg` dumps every rx frame): after
the 44th chunk ack the chip sends nothing on the GPS channel; nothing is dropped on the host.
Identical between stock Android (GPS running) and mainline (GPS on), so ruled out:
- bytes mnld sends (config, chunks, start), chip replies up to the last ack
- WMT GPS power-on steps (VCN28 on, GPS LNA = GPIO114 per stock DT, desense cmd), co-clock flag 1
  (= vendor PMIC auto-detect), osc_type init cmd, both ROM patches (1_1 then 1_0) downloaded
- registers: topckgen/infracfg/SPM conn domain, consys MCU cfg 0x18070000 (one status byte),
  PMIC LDOs, DCXO CW00..CW16 (only buffer EN_M bit / 32k NFRAC trim), RTC 32k export (RTC_CON
  F32KOB clear on both; no PDN1 user bits on stock either)
- stock wmt_drv.ko has no set-chipid / RF-calibration scripts; only extra init cmds are WiFi/BT
  front-end coex (bt tssi from wifi, wifi elna gain p1, wifi ext component, wifi path)
Open: chip-internal state not visible from the AP. Next ideas: port the 4 missing coex init
commands; read chip-internal registers via wmt_dbg 0xb on both sides (needs an address map).

- Ported the stock SoC coex init commands ("wifi path", "wifi elan gain p1"; connsys commit): WiFi/BT fine, GPS unchanged (still no 0xFE42). GPS parked again.

## 2026-11-08 evening: second dig (still not starting)

New comparisons, Android (GPS running) vs mainline (GPS on), all equal unless noted:
- all 190 GPIOs (mode/dir/out), IOCFG pad config (only SD card pads differ), APMIXED/topck/infra
  clocks (CPU/audio/display PLLs only), CONSYS_EMI_MAPPING 0x10001340 = 0x19fe on both, EMI and
  infra QoS tables (bandwidth limiter only), whole PMIC incl. DCXO.
- PMIC: VTCXO24 is ON on Android (LDO_VTCXO24_SWITCH=1, hw-mode EN) and OFF on mainline. Enabling
  it (0x0a06|=0x10, 0x0a36 EN) does NOT make the GPS core start. VCN28 is off on Android during GPS,
  on under mainline; turning it off doesn't help either.
- Transport: WMT loopback (buffer enlarged for the test) echoes 1000..1800-byte packets byte-exact,
  so the 1.4 KB GPS firmware chunks reach the chip intact.
- osc_type command identical to stock wmt_drv.ko; OSC32K bypass not sent by either.
Remaining: chip-internal state set by WMT commands the stock (binary) wmt_drv sends and this port
does not, or ROM-patch/firmware handling inside CONSYS. Next step would be extracting the exact
WMT command sequence from stock wmt_drv.ko (disassembly) or porting the newer vendor common_main.

## 2026-11-08: stock driver disassembly (option 1)

Stock wmt_drv.ko / gps_drv.ko are not stripped and carry DWARF, so the init-script tables of
wmt_ic_soc.c were decoded byte-exact (gps-re/tables.py, ~/workspace/mediatek/gps-re) and the
call order of mtk_wcn_soc_sw_init reconstructed from the disassembly. Result: same scripts, same
bytes, same order as this port (stp setup, power-on DLM, mcuclk 1/2 around the patch download,
init_table_3, LTE coex filter tables, RF calibration with BT/WiFi PALDO on, coex_wmt, osc_type
01 0a 02 00 08 03, FM comm). Extras in stock are 5G/A-die (6592/6625 only) and core-dump level.
Stock gps_drv.ko open/write/read/ioctl = func_on + plain stp send/receive, as this port.
Patch addresses come from the patch headers (same launcher logic). Also tried with STP PSM off:
no change. => No host-side difference left that we can see; the GPS core still never sends
0xFE42 after the download.

## 2026-11-08 late: third dig (stock TX capture, core dumps) — still not starting

New tools: `tools/stphook` (stock-kernel module: hooks the stock wmt_drv `sys_if_tx` pointer, logs every
STP frame sent to CONSYS), `tools/cdgrab` (stock-kernel module: collects the paged CONSYS core dump the
stock driver skips, "coredump is disabled"), connsys `wmt_tx_dump` param + `/sys/kernel/debug/wmt_coredump`
on mainline, BTIF real-time log (`echo "a 5" > /sys/bus/platform/drivers/mtk_btif/flag`).

- Complete stock WMT command stream (power-on, both ROM patches, MCU clock, coex tables, RF cal, coex,
  osc, FM strap, GPS func-on) captured with stphook: byte-identical to mainline after two fixes
  (LTE coex tables + "set LTE project" now sent; "wifi path"/"elna gain" dropped). Only extra on stock:
  modem IDC messages (0x35/0x3d/0x36/0x39/0x3e) — GPS works on Android with the modem stopped
  (ccci stop request) even after a whole-chip reset, so not needed.
- Stock switches VCN28 off after RF calibration (EFUSE_PALDO off) and on at GPS func-on; port now does
  the same. No change.
- BTIF raw RX on mainline: after the 44th chunk ack the chip sends nothing (no lost bytes).
- Android with mainline values (PMIC TOP_CON SRCLKEN SW mode, DCXO CW00) + chip reset: GPS still works.
  Mainline with Android values (INFRA_MISC bits, MCU clock reg, VTCXO24, SPM_CLK_CON): still fails.
- WiFi/BT off + chip reset + GPS first on mainline: fails. Not BT/WiFi coexistence.
- Core dumps (assert during the stuck/working GPS session) decode to MCU RAM 0x02090000 (143 KB,
  Andes NDS32 MCU); differences exist but are uninterpretable without symbols.
Remaining: chip-internal. Next candidates: NDS32 disassembly of the ROM patch GFNS/DSP-boot code
(EMI 0xa0000..), or the GPS DSP's own power/clock (A-die MT6625L GPS block).

## 2026-11-08 night: chip-internal view (ROM dump, GPS block status)

- mnld's own log: the chroot has no logd, so all mnld diagnostics were lost; `/tmp/fakelogd.py`
  (scratch, listens on /srv/android/dev/socket/logdw) recovers them. mnld start-up is identical to
  Android (co-clock buffer 2 coTMS, same calibration, Status (18)); only post-start settings differ
  (GNSSOPMode 0x302 vs 0x102 because `gps.clock.type` can't be set without a property service).
  Full 110-byte FE08 config is byte-identical.
- Chip-internal registers readable on both sides: mainline `echo "0xb <addr> 0xffffffff" >
  /proc/driver/wmt_dbg` (prints `WMTREG`), stock via `tools/chipreg` (calls wmt_drv's wmt_lib_reg_rw,
  address from /proc/kallsyms). 864 registers in 0x8000xxxx..0x800fxxxx compared mid-session.
- GPS block status 0x8007000c bit 29 / 0x80070010 bit 3: set right after GPS func-on, cleared during
  the firmware download on BOTH systems, set again ~100 ms after the last chunk on Android (GPS core
  starts) and never again on mainline.
- MCU ROM (0x0..0x5ffff) dumped through WMT register reads (~2.5 min) and disassembled (binutils
  2.39 --target=nds32le-elf): `driver/gps_reroute.c` relays GPS bytes through a FIFO port at
  0x80070000 (status 0x8007000c, bit 3 = ready); 0x80030000 / 0x800b0000 are MTK-style UARTs
  (configured identically on both systems).
- Ruled out this round: PSM keep-awake during GPS (now stock-like, default off), write pacing,
  chip MCU clock (GPS-only cold start runs at 26 MHz like Android, still fails), shared-memory zero-fill.
- GPS core memory can be read through the mailbox 0x80070030 (addr) / 0x80070020=4 (go, bit 2 busy) /
  0x80070050 (16-bit data) - only while the GPS firmware runs (busy in bootloader state on both
  systems). On mainline it stays busy after the download: the downloaded GPS firmware never runs.
- connsys 32 kHz timer 0x81050034 ticks at 32768 Hz on mainline (32k clock present).
- MCU RAM (0x02090000-0x02098000, $gp = 0x020973d4) Android-working vs mainline-stuck in the same
  GPS-only cold start: GPS reroute flags identical; only counters/logs differ.
- Full-frame check: tails (incl. STP CRC over the whole payload) of all 46 GPS frames (FE08, 44 x FE0C,
  FE05) reassembled from the stock hook and the mainline BTIF log are identical -> the GPS core gets
  byte-identical firmware. (Android's mksh does 32-bit arithmetic: compute kernel addresses in the
  chroot's python, never `$((addr + off))`; a wrong slot address crashed the stock kernel once.
  Also never leave `persist.log.tag V` set on Android: it made Android reboot ~1 min after boot.)
- SPM firmware ruled out (2026-11-08): on Android, with the SPM PCM stopped (tools/regpoke:
  POWERON_CONFIG_EN key, PCM_PWR_IO_EN=0, PCM_CON0 = key|CK_EN|SW_RESET then key|CK_EN -> PCM_CON0
  stays 0x4) and a whole-chip reset, GPS still starts (15 x FE42). Android never uses SPM-assisted
  idle here (dpidle/soidle counts 0) and vcore DVFS is off (fixed 0.9 V / DDR 1 GHz).
- BGF wake IRQ (SPI 237) fires during GPS on both systems (mainline 45/session with stock-like PSM).
- Reverse test on Android (tools/regpoke): with mainline's values applied to every known AP-side
  difference - SPM PCM stopped + SPM_CLK_CON 0x8000, PMIC TOP_CON SW SRCLKEN + VTCXO24 off, infra
  clock gates all open, DCM (0x10001010/70/74), INFRA_MISC 0x10001f00, EMI CONH/CONO/0x5c = 0 -
  a whole-chip reset + GPS still works on Android (15 x FE42). AP register state is NOT the cause.

## 2026-11-08 late night: A-die (MT6625L RF chip) view

- WMT opcode 0x13 reads A-die registers: `01 13 04 00 02 04 <addr16>` -> event `02 13 09 00 <st> 02 04
  <addr16> <val32>` (byte 5 = SPI channel 4; other values are rejected). Chip id at 0x24 = 0x66258a02.
  Mainline: `echo "0xb 0xADE0<addr> 0xffffffff" > /proc/driver/wmt_dbg` (connsys wmt_core.c hook).
  Stock: `tools/chipreg` adie=/adcount= (calls wmt_core_tx/rx directly, inside the stock
  DISABLE_PSM_MONITOR/ENABLE_PSM_MONITOR pair - without that lock it steals PSM events and causes
  whole-chip resets). The address space is 12 bit (0x000-0xffc, mirrors above).
- The MCU reaches the A-die through an SPI master at 0x80103000 (ROM driver/sys_spi.c: write = addr|0x1000..0x5000
  per channel into 0x801030x0, data at +4; read = addr|0x9000.., data at +8; busy bits in 0x80103000).
- Full A-die dumps (1024 regs) Android vs mainline, GPS off and GPS on: identical except
  0x0a00 and 0x00c0. Time series (Android: chipreg adrep mode, ~2 ms/sample; mainline: wmt_dbg loop):
  GPS func-on sets 0xa00 bit0 and 0xc0 bits 27/28 on both. ~50 ms after the last firmware chunk ack,
  0xa00 bit1 and 0xc0 bit0 get set on BOTH systems; on Android they clear again within 6 ms (then the
  GPS core runs), on mainline they stay set forever. 0xa00 bit3 / 0xc0 bits 27-28 also follow WiFi.
  -> the GPS start handshake (looks like an RF/PLL calibration or clock request) never completes on
  mainline. The patch has no writer of A-die 0xc0 for this chip (f00e41e4 is gated on other chip ids),
  so it is probably the GPS core itself.
- MCU PC sampling (CPUPCR 0x18070160; mainline /dev/mem, stock tools/regpeek samp=): with GPS on the MCU
  sits in the ROM idle spin 0xc48 on both systems - the MCU is not stuck.
- Clock source on Android: PMIC (MT6353 DCXO) clock buffers, CKBUF2_CONN SW-enabled (/sys/power/clk_buf);
  RF (modem) clock buffers unused. VCN28 on during GPS on both.

## 2026-11-08/09 night: GPS WORKS on the open-source 3.18 kernel

- Neffos 3.18.79 built from source (`~/workspace/mediatek/kernel`, build-neffos.sh; connsys modules from
  connectivity/source = newer MTK common_main, installed via the Magisk kmod-swap hook into
  /data/linux/kmod/3.18.79-6576acd9) + Android userspace: `FE42: 15 acks: 44`, i.e. GPS starts.
  The cause of the mainline failure is therefore in open source, not in the stock binaries.
- Unattended 3.18 boots: the 3.18 tree now arms MT6353 TOP_RST_MISC_SET (0x02aa) = 3 in pmic_mt_probe
  (reboot/WDT -> PMIC full power cycle -> mainline default). Worked once (16:12); the second reboot
  (~22:20) left the tablet powered off (needs the power button). boot partition currently holds
  release/boot-3.18.79-neffos-gps1.img; stock Magisk boot = backup/parts/boot-magisk-20261108.bin.
- Modem ruled out properly: Magisk post-fs-data hook nomodem.sh (flag /data/linux/nomodem) bind-mounts a
  no-op over ccci_mdinit -> the modem never boots; GPS still starts on 3.18 (15 x FE42, twice).
  (Earlier "modem stopped" tests had the modem booted first.)
- Note: the first andgps.sh run after an Android boot shows 0/0 (mnld's boot-time session); repeat it.
- Chip pages 0x8010xxxx (0x80102000 = connsys AFE, cf. CONSYS_AFE_REG values; 0x80103000 A-die SPI
  master; 0x80106000 HW SPI command sequencer that writes A-die 0xc0), 0x8104/5/6xxxx: equal to Android
  once WiFi/BT states match.
- Writing A-die regs works: `01 13 08 00 01 04 <addr16> <val32>` -> `02 13 02 00 00 01`
  (mainline: `echo "0xc 0xADE0<addr> <val>" > /proc/driver/wmt_dbg`). Clearing 0xc0 bit0 while stuck:
  it is re-asserted at once (driven continuously, probably by the GPS core).
- PMIC during GPS: ~150 registers differ (modem MDRT ADC, charger, accdet, audio, LDO modes), but the
  GPS AUXADC channels (0x0e20 CH7_BY_GPS, 0x0e44 DCXO_BY_GPS, RQST1) are 0 on both.
- pwrap 0x1000d000 dumped on 3.18-Android (scratch and-pwrap.txt); mainline only uses WACS2.
Next: bisect 3.18 vs mainline (both open source).

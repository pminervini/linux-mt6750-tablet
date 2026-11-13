# linux-mt6750-tablet: mainline Linux on an MT6750 tablet

Native Debian on a mainline kernel (Linux 7.3.1 + the patches here) for an MT6750 tablet
(MT6755 family SoC, MT6353 PMIC, k50v1 board, 800x1280 JD9365D DSI panel, MT6625L connectivity),
booted by the stock bootloader (lk) from the `recovery` partition. Mainline is the power-on
default; stock Android stays installed and is one command away.

This is the bring-up as it was done, kept as working notes, scripts and the kernel patch series.
It is not a polished distribution.

## What works

- 8 CPUs (both clusters, SPM power-on), eMMC
- USB-C dual role: CDC-ECM gadget (ssh over USB) when plugged into a computer, host mode (keyboard
  etc. through an OTG adapter) when the ID pin is grounded, switched automatically; reboots with a
  host device attached come back on battery (the port's 5 V is switched off and an RTC alarm powers
  the PMIC back on)
- console on the boot loader's framebuffer (simple-framebuffer; lk's overlay layer switched off),
  PWM backlight, panel sleep/wake over DSI
- Wi-Fi (WPA2, DHCP, power save) and Bluetooth (BlueZ) through the vendor connsys stack, forward-ported
  as out-of-tree modules (see "Not included")
- charging with temperature limits (charging stops if the voltage/NTC readings go stale), coulomb-counting
  fuel gauge, a 70-80 % charge limit for a tablet that stays plugged in, orderly power-off on low battery
  (also with a charger attached that cannot keep up)
- RTC, power key, accelerometer (QMA6100P), SoC temperature sensor (thermal zone with critical trip; the
  userspace DVFS holds the low clock above 85 C)
- touchscreen (Silead GSL3670, multitouch; firmware cut from the device's own stock kernel)
- GPS: open-source engine [opengps-mtk](https://github.com/pminervini/opengps-mtk) (no mnld/libmnl)
- FM receiver: tuning/retuning and RDS PI, station name, and RadioText verified under native Debian;
  requires the matching receiver-capable connsys revision and stock DSP firmware (see "Not included").
  FM digital audio capture/playback and reboot persistence are not verified; module loading is manual.
- established userspace speaker path (`tools/audio/tablet-audio`) retained as an
  unload fallback; the new guarded ALSA deployment keeps playback disabled
- side-button playback volume in 5% steps, with a rotating two-second bar and percentage
  (`tablet-pwrkey` plus `tablet-sentinel`); zero mutes playback samples. Changing the selected
  level never starts audio. Install the renderer in `/usr/local/bin/` and the controller and
  audio helper in `/usr/local/sbin/`; the renderer needs `python3-pil` and `fonts-dejavu-core`.
- ambient eyes with four live telemetry panels (2x2 portrait, four columns landscape),
  retaining the six-hour keyboard-idle gate, random 1-3-hour checks, and 3-5-minute appearances.
  An available, unowned FM receiver performs a timestamped sequential RSSI sweep and
  dwells for received RDS names. Analogue outputs must be off; receiver mute/volume zero
  remain selected. Missing PCM is labelled unavailable, without invented spectra/levels.
  Install the HUP-aware `fmctl` from the receiver-capable connsys source in `/usr/local/bin/`;
  the display neither loads drivers nor enables playback. FM module loading remains manual,
  and reboot persistence is unverified. Offline checks:
  `python3 -B -m unittest discover -s tools/tablet-etc -p 'test_*.py' -v`.
- boot splash: own boot logo (lk), lk's "Orange State" screen and 5 s delay patched out, quiet boot
  with an optional animation until the login prompt, console rotated for landscape
  (`tools/splash`)
- unattended operation: hardware watchdog fed by systemd (full reset if userspace hangs), fallback to
  Android when a kernel fails early or Debian cannot start (after 30 min on the USB rescue console), Wi-Fi watchdog, flashing script that verifies the write and the
  booted kernel, quiet console with a clean login prompt

- microSD slot (MSDC1, SD default/high speed): cards read and mount (tested: 32 GB SDHC, FAT32);
  `tools/tablet-etc/60-sdcard-readonly.rules` keeps cards read-only until removed
- auto-rotation: the text console follows the tablet's orientation (accelerometer, `tablet-rotate`)
- containers: Docker (Debian's `docker.io`) with its default bridge networking and published ports;
  the kernel config carries the netfilter/overlay/veth pieces it needs
- cameras: rear OV8856 (8 MP) and front GC2365 (2 MP) take still photos with `tablet-camera`, a
  userspace capture pipeline (sensor -> SENINF -> CAMSV raw dump -> demosaic/AE/AWB) with no kernel
  camera driver needed (`tools/camera`); the installed ownership guard refuses this raw path
  while the new kernel camera owner is loaded
- standard audio/camera interfaces: the ASoC card/endpoints/mixer and both V4L2 RAW
  nodes' formats/controls/media topology enumerate; ordinary unload and reload passed.
  Playback, FM PCM capture, and camera streaming remain disabled. The four-panel
  muted RF display still works. See [the verified scope](STANDARD-INTERFACES.md).

Not accepted yet: actual PCM/DMA, V4L2 images/video/Bayer phase and app support,
autofocus, microphone/jack support, reboot persistence, CPU frequency scaling beyond
the boot loader's operating point (a small userspace governor switches between two points), GPU.

## Building

```sh
git clone https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git linux
cd linux && git checkout -b mt6750-tablet v7.3.1 && git am ../patches/linux/*.patch && cd ..
./build.sh config      # .config from defconfig + the fragments in build.sh (config/ has the result)
./build.sh             # Image.gz + DTB, modules staged in out/modules
./mkimage.sh <tag>     # boot image release/boot-mainline-<tag>.img (Android v0 header, for lk)
python3 check-dtb.py out/arch/arm64/boot/dts/mediatek/mt6750-k50v1-tablet.dtb
```

`check-dtb.py` refuses DTBs that lk would reject (lk applies the board's ODM overlay and checks
reserved memory; a rejected DTB ends in a boot-ROM loop). It needs that overlay as `dtbo/ov0.dtbo`,
taken from your own device's `odmdtbo` partition.

## Boot selection

lk boots `recovery` (mainline) when either
- lk's **one-shot RTC flag** is set (`/sys/kernel/mt6755_pmic/boot_recovery`; lk clears it when it
  boots recovery). `mainline-default.service` sets it once a boot has been healthy for 60 s, so any
  reboot/power-on returns to mainline, while a kernel that panics or hangs falls back to Android.
- the **BCB** in `para` says `boot-recovery` (Android -> mainline hand-off; the initramfs consumes it).

| from | command | result |
|---|---|---|
| mainline | `sudo tablet-android` | Android once (BCB `android-once`); a Magisk `service.d` script re-arms mainline |
| Android | reboot (any) after `tablet-android` | mainline |
| Android | `adb reboot recovery` | mainline |
| mainline | reboot / power-on / crash after 60 s up | mainline |
| mainline | crash or hang before healthy | Android |

## Layout

- `patches/linux/` - kernel patch series on top of `v7.3.1`
- `config/kernel-7.3.1.config` - the kernel .config in use
- `build.sh`, `mkimage.sh`, `check-dtb.py` - kernel, boot image, DTB sanity check
- `initramfs/` - init sources (switch_root into Debian on userdata `/linux/debian`)
- `tools/` - flashing and test loop (`flash-mainline.sh`, `to-mainline.sh`, `tab.py`), register
  peek/poke/diff helpers for the stock kernel (`regpeek`, `regpoke`, `regdiff`, `chipreg`), I2C
  bit-bang probe, charger and audio experiments, `splash/` (boot logo, animation, lk patch), `tablet-etc/` (systemd units and scripts installed on
  the tablet), `gps/` (notes; the engine is in opengps-mtk), `touch-notes.md`,
  `gsl-fwextract.py` (touchscreen firmware from the stock boot image)
- `diag/` - boot-ROM (mtkclient) session scripts used to read logs and registers during early bring-up
- `nokia-patches/` - patch lists consulted from other MediaTek mainline efforts

## Not included

Proprietary firmware and per-device data are not included; take them from your own
device. Board register sequences derived from GPL BSP source retain their provenance
in the component READMEs.

- **connsys firmware and NVRAM** (`WIFI_RAM_CODE_6755`, `ROMv2_lm_patch_*`, `WMT_SOC.cfg`, Wi-Fi
  calibration): from the stock `vendor`/`nvdata` partitions.
- **connsys kernel modules** (WMT/STP/BTIF, wlan gen2): a forward-port of MediaTek's GPL downstream
  drivers, based on [mt6572-connsys-modules](https://github.com/buddyjojo/mt6572-connsys-modules),
  in their own repository, [mt6750-connsys](https://github.com/pminervini/mt6750-connsys) (Wi-Fi
  incl. monitor mode, Bluetooth, the GPS core, and FM receiver/RDS); built separately, against the same kernel.
  For receiver setup and the distinction from the MT6572 ALSA audio helpers, see its
  [MT6755 FM runbook](https://github.com/pminervini/mt6750-connsys/blob/main/tools/fm.md#mt6755--mt6750-receiver-and-rds).
- **FM DSP firmware**: `mt6627_fm_v1_patch.bin` and `mt6627_fm_v1_coeff.bin`, from your own
  stock `/vendor/firmware/`, installed in `/lib/firmware/`. No additional kernel patch was needed
  for the receiver milestone; these proprietary blobs are not included here.
- **touchscreen firmware**: `tools/gsl-fwextract.py <stock boot.img> /lib/firmware/silead/gsl3670-k50v1.fw`.
- **GPS firmware**: `fwextract.py` in opengps-mtk.
- **the board's ODM overlay** (`dtbo/ov0.dtbo`, for `check-dtb.py`) and stock device-tree dumps.
- bring-up logs and memory dumps (device-specific).

## License

Kernel patches: GPL-2.0, as the kernel. Scripts and tools: GPL-2.0-or-later unless a file says otherwise.

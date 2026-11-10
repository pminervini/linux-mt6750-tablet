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
- charging with temperature limits, coulomb-counting fuel gauge, a 70-80 % charge limit for a tablet that
  stays plugged in, orderly power-off on low battery
- RTC, power key, accelerometer (QMA6100P), SoC temperature sensor (thermal zone with critical trip)
- touchscreen (Silead GSL3670, multitouch; firmware cut from the device's own stock kernel)
- GPS: open-source engine [opengps-mtk](https://github.com/pminervini/opengps-mtk) (no mnld/libmnl)
- speaker audio from userspace (`tools/audio/tablet-audio`, no ALSA driver yet)
- unattended operation: watchdog full-reset, fallback to Android when a kernel fails early, Wi-Fi
  watchdog, quiet console with a clean login prompt

Not working yet: cameras, CPU frequency scaling beyond the boot loader's operating point
(a small userspace governor switches between two points), GPU.

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
  bit-bang probe, charger and audio experiments, `tablet-etc/` (systemd units and scripts installed on
  the tablet), `gps/` (notes; the engine is in opengps-mtk), `touch-notes.md`,
  `gsl-fwextract.py` (touchscreen firmware from the stock boot image)
- `diag/` - boot-ROM (mtkclient) session scripts used to read logs and registers during early bring-up
- `nokia-patches/` - patch lists consulted from other MediaTek mainline efforts

## Not included

Nothing here is MediaTek's or the device vendor's code or firmware; take those from your own device:

- **connsys firmware and NVRAM** (`WIFI_RAM_CODE_6755`, `ROMv2_lm_patch_*`, `WMT_SOC.cfg`, Wi-Fi
  calibration): from the stock `vendor`/`nvdata` partitions.
- **connsys kernel modules** (WMT/STP/BTIF, wlan gen2): a forward-port of MediaTek's GPL downstream
  drivers, based on [mt6572-connsys-modules](https://github.com/buddyjojo/mt6572-connsys-modules);
  built separately, against the same kernel.
- **touchscreen firmware**: `tools/gsl-fwextract.py <stock boot.img> /lib/firmware/silead/gsl3670-k50v1.fw`.
- **GPS firmware**: `fwextract.py` in opengps-mtk.
- **the board's ODM overlay** (`dtbo/ov0.dtbo`, for `check-dtb.py`) and stock device-tree dumps.
- bring-up logs and memory dumps (device-specific).

## License

Kernel patches: GPL-2.0, as the kernel. Scripts and tools: GPL-2.0-or-later unless a file says otherwise.

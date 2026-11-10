# Touchscreen (GSL3670/"gsl1680" family, Silead) - status 2026-11-10: WORKING at driver level

**Solved:** the chip answers register transactions (write the register pointer, then read) but
NACKs bare address probes (i2cdetect -r/-q, the bit-bang tool's address-read probe) - that is why
the bus looked empty. `i2ctransfer -y 0 w1@0x40 0xfc r4` returns the ID 00 00 91 50. It also NACKs
the end of the reset command (0xe0 = 0x88) because it resets immediately; mainline silead treated
that as fatal (patched). Now: silead driver (module), DT node touchscreen@40 (GPIO10 power/shutdown,
EINT1), firmware /lib/firmware/silead/gsl3670-k50v1.fw cut from the stock kernel by
tools/gsl-fwextract.py (config entry 0; table found by disassembling gsl_sw_init with the stock
/proc/kallsyms). Firmware status 0x5a5a5a5a, interrupts arrive. Orientation: the chip's X runs along the long side
(touchscreen-swapped-x-y), ranges about 1664 x 960 (DT); fine calibration from three taps with
tools/tablet-etc/touch-calibrate (red crosses on the framebuffer) -> libinput matrix in
/etc/udev/rules.d/99-touch-calibration.rules; centre check error 9 px. Multitouch (3+ fingers) works.

GPIO11 is NOT touch power (gsl_resume/gsl_probe never touch it; it follows the display).

## History of the investigation (kept for reference)

Facts from the stock Android 8.1 kernel on this tablet (tablet-android, adb, regpeek, /proc/gsl_config):
- driver `gslX68X` (MTK tpd), **I2C bus 0 = controller 0x11007000, address 0x40** (it overrides the DT's
  0x5d placeholder); chip ID read 0xfc = 0x50910000; firmware loaded by the driver at probe.
- I2C0 pads: GPIO92 = SDA0, GPIO93 = SCL0 (mode 1), same as mainline.
- **GPIO11 = touch power enable** (high while running, low in suspend) - in no DT.
- **GPIO10 = shutdown/reset, active low** (high while running, low in suspend). The overlay's rst pin.
- GPIO1 = interrupt (EINT1), bias disabled.
- **VLDO28 is OFF** in Android, running or suspended: the DT's vtouch-supply = VLDO28 is wrong for this
  board. (mainline enables VLDO28 at boot; harmless but unneeded.)
- PMIC EN_STATUS1/2: identical to mainline except VCN28 (connsys, used by mainline Wi-Fi/BT).
- GPIO + IOCFG (0x10005000, 0x10002000-0x10002bff) diff Android-running vs mainline: no touch-relevant
  difference once GPIO10/11 are high and GPIO1's pull is off (others: IDDIG, eMMC drive, audio, camera).

On mainline with exactly that state (VLDO28 off, GPIO11 power-cycled incl. 3 s off with SDA/SCL held
low, GPIO10 released after power-up): **nothing on bus 0 ACKs - neither 0x40 nor the LCD bias NT50358
at 0x3e** (which lk programs at every boot), by the controller or bit-banged (tools/i2c-bitbang.py,
validated on bus 1: accelerometer 0x12 ACKs). The pads drive low and release normally.
Bus 3 (MT6311 external buck, 3-006b on Android) is also silent on mainline; bus 1 works.

Second round (2026-11-10, Android screen kept on, touch resumed and answering via /proc/gsl_config):
- full PMIC register space (0x000-0xffe) diffed: rail enables identical once VCN28 is switched off on
  mainline (tested: still no ACK); remaining differences are off rails' voltage selects (VSIM, VMC,
  VCAMD), Android DVFS (VSRAM_PROC), clock-request HW modes (TOP_CON), start-up protection enables,
  and LDO_RSV_CON1 RSV2 = 0xff on Android (0x01 on mainline; write does not stick from mainline).
- IOCFG 0x10002000-0x10002fff, pericfg: no touch-relevant difference; infracfg: clock gates only.
- bus 0 electrically: external pull-ups powered (lines stay high with the internal pull-down on), the
  pads drive low and release; probes at 100 kHz (controller) and ~10 kHz (bit-bang), SDA/SCL swapped,
  INT floating or low through reset, 0.3-3 s power-off with SDA/SCL held low: never an ACK.
- bus 3 (MT6311) idles LOW on mainline (no powered pull-ups) - a different problem from bus 0.

Not yet compared (needs other means): other PMIC registers (LDO voltages, buck/IO-domain settings) beyond EN_STATUS, the
MT6311 state (silent bus 3 - common cause with bus 0?), infra/pericfg registers.
Next: physical measurement (touch VDD on the flex connector, SDA/SCL with a scope while probing), or
disassemble the stock gslX68X driver (kernel in backup/parts/boot.bin) for its exact power/reset
sequence and any extra GPIO/PMIC access, or trace Android's I2C0 traffic at resume.

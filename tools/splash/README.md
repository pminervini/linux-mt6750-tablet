# Boot splash

What the screen shows from power-on to the login prompt:

1. **lk** (boot loader) draws the boot logo from the `logo` partition. On an unlocked device stock lk
   then prints the "Orange State" warning and waits 5 s; `install.sh lk-patch` removes both.
2. **Linux** keeps that picture: the kernel is `quiet` and fbcon takes over the screen only when text is
   printed (`FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER`), so on a healthy boot the logo simply stays.
3. **tablet-splash** (optional) continues it as an animation from early userspace (~4 s) until
   `getty@tty1` starts (~16 s), then gives tty1 back to the console.
4. The **console** is rotated for landscape (`fbcon=rotate:3`).

Instead of the animation, `install.sh scroll on` keeps the logo only while the kernel boots and then
lets systemd's `[  OK  ] Started ...` lines scroll on tty1 until the login prompt
(`show-status.conf`: `ShowStatus=yes`; `getty-keep-boot-log.conf`: the getty does not wipe the
screen first). `install.sh scroll off` (or installing an animation) goes back.

Errors still reach the screen: any kernel message at error level or worse ends the logo, and the
animation stops on emergency/rescue mode or after 90 s. `BOOT_VERBOSE=1 ./build.sh config` builds a
kernel with the old verbose boot console instead (the animation is then skipped).

## Orientation

All images are given in the *view* orientation: landscape **1280x800**, as the tablet is held with the
rotated console. Other sizes are scaled to fit (black bars). The tools convert to the panel (portrait
800x1280) and to lk's storage orientation (lk's logo blit rotates by 90 degrees, so the stored image is
turned by 180 degrees). `install.sh capture` shows what lk actually drew at the last boot (the kernel
keeps a copy), to check a new logo without looking at the device.

## Usage (on the build machine; Pillow needed; `TABLET=user@host`, default `tablet-usb`)

```sh
tools/splash/install.sh default            # generated cyberpunk splash: logo + animation
tools/splash/install.sh logo my.png        # any image as the boot logo
tools/splash/install.sh anim my.gif        # GIF/APNG, directory of PNGs, or a single image
tools/splash/install.sh anim none          # no animation: the logo stays until the login prompt
tools/splash/install.sh scroll on          # no animation: logo, then systemd's boot lines scroll
tools/splash/install.sh scroll off         # back to the animation (if one is installed)
tools/splash/install.sh lk-patch           # no Orange State screen / 5 s delay
tools/splash/install.sh capture shot.png   # what lk showed at this boot
tools/splash/install.sh restore lk  backups/lk-<date>.bin     # undo (also: restore logo ...)
```

Every partition is saved to `tools/splash/backups/` before it is written (`BACKUP_DIR=` to change), and
every write is read back and compared. `splash.py` has the pieces separately (`logo-unpack`,
`logo-pack`, `anim-pack`, `lk-patch`, `cyber`, `fb2png`).

Animations: frame delays come from the GIF/APNG (or `--fps`, default 20). The player redraws only the
changed parts of each frame; a full-screen change costs about 4 MB per frame, so keep the moving areas
small (the default animation, a scrolling grid and a blinking cursor, runs at 20 fps, 4 MB file).

## The lk patch

The warning is one function in lk (`0x46002d50` in this device's lk, located by its reference to the
"Orange State" text and its `mdelay(5000)`): it prints three lines, waits 5 s and returns 0. The patch
replaces its first instruction with `movs r0, #0; bx lr` (4 bytes), so the unlocked boot takes the same
path as a locked ("green") one. Secure boot is off on this device (mtkclient: SBC disabled), so the
preloader does not check lk's signature. `lk2` (the second copy) is left stock. If a patched lk did not
boot, nothing would start, not even Android: restore the saved `lk` with mtkclient from boot ROM mode
(mtkclient `w lk backups/lk-<date>.bin`).

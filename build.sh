#!/bin/bash
# Build mainline kernel for the MT6750 tablet: Image.gz + appended DTB.
# usage: ./build.sh [config|all]   (config: regenerate .config only)
set -euo pipefail
cd "$(dirname "$0")"
TOP=$PWD
SRC=$TOP/linux OUT=$TOP/out
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
M="make -C $SRC O=$OUT -j$(nproc) LOCALVERSION="
# stable release name (7.3.1-mt6750): modules in /lib/modules and the connsys .ko keep working across commits
mkdir -p $OUT

if [ ! -f $OUT/.config ] || [ "${1:-all}" = config ]; then
  $M defconfig
  C="$SRC/scripts/config --file $OUT/.config"
  # only MediaTek platform
  for a in $(grep -o '^CONFIG_ARCH_[A-Z0-9_]*=y' $OUT/.config | sed 's/CONFIG_//;s/=y//'); do
    case $a in ARCH_MEDIATEK|ARCH_HAS_*|ARCH_WANT_*|ARCH_SUPPORTS_*|ARCH_USE_*|ARCH_INLINE_*|ARCH_MMAP_*|ARCH_KEEP_*|ARCH_HIBERNATION_*|ARCH_SUSPEND_*|ARCH_SELECT_*|ARCH_ENABLE_*|ARCH_FORCE_*|ARCH_MHP_*|ARCH_PROC_*|ARCH_SPARSEMEM_*|ARCH_STACKWALK|ARCH_WANTS_*|ARCH_CORRECT_*|ARCH_BINFMT_*|ARCH_DMA_*|ARCH_FLATMEM_*|ARCH_HIBERNATION_POSSIBLE) ;;
    *) $C -d $a ;; esac
  done
  # big subsystems we don't need yet (keeps Image.gz well under the 16 MiB partition)
  for o in DRM SOUND SND MEDIA_SUPPORT NFC CAN VIRTUALIZATION KVM \
           XEN INFINIBAND NET_VENDOR_MELLANOX SCSI_UFSHCD PCI ATA MTD STAGING CHROME_PLATFORMS \
           SURFACE_PLATFORMS RC_CORE REMOTEPROC RPMSG FPGA \
           NFS_FS CIFS SMB_SERVER BTRFS_FS XFS_FS OCFS2_FS GFS2_FS FUSE_FS SECURITY_SELINUX \
           DEBUG_INFO DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT DEBUG_INFO_BTF FTRACE KPROBES \
           RANDOMIZE_BASE ARM64_MTE ARM64_PTR_AUTH ARM64_BTI ARM64_SVE ARM64_SME EFI \
           CPU_IDLE CPU_FREQ ARM_SCMI_PROTOCOL ARM_SCPI_PROTOCOL; do
    $C -d $o
  done
  $C -e DEBUG_INFO_NONE
  $C -d LOCALVERSION_AUTO --set-str LOCALVERSION -mt6750
  # backlight (DISP_PWM + pwm-backlight), built in so the screen dims early
  for o in BACKLIGHT_CLASS_DEVICE PWM PWM_MTK_DISP BACKLIGHT_PWM; do $C -e $o; done
  # SoC temperature (thermal controller + AUXADC, eFuse calibration)
  for o in MTK_THERMAL MTK_SOC_THERMAL; do $C -e $o; done
  # GPIO/EINT, I2C0, Goodix touchscreen
  for o in PINCTRL PINCTRL_MT6755 GPIOLIB I2C I2C_CHARDEV I2C_MT65XX INPUT_TOUCHSCREEN IIO QMA6100P; do $C -e $o; done
  $C -m TOUCHSCREEN_SILEAD   # needs its firmware from the rootfs: module
  # bring-up: allow /dev/mem reads of RAM (lk log_store at 0x7ff80000, stock-vs-mainline register work)
  $C -d STRICT_DEVMEM
  # bring-up essentials
  for o in PSTORE PSTORE_RAM PSTORE_CONSOLE PSTORE_PMSG PSTORE_DEFLATE_COMPRESS \
           FB FB_SIMPLE FRAMEBUFFER_CONSOLE SYSFB_SIMPLEFB VT VT_CONSOLE \
           WATCHDOG MEDIATEK_WATCHDOG WATCHDOG_HANDLE_BOOT_ENABLED \
           SERIAL_8250 SERIAL_8250_MT6577 SERIAL_8250_CONSOLE \
           BLK_DEV_INITRD RD_GZIP DEVTMPFS DEVTMPFS_MOUNT TMPFS PRINTK_TIME \
           MAGIC_SYSRQ PANIC_ON_OOPS IKCONFIG IKCONFIG_PROC; do
    $C -e $o
  done
  $C --set-val PANIC_TIMEOUT 10
  # stage 2: eMMC (mtk-sd), USB device (mtu3 + t-phy) with CDC Ethernet gadget
  for o in MMC MMC_BLOCK MMC_MTK EXT4_FS EFI_PARTITION PARTITION_ADVANCED REGULATOR REGULATOR_FIXED_VOLTAGE \
           USB_SUPPORT USB_GADGET USB_MTU3 USB_MTU3_DUAL_ROLE PHY_MTK_TPHY USB_ETH NETDEVICES INET DEBUG_FS \
           QUOTA QUOTACTL QUOTA_TREE QFMT_V2 FS_ENCRYPTION UNICODE; do
    $C -e $o
  done
  $C -d USB_MTU3_HOST -d USB_MTU3_GADGET -d USB_ETH_RNDIS -d USB_ETH_EEM
  # stage 5: connectivity (out-of-tree CONSYS modules: btif, wmt, wlan gen2) and NAT
  for o in WIRELESS CFG80211 CFG80211_DEFAULT_PS NETFILTER NF_CONNTRACK NF_NAT NF_TABLES NF_TABLES_IPV4 NFT_NAT NFT_MASQ; do
    $C -e $o
  done
  $C -m BT -m BT_HCIVHCI -m UHID -d WLAN -d MAC80211
  # Bluetooth: BLE + RFCOMM/BNEP/HIDP for BlueZ on the VHCI bridge (tablet-btbridge)
  $C -e HID -e HID_GENERIC -m UHID
  $C -e BT_LE -m BT_RFCOMM -e BT_RFCOMM_TTY -m BT_BNEP -e BT_BNEP_MC_FILTER -e BT_BNEP_PROTO_FILTER -m BT_HIDP
  $C --set-val WATCHDOG_OPEN_TIMEOUT 150
  $C --set-val SERIAL_EARLYCON_RAM_BASE 0xb00d0000 --set-val SERIAL_EARLYCON_RAM_HEAD 0xb00c0000
  # console: rotated for landscape (fbcon=rotate:3, console top at the panel's left edge) and,
  # unless BOOT_VERBOSE=1, quiet with deferred fbcon takeover: the boot logo stays on screen
  # (or tablet-splash animates) until the login prompt or an error message
  $C -e FRAMEBUFFER_CONSOLE_ROTATION -e FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER -d MTK_SOCINFO
  if [ "${BOOT_VERBOSE:-0}" = 1 ]; then VERB="loglevel=8 ignore_loglevel"; else VERB="quiet"; fi
  $C --set-str CMDLINE "console=tty0 fbcon=rotate:3 $VERB clk_ignore_unused pd_ignore_unused panic=10 rdinit=/init keep_bootcon g_ether.dev_addr=02:4d:54:00:00:02 g_ether.host_addr=02:4d:54:00:00:01"
  $C -e CMDLINE_FORCE -e SERIAL_EARLYCON_RAM_FORCE
  $M olddefconfig
fi
[ "${1:-all}" = config ] && exit 0

# resolve options added by config edits non-interactively (a NEW-option prompt would hang make forever)
$M olddefconfig > /dev/null
$M Image.gz dtbs > $OUT/build.log 2>&1 || { tail -30 $OUT/build.log; echo "BUILD FAILED (log: $OUT/build.log)"; exit 1; }
tail -5 $OUT/build.log
# modules (cfg80211, bt, ...) staged for the Debian rootfs: $OUT/modules/lib/modules/<ver>
$M modules > $OUT/modules.log 2>&1 || { tail -30 $OUT/modules.log; echo "MODULES BUILD FAILED"; exit 1; }
rm -rf $OUT/modules && $M INSTALL_MOD_PATH=$OUT/modules INSTALL_MOD_STRIP=1 modules_install > /dev/null 2>&1 || { echo "modules_install failed"; exit 1; }
echo "modules staged: $(ls $OUT/modules/lib/modules)"
D=$OUT/arch/arm64/boot
cat $D/Image.gz $D/dts/mediatek/mt6750-k50v1-tablet.dtb > $OUT/Image.gz-dtb
ls -la $D/Image $D/Image.gz $OUT/Image.gz-dtb

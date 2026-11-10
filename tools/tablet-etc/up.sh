#!/bin/sh
# MT6755 CONSYS bring-up (mainline): btif -> wmt -> launcher -> wifi chrdev -> cfg80211 -> wlan -> func-on
cd /root/connsys || exit 1
log() { echo "== $*"; echo "<5>CONNSYS-UP: $*" > /dev/kmsg; }
lsmod | grep -q mtk_btif_drv || insmod mtk_btif_drv.ko || exit 1
lsmod | grep -q mtk_stp_wmt_soc || insmod mtk_stp_wmt_soc.ko || exit 1
[ -c /dev/stpwmt ] || mknod /dev/stpwmt c 190 0
[ -c /dev/stpbt ] || mknod /dev/stpbt c 192 0
# GPS chrdev (WMT GPS function + LNA on GPIO114 while /dev/stpgps is open)
lsmod | grep -q mtk_stp_gps_soc || insmod mtk_stp_gps_soc.ko
[ -c /dev/stpgps ] || mknod /dev/stpgps c 191 0
# Bluetooth chrdev (bridged to BlueZ by tablet-btbridge.service)
lsmod | grep -q mtk_stp_bt_soc || insmod mtk_stp_bt_soc.ko
[ -c /dev/stpbt ] || mknod /dev/stpbt c 192 0
log "btif+wmt loaded"
if ! pgrep -x mtk_stp_launche >/dev/null; then
	nohup ./mtk_stp_launcher -m 3 -p /system/etc/firmware/ > /tmp/launcher.log 2>&1 &
	sleep 3
fi
log "launcher running"
[ "$1" = "wmt" ] && exit 0
lsmod | grep -q mtk_wmt_wifi_soc || insmod mtk_wmt_wifi_soc.ko || exit 1
[ -c /dev/wmtWifi ] || mknod /dev/wmtWifi c 155 0
modprobe cfg80211 || exit 1
lsmod | grep -q wlan_gen2 || insmod wlan_gen2.ko || exit 1
log "wlan modules loaded, func-on"
echo 1 > /dev/wmtWifi
for i in $(seq 1 20); do [ -d /sys/class/net/wlan0 ] && break; sleep 1; done
[ -d /sys/class/net/wlan0 ] && log "WLAN0 UP mac=$(cat /sys/class/net/wlan0/address)" || log "FAIL: no wlan0"
# 802.11 power save (measured: no latency penalty worth noting, ~10 ms to the AP)
[ -d /sys/class/net/wlan0 ] && iw dev wlan0 set power_save on

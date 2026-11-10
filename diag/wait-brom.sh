#!/bin/bash
# usage: wait-brom.sh <session> [seconds]; waits for MTK BROM (0e8d:0003) on ANY USB port, runs session once.
cd "$(dirname "$0")"; S=$1; T=${2:-36000}
rm -f .state; [ -e run-$S.log ] && { echo "session $S already ran"; exit 2; }
echo "ARMED $S $(date +%T)"
end=$((SECONDS+T))
while [ $SECONDS -lt $end ]; do
  for p in /sys/bus/usb/devices/*; do
    if [ "$(cat $p/idVendor 2>/dev/null)" = 0e8d ] && [ "$(cat $p/idProduct 2>/dev/null)" = 0003 ]; then
      echo "DETECTED $(date +%T) at $p"
      sudo -n "${MTK_PYTHON:-python3}" -u mtk-session.py $S > run-$S.log 2>&1
      rc=$?; echo "FINISHED rc=$rc $(date +%T)"; exit $rc
    fi
  done
  sleep 0.05
done
echo "TIMEOUT"; exit 3

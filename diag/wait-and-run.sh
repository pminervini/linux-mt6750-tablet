#!/bin/bash
# usage: wait-and-run.sh <session-name>; waits for MTK preloader on port 3-2, then runs that session once.
cd "$(dirname "$0")"; S=$1; PORT=/sys/bus/usb/devices/3-2; rm -f .state
[ -e run-$S.log ] && { echo "session $S already ran"; exit 2; }
echo "ARMED $S"
for i in $(seq 36000); do
  if [ "$(cat $PORT/idVendor 2>/dev/null)" = 0e8d ] && [ "$(cat $PORT/idProduct 2>/dev/null)" = 2000 -o "$(cat $PORT/idProduct 2>/dev/null)" = 0003 ]; then
    echo "DETECTED $(date -u +%T)"
    sudo -n "${MTK_PYTHON:-python3}" -u mtk-session.py $S > run-$S.log 2>&1
    rc=$?; echo "FINISHED rc=$rc"; exit $rc
  fi
  sleep 0.05
done
echo "TIMEOUT no preloader"; exit 3

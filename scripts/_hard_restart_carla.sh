#!/usr/bin/env bash
# Hard restart CARLA so sync-mode deadlocks cannot stick.
set -euo pipefail
pkill -9 -f CarlaUE4-Linux-Shipping || true
pkill -9 -f 'CarlaUE4.sh' || true
sleep 2
fuser -k 2000/tcp 2>/dev/null || true
fuser -k 2001/tcp 2>/dev/null || true
sleep 1
export DISPLAY="${DISPLAY:-:0}"
export CARLA_ROOT="${CARLA_ROOT:-$HOME/CARLA}"
cd "$CARLA_ROOT"
nohup bash ./CarlaUE4.sh -quality-level=Low -windowed -resx=1280 -resy=720 \
  -carla-rpc-port=2000 -carla-streaming-port=2001 \
  >/tmp/carla-ue4.log 2>&1 &
echo "started pid $!"
for i in $(seq 1 60); do
  if ss -ltn 2>/dev/null | grep -q ':2000'; then
    echo "READY after ${i}s"
    # give UE a few more seconds after bind
    sleep 5
    exit 0
  fi
  sleep 1
done
echo "FAILED to bind :2000" >&2
tail -n 40 /tmp/carla-ue4.log | strings || true
exit 1

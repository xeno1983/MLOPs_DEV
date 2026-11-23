#!/usr/bin/env bash
set -euo pipefail
pkill -9 -f CarlaUE4 || true
pkill -9 -f CarlaUE4-Linux-Shipping || true
sleep 2
# clear stale
fuser -k 2000/tcp 2>/dev/null || true
fuser -k 2001/tcp 2>/dev/null || true
sleep 1
pgrep -af CarlaUE4 || echo "CARLA stopped"
ss -ltn | grep -E ':2000|:2001' || echo "ports free"

#!/usr/bin/env bash
# CARLA mining_sim 走行中に AutoE2E 用 rosbag2 を録る（WSL）。
# 前提: CARLA :2000、mining_sim、本スクリプトと同ターミナルで odom bridge。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AW="${AUTOWARE_E2E_ROOT:-/mnt/c/Users/xeno1/Autoware_e2e}"
CARLA_ROOT="${CARLA_ROOT:-$HOME/CARLA}"
OUT_NAME="${1:-mine_autoe2e_001}"
OUT_DIR="${AW}/datasets/carla_bags/${OUT_NAME}"

source /opt/ros/jazzy/setup.bash 2>/dev/null || source /opt/ros/humble/setup.bash

EGG="$(ls -1 "${CARLA_ROOT}"/PythonAPI/carla/dist/carla-*.egg 2>/dev/null | head -1 || true)"
if [[ -n "$EGG" ]]; then
  export PYTHONPATH="${EGG}:${PYTHONPATH:-}"
fi

mkdir -p "$(dirname "$OUT_DIR")"
echo "recording -> $OUT_DIR"
echo "starting odom bridge in background…"
python3 "$ROOT/scripts/carla-odom-bridge.py" --host 127.0.0.1 --port 2000 &
ODOM_PID=$!
trap 'kill $ODOM_PID 2>/dev/null || true' EXIT

sleep 2
ros2 bag record \
  /carla/ego_vehicle/rgb/image \
  /carla/ego_vehicle/odometry \
  /carla/ego_vehicle/speed \
  /tf /tf_static \
  -o "$OUT_DIR"

echo "done: $OUT_DIR"
echo "next: MLOPS portal 工程1 import → ingest → dataset(task=autoe2e) → host worker train"

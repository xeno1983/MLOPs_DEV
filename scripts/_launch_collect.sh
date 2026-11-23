#!/usr/bin/env bash
# Launch WASD drive + rosbag collector against a running CARLA.
#   bash _launch_collect.sh Town01
#   NOSYNC=1 bash _launch_collect.sh Town01
set -euo pipefail
export DISPLAY="${DISPLAY:-:0}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
# shellcheck disable=SC1091
source "$HOME/.venv-carla915/bin/activate"
SP="$("$HOME/.venv-carla915/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
if [[ -f "$SP/carla.pth" ]]; then
  mv -f "$SP/carla.pth" "$SP/carla.pth.egg.bak"
fi
COLLECT=/mnt/c/Users/xeno1/MLOPS/scripts/carla_offroad_collect.py
sed -i 's/\r$//' "$COLLECT"
MAP="${1:-Town01}"
ARGS=(--host 127.0.0.1 -p 2000 --map "$MAP" --vehicle vehicle.tesla.cybertruck)
if [[ "${NOSYNC:-0}" != "1" ]]; then
  ARGS+=(--sync)
fi
exec python "$COLLECT" "${ARGS[@]}"

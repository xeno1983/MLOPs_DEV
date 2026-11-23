#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export SDL_VIDEODRIVER=dummy
export PYGAME_HIDE_SUPPORT_PROMPT=1
export PYTHONUNBUFFERED=1
# shellcheck disable=SC1091
source "$HOME/.venv-carla915/bin/activate"
sed -i 's/\r$//' /mnt/c/Users/xeno1/MLOPS/scripts/_smoke_collect.py /mnt/c/Users/xeno1/MLOPS/scripts/carla_offroad_collect.py
exec python /mnt/c/Users/xeno1/MLOPS/scripts/_smoke_collect.py

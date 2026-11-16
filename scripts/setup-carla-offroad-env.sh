#!/usr/bin/env bash
# WSL: CARLA 0.9.15 用の Python 3.10 venv + Mine_01 マップ準備
#   bash scripts/setup-carla-offroad-env.sh
#   bash scripts/setup-carla-offroad-env.sh --skip-mine
#
# Official CARLA 0.9.15 ships only a py3.7 egg (segfaults on 3.10).
# We install the manylinux cp310 wheel: pip package "carla-0-9-15".
set -euo pipefail

SKIP_MINE=0
for a in "$@"; do
  case "$a" in
    --skip-mine) SKIP_MINE=1 ;;
  esac
done

CARLA_ROOT="${CARLA_ROOT:-$HOME/CARLA}"
ENV_DIR="${CARLA_OFFROAD_ENV:-$HOME/.venv-carla915}"
MINE_CACHE="${CARLA_MINE_CACHE:-$HOME/carla-downloads/mine01_ue4}"
PY_VER="${CARLA_OFFROAD_PY:-3.10}"

echo "CARLA_ROOT=$CARLA_ROOT"
echo "ENV_DIR=$ENV_DIR (python $PY_VER)"

if [[ ! -d "$CARLA_ROOT" ]]; then
  echo "CARLA がありません: $CARLA_ROOT" >&2
  exit 1
fi

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (user)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [[ -x "$ENV_DIR/bin/python" ]]; then
  cur="$("$ENV_DIR/bin/python" -c 'import sys; print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)"
  if [[ "$cur" != "$PY_VER" ]]; then
    echo "Recreating venv ($cur -> $PY_VER)..."
    rm -rf "$ENV_DIR"
  fi
fi

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  echo "Creating venv with Python $PY_VER via uv..."
  uv python install "$PY_VER"
  uv venv "$ENV_DIR" --python "$PY_VER"
fi
# shellcheck disable=SC1091
source "$ENV_DIR/bin/activate"

uv pip install --python "$ENV_DIR/bin/python" \
  "setuptools==69.5.1" wheel \
  "pygame>=2.1" "numpy<2" "opencv-python-headless" "Pillow" \
  "rosbags>=0.10" "PyYAML" "gdown" \
  "carla-0-9-15"

# Do not use the bundled py3.7 egg (ABI crash on 3.8+)
SP="$("$ENV_DIR/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
if [[ -f "$SP/carla.pth" ]]; then
  mv -f "$SP/carla.pth" "$SP/carla.pth.egg.bak"
  echo "disabled egg path: $SP/carla.pth.egg.bak"
fi

"$ENV_DIR/bin/python" -c "import carla; print('carla OK', carla.__file__)"
"$ENV_DIR/bin/python" -c "import pygame, rosbags; print('pygame/rosbags OK')"

# --- Mine_01 (UE4 content migrated for 0.9.x) ---
MAPS="$CARLA_ROOT/CarlaUE4/Content/Carla/Maps"
if [[ "$SKIP_MINE" -eq 1 ]]; then
  echo "skip Mine_01 download"
elif ls "$MAPS"/Mine_01* >/dev/null 2>&1; then
  echo "Mine_01 already present"
else
  echo "Downloading Mine_01 UE4 content (Google Drive)..."
  mkdir -p "$MINE_CACHE"
  # Robotec.ai UE4 port of Mine_01 (Autoware off-road WG). Drive often requires login.
  MINE_URL="https://drive.google.com/drive/folders/15ig-afICo1bZpyeW6u0UaTgDk9-7lJIp"
  if ! python -m gdown "$MINE_URL" -O "$MINE_CACHE"; then
    echo "WARN: gdown cannot access Drive (login/permission)." >&2
    echo "  Manual: open $MINE_URL" >&2
    echo "  Download UE4_Content, then:" >&2
    echo "    cp -a /path/to/UE4_Content/. \"$MAPS\"/" >&2
    echo "  Until then start-carla-offroad falls back to Town07 (rural)." >&2
  fi
  SRC=""
  if [[ -d "$MINE_CACHE/UE4_Content" ]]; then
    SRC="$MINE_CACHE/UE4_Content"
  else
    SRC="$(find "$MINE_CACHE" -type d -name 'UE4_Content' 2>/dev/null | head -1 || true)"
  fi
  # Some mirrors put umap files directly under cache
  if [[ -z "$SRC" ]] && ls "$MINE_CACHE"/Mine_01* >/dev/null 2>&1; then
    SRC="$MINE_CACHE"
  fi
  if [[ -n "$SRC" ]]; then
    echo "Copying Mine_01 assets into $MAPS (CARLA must be stopped)..."
    mkdir -p "$MAPS"
    cp -a "$SRC"/. "$MAPS"/
    echo "Mine_01 installed. Restart CARLA."
  else
    echo "Mine_01 content not found after download. Manual copy needed." >&2
  fi
fi

echo ""
echo "=== done ==="
echo "activate: source $ENV_DIR/bin/activate"
echo "collect:  python /mnt/c/Users/xeno1/MLOPS/scripts/carla_offroad_collect.py --help"
ls "$MAPS"/Mine_01* 2>/dev/null | head || echo "(Mine_01 files not listed yet — Town07 fallback available)"

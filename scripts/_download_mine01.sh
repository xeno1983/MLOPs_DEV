#!/usr/bin/env bash
# Download UE4-migrated Mine_01 into CARLA Maps.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.venv-carla915/bin/activate"

CARLA_ROOT="${CARLA_ROOT:-$HOME/CARLA}"
MAPS="$CARLA_ROOT/CarlaUE4/Content/Carla/Maps"
MINE_CACHE="${CARLA_MINE_CACHE:-$HOME/carla-downloads/mine01_ue4}"
# Robotec.ai UE4 port (Autoware off-road WG)
FOLDER_ID="15ig-afICo1bZpyeW6u0UaTgDk9-7lJIp"
FOLDER_URL="https://drive.google.com/drive/folders/${FOLDER_ID}"

mkdir -p "$MINE_CACHE"
cd "$MINE_CACHE"

if ls "$MAPS"/Mine_01* >/dev/null 2>&1; then
  echo "Mine_01 already in Maps"
  ls "$MAPS"/Mine_01* | head
  exit 0
fi

echo "Downloading Mine_01 UE4_Content from Drive..."
echo "  $FOLDER_URL"
# Prefer folder download; fall back to known zip if published later
python -m gdown --folder "$FOLDER_URL" -O "$MINE_CACHE" 2>&1 | tee "$MINE_CACHE/gdown.log" | tail -80

echo "=== cache tree (depth 3) ==="
find "$MINE_CACHE" -maxdepth 3 -type d | head -80
find "$MINE_CACHE" -iname 'Mine_01*' | head -40
find "$MINE_CACHE" -type d -name 'UE4_Content' | head

SRC=""
if [[ -d "$MINE_CACHE/UE4_Content" ]]; then
  SRC="$MINE_CACHE/UE4_Content"
else
  SRC="$(find "$MINE_CACHE" -type d -name 'UE4_Content' 2>/dev/null | head -1 || true)"
fi
if [[ -z "$SRC" ]] && ls "$MINE_CACHE"/*/Mine_01* >/dev/null 2>&1; then
  SRC="$(dirname "$(find "$MINE_CACHE" -name 'Mine_01.umap' 2>/dev/null | head -1)")"
fi

if [[ -z "$SRC" ]]; then
  echo "DOWNLOAD incomplete — listing cache:" >&2
  ls -la "$MINE_CACHE" >&2 || true
  exit 3
fi

echo "Installing from $SRC -> $MAPS"
mkdir -p "$MAPS"
cp -a "$SRC"/. "$MAPS"/
ls "$MAPS"/Mine_01* | head
echo "Mine_01 installed"

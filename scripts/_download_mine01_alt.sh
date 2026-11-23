#!/usr/bin/env bash
# Try alternate sources for Mine_01 / mining CARLA package.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.venv-carla915/bin/activate"

CACHE="$HOME/carla-downloads"
mkdir -p "$CACHE"
cd "$CACHE"

try_folder() {
  local url="$1"
  echo "=== try folder $url ==="
  python -m gdown "$url" -O "$CACHE/mine01_try" 2>&1 | tail -20 || true
}

try_file() {
  local fid="$1"
  local out="$2"
  echo "=== try file $fid -> $out ==="
  if [[ -f "$out" ]]; then
    echo "already have $out"
    return 0
  fi
  python -m gdown "https://drive.google.com/uc?id=${fid}" -O "$out" 2>&1 | tail -40
}

# Older mirror mentioned in earlier notes
try_folder "https://drive.google.com/drive/folders/1r_PGuSETMCvuR3-TL510_pgOjSu7xW23"

# Full CARLA 0.9.16 + Mine01 binary (Autoware off-road WG)
# https://drive.google.com/file/d/1LhYgvC6IaKZYs21neDLKOCwurSjWdaJ1/view
try_file "1LhYgvC6IaKZYs21neDLKOCwurSjWdaJ1" "$CACHE/CARLA_0.9.16_Mine01.bin_or_zip"

ls -lah "$CACHE" | head -30
find "$CACHE" -iname '*Mine*' 2>/dev/null | head

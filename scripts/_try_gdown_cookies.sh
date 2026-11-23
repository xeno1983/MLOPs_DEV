#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.venv-carla915/bin/activate"

# Try browser cookies from Windows Chrome/Edge profiles (WSL path)
for browser in chrome edge chromium firefox; do
  echo "=== cookies-from-browser $browser ==="
  python -m gdown --cookies-from-browser "$browser" \
    "https://drive.google.com/drive/folders/15ig-afICo1bZpyeW6u0UaTgDk9-7lJIp" \
    -O "$HOME/carla-downloads/mine01_ue4" 2>&1 | tail -25 && exit 0 || true
done

# Windows cookie DB paths often visible under /mnt/c/Users/...
COOKIE_CANDIDATES=(
  "/mnt/c/Users/xeno1/AppData/Local/Google/Chrome/User Data/Default/Cookies"
  "/mnt/c/Users/xeno1/AppData/Local/Microsoft/Edge/User Data/Default/Cookies"
)
for c in "${COOKIE_CANDIDATES[@]}"; do
  echo "cookie path exists? $c -> $([[ -f "$c" ]] && echo yes || echo no)"
done
exit 1

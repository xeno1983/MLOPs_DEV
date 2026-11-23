#!/usr/bin/env bash
# WSL 内で CARLA 0.9.15 を ~/CARLA に展開する（Mine_01 は別途）。
set -euo pipefail

CARLA_VERSION="${CARLA_VERSION:-0.9.15}"
DEST="${CARLA_ROOT:-$HOME/CARLA}"
DL_DIR="${CARLA_DOWNLOAD_DIR:-$HOME/carla-downloads}"
# b-cdn は 403 になることがある → Backblaze を既定にする
CARLA_URL="${CARLA_URL:-https://carla-releases.s3.us-east-005.backblazeb2.com/Linux/CARLA_${CARLA_VERSION}.tar.gz}"
MAPS_URL="${CARLA_MAPS_URL:-https://carla-releases.s3.us-east-005.backblazeb2.com/Linux/AdditionalMaps_${CARLA_VERSION}.tar.gz}"
DOWNLOAD="${1:-}"

mkdir -p "$DL_DIR" "$DEST"
cd "$DL_DIR"

echo "== CARLA setup =="
echo "DEST=$DEST"
echo "URL=$CARLA_URL"

need_download=0
if [[ ! -f "$DEST/CarlaUE4.sh" ]]; then
  need_download=1
fi

download_file() {
  local out="$1"
  local url="$2"
  if command -v wget >/dev/null; then
    wget -c -O "$out" "$url"
  else
    curl -L -C - -A "Mozilla/5.0" -o "$out" "$url"
  fi
}

if [[ "$DOWNLOAD" == "--download" || "$DOWNLOAD" == "-Download" ]]; then
  if [[ "$need_download" -eq 1 ]]; then
    echo "Downloading CARLA ${CARLA_VERSION} (~8GB。時間がかかります)…"
    if ! download_file "CARLA_${CARLA_VERSION}.tar.gz" "$CARLA_URL"; then
      echo "主 URL 失敗。tiny.carla.org 経由を試します…"
      download_file "CARLA_${CARLA_VERSION}.tar.gz" "https://tiny.carla.org/carla-0-9-15-linux"
    fi
    echo "Extracting into $DEST …"
    tar -xzf "CARLA_${CARLA_VERSION}.tar.gz" -C "$DEST" --strip-components=0 2>/dev/null \
      || tar -xzf "CARLA_${CARLA_VERSION}.tar.gz" -C "$DEST"
    # アーカイブがサブディレクトリの場合を吸収
    if [[ ! -f "$DEST/CarlaUE4.sh" ]]; then
      inner="$(find "$DEST" -maxdepth 2 -name CarlaUE4.sh | head -1 || true)"
      if [[ -n "$inner" ]]; then
        inner_dir="$(dirname "$inner")"
        if [[ "$inner_dir" != "$DEST" ]]; then
          shopt -s dotglob
          mv "$inner_dir"/* "$DEST"/ || true
          shopt -u dotglob
        fi
      fi
    fi
  else
    echo "CarlaUE4.sh 既存: $DEST — 本体 DL スキップ"
  fi

  if [[ "${CARLA_INSTALL_MAPS:-1}" == "1" ]]; then
    echo "Downloading AdditionalMaps (optional)…"
    download_file "AdditionalMaps_${CARLA_VERSION}.tar.gz" "$MAPS_URL" || true
    if [[ -f "AdditionalMaps_${CARLA_VERSION}.tar.gz" ]]; then
      tar -xzf "AdditionalMaps_${CARLA_VERSION}.tar.gz" -C "$DEST" || true
    fi
  fi
else
  echo "ドライラン: --download を付けると本体を取得します"
fi

if [[ -f "$DEST/CarlaUE4.sh" ]]; then
  chmod +x "$DEST/CarlaUE4.sh" || true
  echo "OK: $DEST/CarlaUE4.sh"
else
  echo "WARN: CarlaUE4.sh がまだありません。 --download で取得してください。"
  exit 2
fi

# Python クライアント egg（mining_sim 用）
EGG="$(ls -1 "$DEST"/PythonAPI/carla/dist/carla-*.egg 2>/dev/null | head -1 || true)"
if [[ -n "$EGG" ]]; then
  echo "Python egg: $EGG"
  echo "  使い方: export PYTHONPATH=\"$EGG:\${PYTHONPATH:-}\""
else
  echo "WARN: PythonAPI egg 未検出（mining_sim で carla import が必要な場合あり）"
fi

if ! ls "$DEST"/CarlaUE4/Content/Carla/Maps/Mine_01* >/dev/null 2>&1 \
  && ! ls "$DEST"/Import/Mine_01* >/dev/null 2>&1; then
  echo "NOTE: Mine_01 マップがありません。mining_sim は load_world('Mine_01') します。"
  echo "  Autoware off-road の Mine_01 アセットを Import/ に置き ImportAssets.sh を実行してください。"
fi

# 依存のヒント（自動 apt はしない）
echo "依存ヒント: sudo apt install libomp5 vulkan-tools"
echo "export CARLA_ROOT=$DEST"
echo "DONE"

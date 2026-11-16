#!/usr/bin/env bash
# WSL で CARLA を起動（画面あり: CARLA_MODE=window）
set +e
export DISPLAY="${DISPLAY:-:0}"
export CARLA_ROOT="${CARLA_ROOT:-$HOME/CARLA}"
export LD_LIBRARY_PATH="/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"

cd "$CARLA_ROOT" || exit 1

# 既存が生きていて :2000 が開いていれば再利用
if ss -ltn 2>/dev/null | grep -q ':2000'; then
  if pgrep -f 'CarlaUE4-Linux-Shipping' >/dev/null 2>&1; then
    echo "CARLA already listening on :2000"
    ss -ltn | grep -E '2000|2001'
    exit 0
  fi
fi

pkill -9 -f 'CarlaUE4-Linux-Shipping' 2>/dev/null
pkill -9 -f './CarlaUE4.sh' 2>/dev/null
sleep 2

QUALITY="${CARLA_QUALITY:-Low}"
MODE="${CARLA_MODE:-window}"
EXTRA=()
case "$MODE" in
  offscreen) EXTRA+=(-RenderOffScreen) ;;
  vulkan) EXTRA+=(-vulkan) ;;
  window) EXTRA+=(-windowed -resx=1280 -resy=720) ;;
esac

: > /tmp/carla-ue4.log
setsid ./CarlaUE4.sh -quality-level="$QUALITY" "${EXTRA[@]}" \
  -carla-rpc-port=2000 -carla-streaming-port=2001 \
  >> /tmp/carla-ue4.log 2>&1 &
echo "PID=$! MODE=$MODE DISPLAY=$DISPLAY"

for i in $(seq 1 60); do
  if ss -ltn 2>/dev/null | grep -q ':2000'; then
    echo "READY port 2000 after $((i*2))s"
    ss -ltn | grep -E '2000|2001'
    pgrep -af 'CarlaUE4-Linux-Shipping' | head -2
    exit 0
  fi
  # プロセスが完全消滅してログに fatal があれば失敗
  if ! pgrep -f 'CarlaUE4' >/dev/null 2>&1; then
    sleep 1
    if ! pgrep -f 'CarlaUE4' >/dev/null 2>&1; then
      echo "CarlaUE4 exited early (MODE=$MODE)"
      strings /tmp/carla-ue4.log 2>/dev/null | tail -n 40
      exit 1
    fi
  fi
  sleep 2
done

echo "TIMEOUT waiting for :2000"
strings /tmp/carla-ue4.log 2>/dev/null | tail -n 40
pgrep -af Carla || true
exit 2

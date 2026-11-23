#!/usr/bin/env bash
set +e
echo "=== processes ==="
pgrep -af CarlaUE4 || echo none
echo "=== port 2000 ==="
ss -ltn | grep 2000 || echo "not listening"
echo "=== DISPLAY ==="
echo "DISPLAY=${DISPLAY:-unset}"
ls /mnt/wslg >/dev/null 2>&1 && echo "WSLg present" || echo "no WSLg"
echo "=== vulkan ==="
command -v vulkaninfo >/dev/null && vulkaninfo --summary 2>&1 | head -30 || echo "no vulkaninfo"
echo "=== glx ==="
command -v glxinfo >/dev/null && glxinfo -B 2>&1 | head -15 || echo "no glxinfo"
echo "=== nvidia ==="
nvidia-smi 2>&1 | head -12 || true
echo "=== last log ==="
tail -n 50 /tmp/carla-ue4.log 2>/dev/null || echo no-log

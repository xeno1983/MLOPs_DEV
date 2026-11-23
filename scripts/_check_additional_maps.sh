#!/usr/bin/env bash
set -euo pipefail
TAR="$HOME/carla-downloads/AdditionalMaps_0.9.15.tar.gz"
echo "tar size:" $(stat -c%s "$TAR")
echo "=== Mine_01 entries in AdditionalMaps ==="
tar -tzf "$TAR" 2>/dev/null | grep -i 'Mine_01' | head -40 || echo "(no Mine_01)"
echo "=== miningtruck ==="
tar -tzf "$TAR" 2>/dev/null | grep -i 'miningtruck\|Mining' | head -40 || echo "(none)"
echo "=== top-level ==="
tar -tzf "$TAR" 2>/dev/null | head -30

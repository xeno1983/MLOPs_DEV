#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
# Prefer clean env: manylinux wheel should not need jammy libtiff
unset LD_LIBRARY_PATH || true
source "$HOME/.venv-carla915/bin/activate"
python - <<'PY'
import carla, pygame, rosbags, sys
print("python", sys.version)
print("carla", carla.__file__)
print("pygame", pygame.__version__)
print("rosbags OK")
# smoke: Client class exists
assert hasattr(carla, "Client")
print("Client OK")
PY
echo "=== maps ==="
ls "$HOME/CARLA/CarlaUE4/Content/Carla/Maps"/Mine_01* 2>/dev/null | head || echo "Mine_01 missing"
ls "$HOME/CARLA/CarlaUE4/Content/Carla/Maps"/Town07* 2>/dev/null | head || echo "Town07 missing"
echo "=== carla server? ==="
ss -ltn 2>/dev/null | grep -E ':2000|:2001' || netstat -ltn 2>/dev/null | grep -E ':2000|:2001' || echo "(ports not listening / tools missing)"

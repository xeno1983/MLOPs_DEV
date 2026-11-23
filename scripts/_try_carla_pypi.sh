#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/carla-libs/min:${LD_LIBRARY_PATH:-}"

ENV="$HOME/.venv-carla915"
source "$ENV/bin/activate"

echo "=== try pip carla-0-9-15 ==="
uv pip install --python "$ENV/bin/python" "carla-0-9-15" 2>&1 | tail -30
# remove egg path so we use the wheel
SP="$("$ENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
if [[ -f "$SP/carla.pth" ]]; then
  mv "$SP/carla.pth" "$SP/carla.pth.bak"
  echo "moved carla.pth aside"
fi
python - <<'PY'
import traceback
try:
    import carla
    print("OK", getattr(carla, "__file__", carla), getattr(carla, "__version__", "?"))
except Exception:
    traceback.print_exc()
PY

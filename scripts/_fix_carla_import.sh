#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
ENV_DIR="${CARLA_OFFROAD_ENV:-$HOME/.venv-carla915}"
# shellcheck disable=SC1091
source "$ENV_DIR/bin/activate"
uv pip install --python "$ENV_DIR/bin/python" "setuptools==69.5.1"
python -c "import pkg_resources; print('pkg_resources OK')"
EGG="$(ls -1 "$HOME"/CARLA/PythonAPI/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg | head -1)"
SP="$(python -c 'import site; print(site.getsitepackages()[0])')"
echo "$EGG" > "$SP/carla.pth"
python -c "import carla; print('carla OK', carla.__file__)"

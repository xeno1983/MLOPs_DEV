#!/usr/bin/env bash
set -euo pipefail
EGG="$HOME/CARLA/PythonAPI/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg"
LIBDIR="$HOME/.local/carla-libs/root/usr/lib/x86_64-linux-gnu"
TD=$(mktemp -d)
python3 - <<PY
import zipfile, os
egg = "$EGG"
td = "$TD"
with zipfile.ZipFile(egg) as z:
    for n in z.namelist():
        if n.endswith(".so") or n.endswith(".so.1"):
            z.extract(n, td)
            print(os.path.join(td, n))
PY
SO=$(find "$TD" -name '*.so' | head -1)
echo "SO=$SO"
echo "=== ldd (system) ==="
ldd "$SO" || true
echo "=== missing ==="
ldd "$SO" 2>/dev/null | grep "not found" || echo "(none)"

echo "=== only libtiff.so.5 symlink dir ==="
MIN="$HOME/.local/carla-libs/min"
mkdir -p "$MIN"
cp -a "$LIBDIR"/libtiff.so.5* "$MIN/" 2>/dev/null || true
# also need jammy deps that libtiff5 itself needs if noble doesn't satisfy
for n in libjbig.so.0 libwebp.so.7 libjpeg.so.8 liblzma.so.5 libdeflate.so.0 libzstd.so.1 libz.so.1; do
  if [[ -e "$LIBDIR/$n" ]]; then cp -a "$LIBDIR"/$n* "$MIN/" 2>/dev/null || true; fi
done
export LD_LIBRARY_PATH="$MIN"
ldd "$SO" 2>/dev/null | grep -E "not found|libtiff" || true

echo "=== python versions ==="
command -v python3.7 || true
command -v python3.8 || true
command -v python3.10 || true
~/.local/bin/python3.10 -V || true

echo "=== try py3.10 with MIN path ==="
export LD_LIBRARY_PATH="$MIN"
source "$HOME/.venv-carla915/bin/activate"
python -c "import carla; print('OK', carla.__file__)" && echo PY310_OK || echo PY310_FAIL

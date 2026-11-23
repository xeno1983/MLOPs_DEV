#!/usr/bin/env bash
# Install carla egg shared-lib deps into $HOME without sudo (dpkg -x).
set -euo pipefail
LIBROOT="${CARLA_OFFROAD_LIBS:-$HOME/.local/carla-libs}"
mkdir -p "$LIBROOT/debs" "$LIBROOT/root"
cd "$LIBROOT/debs"

# CARLA 0.9.15 egg was built against Ubuntu 20.04/22.04 (libtiff.so.5).
# Pull jammy packages by URL so Noble hosts still work.
urls=(
  "http://archive.ubuntu.com/ubuntu/pool/main/t/tiff/libtiff5_4.3.0-6ubuntu0.13_amd64.deb"
  "http://archive.ubuntu.com/ubuntu/pool/main/libw/libwebp/libwebp7_1.2.2-2ubuntu0.22.04.2_amd64.deb"
  "http://archive.ubuntu.com/ubuntu/pool/main/j/jbigkit/libjbig0_2.1-3.1ubuntu0.22.04.1_amd64.deb"
  "http://archive.ubuntu.com/ubuntu/pool/main/libj/libjpeg-turbo/libjpeg-turbo8_2.1.2-0ubuntu1_amd64.deb"
  "http://archive.ubuntu.com/ubuntu/pool/main/x/xz-utils/liblzma5_5.2.5-2ubuntu1_amd64.deb"
  "http://archive.ubuntu.com/ubuntu/pool/main/libd/libdeflate/libdeflate0_1.10-2_amd64.deb"
  "http://archive.ubuntu.com/ubuntu/pool/main/libp/libpng1.6/libpng16-16_1.6.37-3build5_amd64.deb"
)
for u in "${urls[@]}"; do
  f="$(basename "$u")"
  if [[ ! -f "$f" ]]; then
    curl -fL --retry 3 -o "$f" "$u" || echo "WARN: failed $u" >&2
  fi
done

for deb in *.deb; do
  [[ -f "$deb" ]] || continue
  dpkg-deb -x "$deb" "$LIBROOT/root" || true
done

libdir="$LIBROOT/root/usr/lib/x86_64-linux-gnu"
echo "export LD_LIBRARY_PATH=\"$libdir:\${LD_LIBRARY_PATH:-}\"" > "$LIBROOT/env.sh"
# shellcheck disable=SC1091
source "$LIBROOT/env.sh"
ls -la "$libdir"/libtiff* 2>/dev/null || echo "libtiff missing"
export PATH="$HOME/.local/bin:$PATH"
# shellcheck disable=SC1091
source "$HOME/.venv-carla915/bin/activate"
python -c "import carla; print('carla OK', carla.__file__)"

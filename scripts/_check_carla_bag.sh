#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.venv-carla915/bin/activate"
BAG="${1:-/mnt/c/Users/xeno1/Autoware_e2e/datasets/carla_bags/smoke_20261109_153201}"
python - <<PY
from pathlib import Path
from rosbags.rosbag2 import Reader
from rosbags.typesys import Stores, get_typestore

bag = Path("$BAG")
print("bag:", bag)
print("exists:", bag.exists())
for p in sorted(bag.iterdir()):
    print(f"  {p.name}: {p.stat().st_size} bytes")

ts = get_typestore(Stores.ROS2_JAZZY)
with Reader(bag) as reader:
    print("connections:", len(reader.connections))
    for c in reader.connections:
        print(f"  {c.topic}  [{c.msgtype}]")
    counts = {c.id: 0 for c in reader.connections}
    first_t = last_t = None
    for conn, t, raw in reader.messages():
        counts[conn.id] += 1
        first_t = t if first_t is None else first_t
        last_t = t
    print("message counts:")
    for c in reader.connections:
        print(f"  {c.topic}: {counts[c.id]}")
    if first_t is not None:
        print(f"time_ns: {first_t} .. {last_t}  ({(last_t-first_t)/1e9:.2f}s span)")
print("OK")
PY

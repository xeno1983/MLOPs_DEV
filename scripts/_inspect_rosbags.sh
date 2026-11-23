#!/usr/bin/env bash
source "$HOME/.venv-carla915/bin/activate"
python <<'PY'
import inspect
from rosbags.rosbag2 import Writer
from rosbags.typesys import Stores
print("version ok")
print("Stores", [x for x in dir(Stores) if x.startswith("ROS2") or x=="LATEST"])
print("sig", inspect.signature(Writer.add_connection))
PY

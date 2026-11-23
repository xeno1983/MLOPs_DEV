#!/usr/bin/env bash
source "$HOME/.venv-carla915/bin/activate"
python <<'PY'
from rosbags.typesys import Stores, get_typestore
import inspect
ts = get_typestore(Stores.ROS2_JAZZY)
Image = ts.types['sensor_msgs/msg/Image']
print(Image)
print(getattr(Image, '__annotations__', None) or Image.__dataclass_fields__)
# try serialize with memoryview / array
import array, numpy as np
from rosbags.typesys.stores.latest import builtin_interfaces__msg__Time as Time
# get nested types
Header = ts.types['std_msgs/msg/Header']
Time = ts.types['builtin_interfaces/msg/Time']
h, w = 2, 3
raw = np.zeros((h, w, 3), dtype=np.uint8)
for candidate_name, candidate in [
    ('bytes', raw.tobytes()),
    ('bytearray', bytearray(raw.tobytes())),
    ('memoryview', memoryview(raw.tobytes())),
    ('arrayB', array.array('B', raw.tobytes())),
    ('ndarray', raw.reshape(-1)),
    ('list', raw.reshape(-1).tolist()),
]:
    try:
        img = Image(
            header=Header(stamp=Time(sec=0, nanosec=0), frame_id='x'),
            height=h, width=w, encoding='bgr8', is_bigendian=0, step=w*3,
            data=candidate,
        )
        b = ts.serialize_cdr(img, 'sensor_msgs/msg/Image')
        print('OK', candidate_name, len(b))
    except Exception as e:
        print('FAIL', candidate_name, type(e).__name__, e)
PY

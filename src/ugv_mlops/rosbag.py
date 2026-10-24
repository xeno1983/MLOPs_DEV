from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from ugv_mlops.settings import settings

IMAGE_MSGTYPES = {
    "sensor_msgs/msg/Image",
    "sensor_msgs/msg/CompressedImage",
    "sensor_msgs/Image",
    "sensor_msgs/CompressedImage",
}
IMU_MSGTYPES = {"sensor_msgs/msg/Imu", "sensor_msgs/Imu"}
GNSS_MSGTYPES = {"sensor_msgs/msg/NavSatFix", "sensor_msgs/NavSatFix"}
ODOM_MSGTYPES = {"nav_msgs/msg/Odometry", "nav_msgs/Odometry"}
TWIST_MSGTYPES = {
    "geometry_msgs/msg/Twist",
    "geometry_msgs/msg/TwistStamped",
    "geometry_msgs/Twist",
    "geometry_msgs/TwistStamped",
    "ackermann_msgs/msg/AckermannDrive",
    "ackermann_msgs/msg/AckermannDriveStamped",
    "ackermann_msgs/AckermannDrive",
    "ackermann_msgs/AckermannDriveStamped",
}
LIDAR_MSGTYPES = {
    "sensor_msgs/msg/PointCloud2",
    "sensor_msgs/PointCloud2",
    "sensor_msgs/msg/LaserScan",
    "sensor_msgs/LaserScan",
}
PC2_DTYPE = {1: "i1", 2: "u1", 3: "<i2", 4: "<u2", 5: "<i4", 6: "<u4", 7: "<f4", 8: "<f8"}


def is_rosbag(path: Path) -> bool:
    path = Path(path)
    if path.is_file() and path.suffix.lower() in {".mcap", ".bag", ".db3"}:
        return True
    if path.is_dir() and (path / "metadata.yaml").exists():
        return True
    return False


def discover_bags(root: Path) -> list[Path]:
    root = Path(root)
    dirs: list[Path] = []
    files: list[Path] = []
    if is_rosbag(root):
        (dirs if root.is_dir() else files).append(root)
    if root.is_dir():
        for meta in root.rglob("metadata.yaml"):
            dirs.append(meta.parent)
        for ext in (".mcap", ".bag"):
            files.extend(root.rglob(f"*{ext}"))
    bag_dirs = {p.resolve() for p in dirs}
    uniq: list[Path] = []
    seen: set[Path] = set()
    for item in dirs + files:
        resolved = item.resolve()
        if resolved in seen:
            continue
        if item.is_file() and any(_is_under(resolved, d) for d in bag_dirs):
            continue
        seen.add(resolved)
        uniq.append(item)
    return uniq


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return path != parent
    except ValueError:
        return False


def camera_folder(topic: str) -> str:
    return topic_modality(topic, "image").removeprefix("camera_")


def topic_modality(topic: str, kind: str) -> str:
    lower = topic.lower()
    if kind == "image":
        if any(k in lower for k in ("rear", "back")):
            return "camera_rear"
        if "left" in lower:
            return "camera_left"
        if "right" in lower:
            return "camera_right"
        return "camera_front"
    return kind


def classify_msg(topic: str, msgtype: str) -> str | None:
    if msgtype in IMAGE_MSGTYPES:
        return "image"
    if msgtype in IMU_MSGTYPES or "/imu" in topic.lower():
        return "imu"
    if msgtype in GNSS_MSGTYPES or any(k in topic.lower() for k in ("gnss", "gps", "/fix")):
        return "gnss"
    if msgtype in ODOM_MSGTYPES or "odom" in topic.lower() or "kinematic" in topic.lower():
        return "odom"
    if msgtype in LIDAR_MSGTYPES or any(k in topic.lower() for k in ("lidar", "points", "velodyne", "hesai")):
        return "lidar"
    if msgtype in TWIST_MSGTYPES or any(
        k in topic.lower() for k in ("cmd_vel", "control_cmd", "vehicle_cmd", "steering", "ackermann", "control/command")
    ):
        return "action"
    return None


def list_topics(bag_path: Path) -> list[dict]:
    from rosbags.highlevel import AnyReader

    paths = [bag_path] if bag_path.is_file() else [bag_path]
    with AnyReader(paths) as reader:
        return [
            {
                "topic": c.topic,
                "msgtype": c.msgtype,
                "msgcount": getattr(c, "msgcount", None),
                "kind": classify_msg(c.topic, c.msgtype),
            }
            for c in reader.connections
        ]


def extract_bag(bag_path: Path, dest_dir: Path) -> dict:
    """Extract camera frames and other sensor streams. ROS install is not required."""
    return extract_bags([bag_path], dest_dir)


def extract_bags(bags: list[Path], dest_dir: Path) -> dict:
    """DVC GUI と同じ配置: cameras/<front>/, lidar/*.bin, localization/, series/。"""
    from rosbags.highlevel import AnyReader

    dest_dir.mkdir(parents=True, exist_ok=True)
    cameras = dest_dir / "cameras"
    lidar_dir = dest_dir / "lidar"
    loc = dest_dir / "localization"
    series_dir = dest_dir / "series"
    for folder in (cameras, lidar_dir, loc, series_dir):
        folder.mkdir(parents=True, exist_ok=True)

    allow = {t.strip() for t in settings.rosbag_topics.split(",") if t.strip()}
    stride = max(int(settings.rosbag_frame_stride), 1)
    cap = max(int(settings.rosbag_max_frames_per_topic), 1)
    lidar_stride = max(int(settings.rosbag_lidar_stride), 1)
    lidar_cap = max(int(settings.rosbag_max_lidar_keyframes), 1)
    max_points = max(int(settings.rosbag_max_lidar_points), 1)

    frames: list[dict] = []
    lidar_files: list[dict] = []
    series: dict[str, list[dict]] = {"imu": [], "gnss": [], "odom": [], "action": [], "lidar": []}
    counters: dict[str, int] = {}
    saved: dict[str, int] = {}
    topics_seen: list[dict] = []

    gnss_f = (loc / "gnss.jsonl").open("w", encoding="utf-8")
    odom_f = (loc / "odom.jsonl").open("w", encoding="utf-8")
    imu_f = (series_dir / "imu.jsonl").open("w", encoding="utf-8")
    action_f = (series_dir / "action.jsonl").open("w", encoding="utf-8")
    lidar_meta = (lidar_dir / "keyframes.jsonl").open("w", encoding="utf-8")

    try:
        with AnyReader(bags) as reader:
            topics_seen = [
                {"topic": c.topic, "msgtype": c.msgtype, "kind": classify_msg(c.topic, c.msgtype)}
                for c in reader.connections
            ]
            for connection, timestamp, raw in reader.messages():
                kind = classify_msg(connection.topic, connection.msgtype)
                if kind is None:
                    continue
                if allow and kind == "image" and connection.topic not in allow:
                    continue
                ts = datetime.fromtimestamp(timestamp / 1e9, tz=timezone.utc).isoformat()
                seen = counters.get(connection.topic, 0)
                counters[connection.topic] = seen + 1
                try:
                    msg = reader.deserialize(raw, connection.msgtype)
                except Exception:
                    continue

                if kind == "image":
                    if seen % stride != 0 or saved.get(connection.topic, 0) >= cap:
                        continue
                    try:
                        image = _image_from_msg(msg, connection.msgtype)
                    except Exception:
                        continue
                    folder_name = camera_folder(connection.topic)
                    folder = cameras / folder_name
                    folder.mkdir(exist_ok=True)
                    name = f"{timestamp}.jpg"
                    out = folder / name
                    image.save(out, format="JPEG", quality=90)
                    saved[connection.topic] = saved.get(connection.topic, 0) + 1
                    relpath = f"cameras/{folder_name}/{name}"
                    frames.append(
                        {
                            "path": out,
                            "topic": connection.topic,
                            "msgtype": connection.msgtype,
                            "ts": ts,
                            "modality": topic_modality(connection.topic, "image"),
                            "width": image.width,
                            "height": image.height,
                            "relpath": relpath,
                        }
                    )
                    continue

                if kind == "lidar":
                    if seen % lidar_stride != 0 or saved.get(connection.topic, 0) >= lidar_cap:
                        continue
                    try:
                        rec = _lidar_record(msg, connection, ts, timestamp, lidar_dir, max_points)
                    except Exception:
                        continue
                    lidar_meta.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    series["lidar"].append({k: rec[k] for k in rec if k != "file"})
                    saved[connection.topic] = saved.get(connection.topic, 0) + 1
                    if rec.get("file"):
                        lidar_files.append(
                            {
                                "path": lidar_dir / rec["file"],
                                "relpath": f"lidar/{rec['file']}",
                                "ts": ts,
                                "topic": connection.topic,
                                "msgtype": connection.msgtype,
                                "n_points": rec.get("n_points", 0),
                            }
                        )
                    continue

                rec = {"ts": ts, "topic": connection.topic, "msgtype": connection.msgtype}
                rec.update(_sensor_record(kind, msg))
                line = json.dumps(rec, ensure_ascii=False) + "\n"
                series[kind].append(rec)
                if kind == "gnss":
                    gnss_f.write(line)
                elif kind == "odom":
                    odom_f.write(line)
                elif kind == "imu":
                    imu_f.write(line)
                elif kind == "action":
                    action_f.write(line)
    finally:
        gnss_f.close()
        odom_f.close()
        imu_f.close()
        action_f.close()
        lidar_meta.close()

    bag_files: list[Path] = []
    for bag in bags:
        bag_files.extend(_bag_files(bag))
    return {
        "frames": frames,
        "lidar_files": lidar_files,
        "series": series,
        "topics": topics_seen,
        "bag_files": bag_files,
        "counts": {
            "images": len(frames),
            "lidar_keyframes": len(lidar_files) or len(series["lidar"]),
            "gnss": len(series["gnss"]),
            "odom": len(series["odom"]),
            "imu": len(series["imu"]),
            "action": len(series["action"]),
        },
    }


def _bag_files(bag_path: Path) -> list[Path]:
    bag_path = Path(bag_path)
    if bag_path.is_file():
        files = [bag_path]
        meta = bag_path.parent / "metadata.yaml"
        if meta.exists():
            files.append(meta)
        return files
    return [p for p in bag_path.rglob("*") if p.is_file()]


def _sensor_record(kind: str, msg) -> dict:
    if kind == "imu":
        lin = getattr(msg, "linear_acceleration", None)
        ang = getattr(msg, "angular_velocity", None)
        ori = getattr(msg, "orientation", None)
        return {
            "ax": _xyz(lin, "x"),
            "ay": _xyz(lin, "y"),
            "az": _xyz(lin, "z"),
            "gx": _xyz(ang, "x"),
            "gy": _xyz(ang, "y"),
            "gz": _xyz(ang, "z"),
            "qw": _xyz(ori, "w"),
            "qx": _xyz(ori, "x"),
            "qy": _xyz(ori, "y"),
            "qz": _xyz(ori, "z"),
        }
    if kind == "gnss":
        return {
            "lat": _num(getattr(msg, "latitude", None)),
            "lon": _num(getattr(msg, "longitude", None)),
            "alt": _num(getattr(msg, "altitude", None)),
            "status": _num(getattr(getattr(msg, "status", None), "status", None)),
        }
    if kind == "odom":
        pose = getattr(getattr(msg, "pose", None), "pose", None)
        twist = getattr(getattr(msg, "twist", None), "twist", None)
        pos = getattr(pose, "position", None)
        return {
            "x": _xyz(pos, "x"),
            "y": _xyz(pos, "y"),
            "z": _xyz(pos, "z"),
            "speed": _xyz(getattr(twist, "linear", None), "x"),
            "yawrate": _xyz(getattr(twist, "angular", None), "z"),
        }
    if kind == "action":
        return _action_from_msg(msg)
    return {}


def _action_from_msg(msg) -> dict:
    drive = getattr(msg, "drive", None)
    if drive is not None and hasattr(drive, "steering_angle"):
        return {
            "speed": _num(getattr(drive, "speed", None)),
            "steer": _num(getattr(drive, "steering_angle", None)),
            "accel": _num(getattr(drive, "acceleration", None)),
        }
    if hasattr(msg, "steering_angle") and hasattr(msg, "speed"):
        return {
            "speed": _num(getattr(msg, "speed", None)),
            "steer": _num(getattr(msg, "steering_angle", None)),
            "accel": _num(getattr(msg, "acceleration", None)),
        }
    if hasattr(msg, "linear") and hasattr(msg, "angular"):
        return {"speed": _xyz(msg.linear, "x"), "steer": _xyz(msg.angular, "z"), "accel": None}
    inner = getattr(msg, "twist", None) or getattr(msg, "drive", None) or getattr(msg, "control", None) or msg
    if hasattr(inner, "linear") and hasattr(inner, "angular"):
        return {"speed": _xyz(inner.linear, "x"), "steer": _xyz(inner.angular, "z"), "accel": None}
    longi = getattr(inner, "longitudinal", None) or inner
    lat = getattr(inner, "lateral", None) or inner
    speed = _first(longi, ["velocity", "speed", "vx"])
    accel = _first(longi, ["acceleration", "accel"])
    steer = _first(lat, ["steering_tire_angle", "steering_angle", "steering"])
    if steer is None:
        steer = _first(inner, ["steering_angle", "steering_tire_angle", "angular.z"])
    return {"speed": speed, "steer": steer, "accel": accel}


def _lidar_record(msg, connection, ts: str, timestamp: int, lidar_dir: Path, max_points: int) -> dict:
    rec = {
        "ts": ts,
        "topic": connection.topic,
        "msgtype": connection.msgtype,
        "bag_time_ns": timestamp,
        "file": None,
        "n_points": 0,
        "width": int(getattr(msg, "width", 0) or 0),
        "height": int(getattr(msg, "height", 0) or 0),
        "point_step": int(getattr(msg, "point_step", 0) or 0),
    }
    xyz = xyz_from_pointcloud2(msg, max_points)
    if xyz is None:
        ranges = getattr(msg, "ranges", None)
        if ranges is not None:
            rec["n_points"] = len(ranges)
            rec["scan_type"] = "laserscan"
        return rec
    slug = topic_modality(connection.topic, "lidar")
    if slug == "lidar":
        lower = connection.topic.lower()
        slug = "front"
        if any(k in lower for k in ("rear", "back")):
            slug = "rear"
        elif "left" in lower:
            slug = "left"
        elif "right" in lower:
            slug = "right"
    name = f"{slug}_{timestamp}.bin"
    (lidar_dir / name).write_bytes(xyz.astype("<f4").tobytes())
    rec["file"] = name
    rec["n_points"] = int(xyz.shape[0])
    return rec


def xyz_from_pointcloud2(msg, max_points: int) -> np.ndarray | None:
    fields = {f.name: f for f in getattr(msg, "fields", [])}
    if not all(k in fields for k in ("x", "y", "z")):
        return None
    point_step = int(getattr(msg, "point_step", 0) or 0)
    raw = getattr(msg, "data", None)
    if raw is None:
        return None
    if isinstance(raw, np.ndarray):
        data = raw.tobytes()
    else:
        data = bytes(raw)
    if point_step <= 0 or not data:
        return None
    n = min(len(data) // point_step, max_points)
    if n <= 0:
        return None
    out = np.zeros((n, 3), dtype=np.float32)
    arr = np.frombuffer(data, dtype=np.uint8)
    needed = n * point_step
    for i, axis in enumerate(("x", "y", "z")):
        field = fields[axis]
        dtype = PC2_DTYPE.get(int(getattr(field, "datatype", 7)))
        if dtype is None:
            return None
        offset = int(field.offset)
        view = np.ndarray(shape=(n,), dtype=dtype, buffer=arr[:needed], offset=offset, strides=(point_step,))
        out[:, i] = view.astype(np.float32)
    good = np.isfinite(out).all(axis=1)
    return out[good]


def _image_from_msg(msg, msgtype: str) -> Image.Image:
    if "CompressedImage" in msgtype:
        return Image.open(BytesIO(bytes(msg.data))).convert("RGB")
    height, width = int(msg.height), int(msg.width)
    encoding = str(getattr(msg, "encoding", "rgb8")).lower()
    buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if encoding in {"rgb8", "bgr8"}:
        arr = buf.reshape((height, width, 3))
        if encoding == "bgr8":
            arr = arr[:, :, ::-1].copy()
        return Image.fromarray(arr, "RGB")
    if encoding in {"mono8", "8uc1"}:
        return Image.fromarray(buf.reshape((height, width)), "L").convert("RGB")
    if encoding in {"rgba8", "bgra8"}:
        arr = buf.reshape((height, width, 4))
        if encoding == "bgra8":
            arr = arr[:, :, [2, 1, 0, 3]]
        return Image.fromarray(arr, "RGBA").convert("RGB")
    raise ValueError(f"unsupported image encoding: {encoding}")


def _xyz(obj, axis: str) -> float | None:
    if obj is None:
        return None
    return _num(getattr(obj, axis, None))


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(obj, names: list[str]) -> float | None:
    if obj is None:
        return None
    for name in names:
        if "." in name:
            cur = obj
            for part in name.split("."):
                cur = getattr(cur, part, None)
                if cur is None:
                    break
            got = _num(cur)
        else:
            got = _num(getattr(obj, name, None))
        if got is not None:
            return got
    return None

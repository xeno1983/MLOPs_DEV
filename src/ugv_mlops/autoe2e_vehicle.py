"""自前 rosbag2（カメラ / GNSS / IMU / 車速 / 任意で odom）→ AutoE2E 学習データ準備。

AutoE2E 切出し（Autoware `tools/rosbag2_autoe2e`）が必須とするのは:
  - カメラ画像（最低 front。1cam なら fill_missing_slots）
  - ego pose: nav_msgs/Odometry | PoseStamped | TF

実機 bag に多い GNSS / IMU / 車速だけでは切出せないため、本モジュールが
  1) トピック検査と rig YAML 下書き
  2) GNSS(+車速/IMU) → `/autoe2e/odometry` を持つ派生 bag
を用意する。LiDAR は AutoE2E 学習に不要（保持してよいが切出しでは使わない）。
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import yaml

from ugv_mlops.rosbag import (
    TWIST_MSGTYPES,
    classify_msg,
    discover_bags,
    is_rosbag,
)
from ugv_mlops.settings import settings

SYNTH_ODOM_TOPIC = "/autoe2e/odometry"
POSE_BAG_NAME = "autoe2e_pose"

# WGS84
_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)


def _bags_from_path(path: Path) -> list[Path]:
    path = Path(path)
    if is_rosbag(path):
        return [path]
    bags = [b for b in discover_bags(path) if b.is_dir() or b.suffix.lower() in {".mcap", ".db3"}]
    if not bags:
        raise FileNotFoundError(f"rosbag2 が見つかりません: {path}")
    return bags


def resolve_mission_bags(mission_id: str | None = None, bag_path: str | Path | None = None) -> list[Path]:
    if bag_path:
        return _bags_from_path(Path(bag_path))
    if not mission_id:
        raise ValueError("mission_id か bag_path が必要です")
    from ugv_mlops.bag_pipeline import raw_dir

    for base in (raw_dir(mission_id), settings.inbox_path / mission_id):
        if base.exists():
            bags = [b for b in discover_bags(base) if b.is_dir() or b.suffix.lower() in {".mcap", ".db3"}]
            # 派生 pose bag は後段で足すので、ここでは元 bag だけ（pose bag 名は除外可）
            bags = [b for b in bags if POSE_BAG_NAME not in b.name]
            if bags:
                return bags
    raise FileNotFoundError(
        f"{mission_id}: bag が data/dvc/raw/missions にも data/inbox にもありません。"
    )


def inspect_bags(bags: list[Path]) -> dict:
    """トピック表と AutoE2E 向けの示唆。"""
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    topics: list[dict] = []
    duration_s = 0.0
    with AnyReader([Path(b) if b.is_dir() else b for b in bags], default_typestore=get_typestore(Stores.LATEST)) as reader:
        duration_s = max(reader.duration, 1) / 1e9
        for conn in reader.connections:
            count = int(getattr(conn, "msgcount", 0) or 0)
            kind = classify_msg(conn.topic, conn.msgtype)
            if kind is None:
                # 車速の素 Float / Twist など
                low = conn.topic.lower()
                mt = conn.msgtype
                if any(k in low for k in ("speed", "velocity", "vehicle_speed", "wheel")):
                    kind = "speed"
                elif mt in TWIST_MSGTYPES:
                    kind = "speed"
                elif "CameraInfo" in mt:
                    kind = "camera_info"
                elif mt.endswith("/TFMessage") or mt.endswith("/tfMessage"):
                    kind = "tf"
            topics.append(
                {
                    "topic": conn.topic,
                    "msgtype": conn.msgtype,
                    "count": count,
                    "hz": round(count / duration_s, 2),
                    "kind": kind,
                    "suggested_slot": _suggest_camera_slot(conn.topic, conn.msgtype),
                }
            )
    by_kind: dict[str, list[dict]] = {}
    for t in topics:
        by_kind.setdefault(t["kind"] or "other", []).append(t)

    cameras = [t for t in topics if t["kind"] == "image"]
    odom = [t for t in topics if t["kind"] == "odom"]
    gnss = [t for t in topics if t["kind"] == "gnss"]
    imu = [t for t in topics if t["kind"] == "imu"]
    speed = [t for t in topics if t["kind"] == "speed"]
    lidar = [t for t in topics if t["kind"] == "lidar"]

    pose_ready = bool(odom) or any(t["topic"] == SYNTH_ODOM_TOPIC for t in topics)
    needs_pose_synth = (not pose_ready) and bool(gnss)

    return {
        "ok": bool(cameras) and (pose_ready or needs_pose_synth),
        "bags": [str(b) for b in bags],
        "duration_s": round(duration_s, 2),
        "topics": sorted(topics, key=lambda x: x["topic"]),
        "by_kind": {k: [x["topic"] for x in v] for k, v in by_kind.items()},
        "summary": {
            "cameras": len(cameras),
            "odom": len(odom),
            "gnss": len(gnss),
            "imu": len(imu),
            "speed": len(speed),
            "lidar": len(lidar),
            "pose_ready": pose_ready,
            "needs_pose_synth": needs_pose_synth,
        },
        "autoe2e": {
            "required": ["camera(image)", "ego_pose(odom|pose_stamped|tf)"],
            "optional_for_learning": ["camera_info", "tf_static"],
            "not_used_by_autoe2e": ["lidar", "raw imu (unless fused into pose)", "map"],
            "hint": (
                "odom がある → draft-rig して extract"
                if pose_ready
                else (
                    "GNSS あり → prepare-pose で /autoe2e/odometry を合成してから extract"
                    if needs_pose_synth
                    else "カメラと pose（odom か GNSS）が不足しています"
                )
            ),
        },
    }


def _suggest_camera_slot(topic: str, msgtype: str) -> str | None:
    if "Image" not in msgtype:
        return None
    t = topic.lower()
    rear = any(k in t for k in ("rear", "back"))
    left = "left" in t
    right = "right" in t
    if rear and left:
        return "rear_left"
    if rear and right:
        return "rear_right"
    if rear:
        return "rear"
    if left:
        return "front_left"
    if right:
        return "front_right"
    if any(k in t for k in ("front", "camera", "rgb", "color", "image")):
        return "front"
    return None


def draft_rig(
    inspection: dict,
    *,
    rig_id: str = "vehicle-1cam",
    base_frame: str = "base_link",
    camera_topic: str | None = None,
    camera_info: str | None = None,
    pose_topic: str | None = None,
    pose_source: str = "odom",
    fill_missing_slots: str = "front",
    extrinsic: dict | None = None,
    intrinsics: dict | None = None,
) -> dict:
    """inspection から rig YAML 用 dict を作る（未指定スロットは推測）。"""
    topics = inspection.get("topics") or []
    images = [t for t in topics if t.get("kind") == "image"]
    infos = [t for t in topics if t.get("kind") == "camera_info"]
    odoms = [t for t in topics if t.get("kind") == "odom"]
    synth = next((t for t in topics if t["topic"] == SYNTH_ODOM_TOPIC), None)

    if not camera_topic:
        # prefer suggested front, else first image
        front = next((t for t in images if t.get("suggested_slot") == "front"), None)
        camera_topic = (front or (images[0] if images else {})).get("topic")
    if not camera_topic:
        raise RuntimeError("画像トピックがありません。bag にカメラを含めてください。")

    if camera_info is None:
        # same prefix heuristic
        cam_base = camera_topic.replace("/compressed", "").replace("/image_raw", "").replace("/image", "")
        for info in infos:
            if info["topic"].startswith(cam_base) or cam_base in info["topic"]:
                camera_info = info["topic"]
                break

    if not pose_topic:
        if synth:
            pose_topic = SYNTH_ODOM_TOPIC
        elif odoms:
            pose_topic = odoms[0]["topic"]
        else:
            pose_topic = SYNTH_ODOM_TOPIC  # after prepare-pose

    cam_entry: dict[str, Any] = {
        "topic": camera_topic,
        "frame": "camera_front_optical",
        "convention": "optical",
    }
    if camera_info:
        cam_entry["camera_info"] = camera_info
    # CameraInfo が無い場合の仮値（必ず実測で差し替え）
    if not camera_info:
        cam_entry["extrinsic"] = extrinsic or {"xyz": [0.5, 0.0, 1.2], "rpy": [0.0, 0.0, 0.0]}
        cam_entry["intrinsics"] = intrinsics or {
            "fx": 600.0,
            "fy": 600.0,
            "cx": 640.0,
            "cy": 360.0,
            "width": 1280,
            "height": 720,
            "distortion": [],
            "model": "plumb_bob",
        }
    elif extrinsic:
        cam_entry["extrinsic"] = extrinsic
    if intrinsics:
        cam_entry["intrinsics"] = intrinsics

    cameras: dict[str, Any] = {"front": cam_entry}
    # 他スロットが bag にあれば載せる
    used = {camera_topic}
    for t in images:
        slot = t.get("suggested_slot")
        if not slot or slot == "front" or t["topic"] in used:
            continue
        if slot in cameras:
            continue
        entry: dict[str, Any] = {"topic": t["topic"], "frame": f"camera_{slot}", "convention": "body"}
        cameras[slot] = entry
        used.add(t["topic"])

    fill = fill_missing_slots if len(cameras) < 6 else ""
    rig = {
        "rig_id": rig_id,
        "base_frame": base_frame,
        "cameras": cameras,
        "pose": {"source": pose_source, "topic": pose_topic},
        "extract": {
            "rate_hz": 10.0,
            "image_size": 512,
            "front_native_size": 1024,
            "fill_missing_slots": fill,
            "max_sync_ms": 80.0,
            "max_pose_gap_s": 0.5,
            "use_header_stamp": True,
            "undistort": False,
            "stop_speed_mps": 0.15,
            "max_stop_s": 3.0,
            "min_frames": 180,
            "max_scene_s": 180.0,
            "max_pose_step_m": 5.0,
        },
        "_comment": (
            "AutoE2E 用。CameraInfo が無い場合の intrinsics/extrinsic は仮値です。"
            "実機キャリブに差し替えてください。LiDAR は学習に使いません。"
        ),
    }
    return rig


def write_rig_yaml(rig: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in rig.items() if not str(k).startswith("_")}
    comment = rig.get("_comment")
    text = ""
    if comment:
        text += "# " + str(comment).replace("\n", "\n# ") + "\n"
    text += yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def mission_rig_path(mission_id: str) -> Path:
    return settings.work_path / "autoe2e" / "rigs" / f"{mission_id}.yaml"


def pose_bag_dir(mission_id: str) -> Path:
    return settings.work_path / "autoe2e" / "pose_bags" / mission_id / POSE_BAG_NAME


# --------------------------------------------------------------------------- GNSS → odom


def _llh_to_ecef(lat_deg: float, lon_deg: float, alt: float) -> tuple[float, float, float]:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    n = _A / math.sqrt(1.0 - _E2 * sin_lat * sin_lat)
    x = (n + alt) * cos_lat * cos_lon
    y = (n + alt) * cos_lat * sin_lon
    z = (n * (1.0 - _E2) + alt) * sin_lat
    return x, y, z


def _ecef_to_enu(
    x: float, y: float, z: float, lat0_deg: float, lon0_deg: float, alt0: float
) -> tuple[float, float, float]:
    x0, y0, z0 = _llh_to_ecef(lat0_deg, lon0_deg, alt0)
    dx, dy, dz = x - x0, y - y0, z - z0
    lat0, lon0 = math.radians(lat0_deg), math.radians(lon0_deg)
    sin_lat, cos_lat = math.sin(lat0), math.cos(lat0)
    sin_lon, cos_lon = math.sin(lon0), math.cos(lon0)
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    return east, north, up


def _yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    """yaw about +Z (ENU), return xyzw."""
    half = 0.5 * yaw
    return (0.0, 0.0, math.sin(half), math.cos(half))


def _pick_topic(topics: list[dict], kind: str, override: str | None) -> str | None:
    if override:
        return override
    for t in topics:
        if t.get("kind") == kind:
            return t["topic"]
    return None


def synthesize_odom_bag(
    bags: list[Path],
    out_dir: Path,
    *,
    gnss_topic: str | None = None,
    imu_topic: str | None = None,
    speed_topic: str | None = None,
    odom_topic: str = SYNTH_ODOM_TOPIC,
    min_speed_mps: float = 0.3,
) -> dict:
    """GNSS(+任意 IMU/車速)から nav_msgs/Odometry だけの派生 rosbag2 を書く。"""
    from rosbags.highlevel import AnyReader
    from rosbags.rosbag2 import Writer
    from rosbags.typesys import Stores, get_typestore

    inspection = inspect_bags(bags)
    topics = inspection["topics"]
    gnss_topic = gnss_topic or _pick_topic(topics, "gnss", None)
    imu_topic = imu_topic or _pick_topic(topics, "imu", None)
    speed_topic = speed_topic or _pick_topic(topics, "speed", None)
    if not gnss_topic:
        raise RuntimeError("GNSS (sensor_msgs/NavSatFix) トピックがありません。odom があるなら prepare-pose は不要です。")

    typestore = get_typestore(Stores.LATEST)
    Odometry = typestore.types["nav_msgs/msg/Odometry"]
    Header = typestore.types["std_msgs/msg/Header"]
    Pose = typestore.types["geometry_msgs/msg/Pose"]
    PoseWithCovariance = typestore.types["geometry_msgs/msg/PoseWithCovariance"]
    Point = typestore.types["geometry_msgs/msg/Point"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    Twist = typestore.types["geometry_msgs/msg/Twist"]
    TwistWithCovariance = typestore.types["geometry_msgs/msg/TwistWithCovariance"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Time = typestore.types["builtin_interfaces/msg/Time"]

    wanted = {gnss_topic}
    if imu_topic:
        wanted.add(imu_topic)
    if speed_topic:
        wanted.add(speed_topic)

    gnss_samples: list[tuple[int, float, float, float]] = []
    imu_yaw: list[tuple[int, float]] = []
    speed_samples: list[tuple[int, float]] = []

    bag_paths = [Path(b) if Path(b).is_dir() else Path(b) for b in bags]
    with AnyReader(bag_paths, default_typestore=typestore) as reader:
        selected = [c for c in reader.connections if c.topic in wanted]
        for conn, ts, raw in reader.messages(connections=selected):
            try:
                msg = reader.deserialize(raw, conn.msgtype)
            except Exception:
                continue
            if conn.topic == gnss_topic:
                try:
                    stamp = int(msg.header.stamp.sec) * 1_000_000_000 + int(msg.header.stamp.nanosec)
                    if stamp <= 0:
                        stamp = int(ts)
                except Exception:
                    stamp = int(ts)
                status = getattr(getattr(msg, "status", None), "status", 0)
                # STATUS_NO_FIX = -1
                if status is not None and int(status) < 0:
                    continue
                gnss_samples.append((stamp, float(msg.latitude), float(msg.longitude), float(getattr(msg, "altitude", 0.0) or 0.0)))
            elif imu_topic and conn.topic == imu_topic:
                try:
                    stamp = int(msg.header.stamp.sec) * 1_000_000_000 + int(msg.header.stamp.nanosec)
                    if stamp <= 0:
                        stamp = int(ts)
                except Exception:
                    stamp = int(ts)
                q = msg.orientation
                # yaw from quaternion (ENU / base_link z-up)
                siny_cosp = 2.0 * (float(q.w) * float(q.z) + float(q.x) * float(q.y))
                cosy_cosp = 1.0 - 2.0 * (float(q.y) * float(q.y) + float(q.z) * float(q.z))
                imu_yaw.append((stamp, math.atan2(siny_cosp, cosy_cosp)))
            elif speed_topic and conn.topic == speed_topic:
                stamp = int(ts)
                try:
                    hdr = getattr(msg, "header", None)
                    if hdr is not None and getattr(hdr, "stamp", None) is not None:
                        stamp = int(hdr.stamp.sec) * 1_000_000_000 + int(hdr.stamp.nanosec)
                        if stamp <= 0:
                            stamp = int(ts)
                except Exception:
                    stamp = int(ts)
                spd = _speed_from_msg(msg, conn.msgtype)
                if spd is not None:
                    speed_samples.append((stamp, spd))

    if len(gnss_samples) < 10:
        raise RuntimeError(f"GNSS サンプルが足りません ({len(gnss_samples)}). fix 済み点を確認してください。")

    gnss_samples.sort(key=lambda x: x[0])
    imu_yaw.sort(key=lambda x: x[0])
    speed_samples.sort(key=lambda x: x[0])

    lat0, lon0, alt0 = gnss_samples[0][1], gnss_samples[0][2], gnss_samples[0][3]
    enu: list[tuple[int, float, float, float]] = []
    for stamp, lat, lon, alt in gnss_samples:
        x, y, z = _llh_to_ecef(lat, lon, alt)
        e, n, u = _ecef_to_enu(x, y, z, lat0, lon0, alt0)
        # AutoE2E / REP-103: x forward, y left. Map ENU east/north → body-ish world: x=north, y=-east
        enu.append((stamp, n, -e, u))

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    last_yaw = 0.0
    with Writer(out_dir, version=9) as writer:
        conn = writer.add_connection(odom_topic, "nav_msgs/msg/Odometry")
        for i, (stamp, x, y, z) in enumerate(enu):
            # heading
            yaw = last_yaw
            spd = _interp_scalar(speed_samples, stamp)
            imu_y = _interp_scalar(imu_yaw, stamp)
            if imu_y is not None and (spd is None or spd >= min_speed_mps):
                yaw = float(imu_y)
            elif i + 1 < len(enu):
                dx = enu[i + 1][1] - x
                dy = enu[i + 1][2] - y
                if math.hypot(dx, dy) > 0.05:
                    yaw = math.atan2(dy, dx)
            elif i > 0:
                dx = x - enu[i - 1][1]
                dy = y - enu[i - 1][2]
                if math.hypot(dx, dy) > 0.05:
                    yaw = math.atan2(dy, dx)
            last_yaw = yaw
            qx, qy, qz, qw = _yaw_to_quat(yaw)

            vx = 0.0
            if i + 1 < len(enu):
                dt = (enu[i + 1][0] - stamp) / 1e9
                if dt > 1e-3:
                    vx = math.hypot(enu[i + 1][1] - x, enu[i + 1][2] - y) / dt
            if spd is not None:
                vx = float(spd)

            sec = int(stamp // 1_000_000_000)
            nsec = int(stamp % 1_000_000_000)
            msg = Odometry(
                header=Header(stamp=Time(sec=sec, nanosec=nsec), frame_id="map"),
                child_frame_id="base_link",
                pose=PoseWithCovariance(
                    pose=Pose(
                        position=Point(x=float(x), y=float(y), z=float(z)),
                        orientation=Quaternion(x=qx, y=qy, z=qz, w=qw),
                    ),
                    covariance=[0.0] * 36,
                ),
                twist=TwistWithCovariance(
                    twist=Twist(
                        linear=Vector3(x=float(vx), y=0.0, z=0.0),
                        angular=Vector3(x=0.0, y=0.0, z=0.0),
                    ),
                    covariance=[0.0] * 36,
                ),
            )
            raw = typestore.serialize_cdr(msg, "nav_msgs/msg/Odometry")
            writer.write(conn, stamp, raw)
            written += 1

    meta = {
        "odom_topic": odom_topic,
        "gnss_topic": gnss_topic,
        "imu_topic": imu_topic,
        "speed_topic": speed_topic,
        "samples": written,
        "origin_llh": [lat0, lon0, alt0],
        "out_dir": str(out_dir),
        "note": "ENU origin = first GNSS fix; x=north, y=-east (approx body world). Prefer LIO/RTK odom when available.",
    }
    (out_dir.parent / "pose_synth.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def _speed_from_msg(msg: Any, msgtype: str) -> float | None:
    # scalar
    for attr in ("data", "speed", "velocity", "linear_velocity"):
        if hasattr(msg, attr):
            v = getattr(msg, attr)
            if isinstance(v, (int, float)):
                return abs(float(v))
    # Twist / TwistStamped
    twist = getattr(msg, "twist", None)
    if twist is not None:
        lin = getattr(twist, "linear", None) or getattr(getattr(twist, "twist", None), "linear", None)
        if lin is not None:
            return math.hypot(float(lin.x), float(lin.y))
    if hasattr(msg, "linear"):
        lin = msg.linear
        return math.hypot(float(lin.x), float(lin.y))
    # Ackermann
    drive = getattr(msg, "drive", None)
    if drive is not None and hasattr(drive, "speed"):
        return abs(float(drive.speed))
    return None


def _interp_scalar(samples: list[tuple[int, float]], stamp: int) -> float | None:
    if not samples:
        return None
    if stamp <= samples[0][0]:
        return samples[0][1]
    if stamp >= samples[-1][0]:
        return samples[-1][1]
    lo, hi = 0, len(samples) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if samples[mid][0] <= stamp:
            lo = mid
        else:
            hi = mid
    t0, v0 = samples[lo]
    t1, v1 = samples[hi]
    if t1 == t0:
        return v0
    a = (stamp - t0) / (t1 - t0)
    return v0 * (1 - a) + v1 * a


# --------------------------------------------------------------------------- prepare


def prepare_vehicle_autoe2e(
    *,
    mission_id: str | None = None,
    bag_path: str | Path | None = None,
    rig_id: str | None = None,
    camera_topic: str | None = None,
    gnss_topic: str | None = None,
    imu_topic: str | None = None,
    speed_topic: str | None = None,
    pose_topic: str | None = None,
    force_pose_synth: bool = False,
    write_autoware_rig: bool = False,
) -> dict:
    """inspect → (必要なら) pose 合成 → rig YAML。build_dataset の前段。"""
    bags = resolve_mission_bags(mission_id, bag_path)
    mid = mission_id or Path(bags[0]).name
    inspection = inspect_bags(bags)
    summary = inspection["summary"]

    pose_meta = None
    extract_bags = list(bags)
    if force_pose_synth or summary.get("needs_pose_synth"):
        out = pose_bag_dir(mid)
        pose_meta = synthesize_odom_bag(
            bags,
            out,
            gnss_topic=gnss_topic,
            imu_topic=imu_topic,
            speed_topic=speed_topic,
        )
        extract_bags.append(out)
        # re-inspect with pose bag for draft
        inspection = inspect_bags(extract_bags)
        pose_topic = pose_topic or SYNTH_ODOM_TOPIC
    elif summary.get("pose_ready") and not pose_topic:
        odoms = inspection["by_kind"].get("odom") or []
        pose_topic = odoms[0] if odoms else SYNTH_ODOM_TOPIC

    rig = draft_rig(
        inspection,
        rig_id=rig_id or f"{mid}-autoe2e",
        camera_topic=camera_topic,
        pose_topic=pose_topic,
    )
    rig_path = write_rig_yaml(rig, mission_rig_path(mid))
    copied = None
    if write_autoware_rig:
        from ugv_mlops.autoe2e import autoware_root

        dest = autoware_root() / "config" / "rosbag2_autoe2e" / f"rig.{mid}.yaml"
        shutil.copy2(rig_path, dest)
        copied = str(dest)

    report = {
        "ok": True,
        "mission_id": mid,
        "bags": [str(b) for b in bags],
        "extract_bags": [str(b) for b in extract_bags],
        "inspection": {
            "duration_s": inspection["duration_s"],
            "summary": inspection["summary"],
            "autoe2e": inspection["autoe2e"],
            "by_kind": inspection["by_kind"],
        },
        "pose_synth": pose_meta,
        "rig_path": str(rig_path),
        "autoware_rig_copy": copied,
        "next": [
            f"AUTOE2E_RIG を {rig_path} にするか、build 時に同 path を渡す",
            "ingest 済みなら POST /datasets/build task=autoe2e mission_id=…",
            "または: python -m ugv_mlops.cli autoe2e-build --mission-id …",
            "host worker (MLOPS_gpu_worker.bat) が必要",
        ],
    }
    report_path = settings.work_path / "autoe2e" / "rigs" / f"{mid}.prepare.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return report


def extract_bags_for_mission(mission_id: str, prepare_pose: bool = True) -> tuple[list[Path], Path | None]:
    """build_autoe2e_dataset 用: 切出し対象 bag と mission rig（あれば）。"""
    bags = resolve_mission_bags(mission_id)
    pose = pose_bag_dir(mission_id)
    if prepare_pose:
        inspection = inspect_bags(bags)
        need = inspection["summary"].get("needs_pose_synth") or not mission_rig_path(mission_id).is_file()
        if need:
            prepare_vehicle_autoe2e(mission_id=mission_id)
            bags = resolve_mission_bags(mission_id)
    out = list(bags)
    if pose.exists():
        out.append(pose)
    rig = mission_rig_path(mission_id)
    return out, (rig if rig.is_file() else None)

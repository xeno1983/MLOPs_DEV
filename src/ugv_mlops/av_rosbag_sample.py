"""Public AV-style ROS2 bag sample.

Official Autoware sample bags omit cameras (privacy). CostNav Teleop is a public
ROS 2 Jazzy MCAP with front camera, LiDAR, IMU, odom, and /cmd_vel — enough for
this stack's bag ingest → E2E dataset → train path.

Source: https://huggingface.co/datasets/maum-ai/CostNav-Teleop-Dataset
Episode: collector_A/recording_20260202_052239 (~59 s). We keep a short trim.
"""
from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

from ugv_mlops.settings import settings

MISSION_ID = "M-AVBAG-001"
HF_BASE = "https://huggingface.co/datasets/maum-ai/CostNav-Teleop-Dataset/resolve/main"
EPISODE = "collector_A/recording_20260202_052239"
MCAP_NAME = "recording_20260202_052239_0.mcap"
LICENSE_NOTE = (
    "CostNav Teleop Dataset (maum-ai/CostNav-Teleop-Dataset). "
    "Outdoor last-mile robot: camera + LiDAR + cmd_vel. Not a classified UGV dump."
)
KEEP_TOPICS = {
    "/front_stereo_camera/left/image_raw/compressed",
    "/cmd_vel",
    "/chassis/imu",
    "/chassis/odom",
    "/front_3d_lidar/lidar_points",
}


def _cache_dir() -> Path:
    path = Path(settings.yolox_cache_dir) / "av_rosbag" / "recording_20260202_052239"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "ugv-mlops/1.0"})
    with urllib.request.urlopen(req, timeout=600) as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)
    tmp.replace(dest)
    return dest


def download_costnav_episode() -> Path:
    """Fetch one public rosbag2 directory (metadata.yaml + mcap) into cache."""
    cache = _cache_dir()
    _download(f"{HF_BASE}/{EPISODE}/metadata.yaml", cache / "metadata.yaml")
    _download(f"{HF_BASE}/{EPISODE}/{MCAP_NAME}", cache / MCAP_NAME)
    return cache


def _trim_bag(src: Path, dest: Path, max_seconds: float, image_stride: int, lidar_stride: int) -> dict:
    from rosbags.highlevel import AnyReader
    from rosbags.rosbag2 import Writer

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    written = 0
    t0 = None
    src_dir = src if src.is_dir() else src.parent
    with AnyReader([src_dir]) as reader, Writer(dest, version=9) as writer:
        conn_map = {}
        for conn in reader.connections:
            if conn.topic not in KEEP_TOPICS:
                continue
            extra: dict = {}
            msgdef = getattr(conn, "msgdef", None)
            if msgdef is not None:
                extra["msgdef"] = getattr(msgdef, "data", msgdef)
            if getattr(conn, "digest", None):
                extra["rihs01"] = conn.digest
            ext = getattr(conn, "ext", None)
            qos = getattr(ext, "offered_qos_profiles", None) if ext is not None else None
            if qos:
                extra["offered_qos_profiles"] = qos
            conn_map[conn.id] = writer.add_connection(conn.topic, conn.msgtype, **extra)
        if not conn_map:
            raise RuntimeError(f"expected camera/cmd_vel topics missing in {src_dir}")
        for conn, timestamp, raw in reader.messages():
            dst = conn_map.get(conn.id)
            if dst is None:
                continue
            if t0 is None:
                t0 = timestamp
            if timestamp - t0 > max_seconds * 1e9:
                break
            seen = counts.get(conn.topic, 0)
            counts[conn.topic] = seen + 1
            if "image" in conn.topic and seen % max(image_stride, 1) != 0:
                continue
            if "lidar" in conn.topic and seen % max(lidar_stride, 1) != 0:
                continue
            writer.write(dst, timestamp, bytes(raw))
            written += 1
    if written < 20:
        raise RuntimeError(f"trimmed bag too small ({written} messages)")
    return {"messages": written, "kept_raw": counts, "seconds": max_seconds}


def prepare_av_rosbag(
    dest: Path | None = None,
    max_seconds: float = 12.0,
    image_stride: int = 2,
    lidar_stride: int = 20,
) -> dict:
    """Download CostNav episode, write a short rosbag2 into inbox/<mission>."""
    dest = Path(dest or (settings.inbox_path / MISSION_ID))
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = download_costnav_episode()
    trimmed = _trim_bag(source, dest, max_seconds, image_stride, lidar_stride)
    manifest = {
        "mission_id": MISSION_ID,
        "vehicle_id": "UGV-01",
        "classification": "UNCLASSIFIED",
        "theater": "public-sample",
        "sensors": ["camera_front", "lidar", "imu", "odom", "cmd_vel"],
        "notes": LICENSE_NOTE,
        "source_url": f"{HF_BASE}/{EPISODE}/{MCAP_NAME}",
        "source_episode": EPISODE,
        "trim": trimmed,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "mission_dir": str(dest),
        "mission_id": MISSION_ID,
        "source": str(source),
        "dataset": "costnav-teleop",
        **trimmed,
    }

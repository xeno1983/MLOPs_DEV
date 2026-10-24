from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


CLASSES = ("clear", "obstacle", "person")
COLORS = {
    "clear": (34, 90, 40),
    "obstacle": (90, 70, 40),
    "person": (180, 70, 50),
}


def generate_mission(dest: Path, n_images: int = 60, seed: int = 7, mission_id: str = "M-DEMO-001") -> Path:
    """Create a synthetic UGV mission (unclassified demo data) for pipeline verification."""
    rng = np.random.default_rng(seed)
    dest = Path(dest)
    cam_dir = dest / "cameras" / "front"
    cam_dir.mkdir(parents=True, exist_ok=True)

    labels = []
    started = "2026-08-31T00:00:00+00:00"
    manifest = {
        "mission_id": mission_id,
        "vehicle_id": "UGV-01",
        "classification": "UNCLASSIFIED",
        "started_ts": started,
        "theater": "demo-range",
        "sensors": ["camera_front"],
        "notes": "synthetic obstacle/person/clear scenes for MLOps smoke test",
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    from ugv_mlops.hashing import sha256_file

    for i in range(n_images):
        label = CLASSES[i % len(CLASSES)]
        img = _render_scene(rng, label)
        path = cam_dir / f"frame_{i:04d}.jpg"
        img.save(path, quality=90)
        digest = sha256_file(path)
        sample_id = f"smp_{digest[:16]}"
        labels.append(
            {
                "sample_id": sample_id,
                "label_type": "class",
                "label_json": {"task": "obstacle_cls", "class": label},
                "annotator": "synthetic",
                "tool": "synthetic",
                "gold": True,
            }
        )

    with (dest / "labels.jsonl").open("w", encoding="utf-8") as fh:
        for rec in labels:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dest


def generate_e2e_mission(dest: Path, n_frames: int = 40, seed: int = 3, mission_id: str = "M-E2E-001") -> Path:
    """Synthetic multi-sensor clip for E2E (camera + imu + cmd_vel)."""
    rng = np.random.default_rng(seed)
    dest = Path(dest)
    cam_dir = dest / "cameras" / "front"
    series = dest / "series"
    cam_dir.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    t0_ns = 1_725_000_000_000_000_000
    dt_ns = 100_000_000
    from datetime import datetime, timezone

    manifest = {
        "mission_id": mission_id,
        "vehicle_id": "UGV-01",
        "classification": "UNCLASSIFIED",
        "started_ts": datetime.fromtimestamp(t0_ns / 1e9, tz=timezone.utc).isoformat(),
        "sensors": ["camera_front", "imu", "cmd_vel"],
        "notes": "synthetic E2E clip (camera + imu + action)",
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    actions, imus, lidars = [], [], []
    for i in range(n_frames):
        ts_ns = t0_ns + i * dt_ns
        ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()
        steer = float(0.18 * np.sin(i / 6.0))
        speed = float(1.1 + 0.25 * np.cos(i / 8.0))
        img = _render_scene(rng, "clear" if abs(steer) < 0.12 else "obstacle")
        img.save(cam_dir / f"front_{ts_ns}.jpg", quality=90)
        actions.append({"ts": ts, "topic": "/cmd_vel", "msgtype": "geometry_msgs/msg/Twist", "speed": speed, "steer": steer, "accel": 0.0})
        imus.append({"ts": ts, "topic": "/imu", "ax": 0.0, "ay": 0.0, "az": 9.81, "gx": 0.0, "gy": 0.0, "gz": steer * 2.0})
        lidars.append({"ts": ts, "topic": "/points_raw", "n_points": int(18000 + 500 * i), "width": 1800, "height": 16})
    (series / "action.jsonl").write_text("\n".join(json.dumps(x) for x in actions) + "\n", encoding="utf-8")
    (series / "imu.jsonl").write_text("\n".join(json.dumps(x) for x in imus) + "\n", encoding="utf-8")
    (series / "lidar.jsonl").write_text("\n".join(json.dumps(x) for x in lidars) + "\n", encoding="utf-8")
    return dest


def _render_scene(rng: np.random.Generator, label: str) -> Image.Image:
    w, h = 320, 240
    sky = (int(rng.integers(70, 120)), int(rng.integers(90, 140)), int(rng.integers(140, 190)))
    ground = COLORS["clear"]
    img = Image.new("RGB", (w, h), sky)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, int(h * 0.55), w, h], fill=ground)
    if label == "obstacle":
        x0 = int(rng.integers(40, 180))
        y0 = int(rng.integers(90, 140))
        draw.rectangle([x0, y0, x0 + 70, y0 + 55], fill=COLORS["obstacle"])
    elif label == "person":
        x0 = int(rng.integers(60, 220))
        y0 = int(rng.integers(80, 130))
        draw.ellipse([x0, y0, x0 + 28, y0 + 28], fill=(220, 190, 160))
        draw.rectangle([x0 + 6, y0 + 26, x0 + 22, y0 + 80], fill=COLORS["person"])
    noise = rng.integers(0, 18, (h, w, 3), dtype=np.uint8)
    arr = np.clip(np.array(img, dtype=np.int16) + noise - 8, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)

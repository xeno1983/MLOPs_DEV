"""Catalog / inbox に置いた帰還データの中身をポータルで見る。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sqlalchemy import text

from ugv_mlops.db import connect
from ugv_mlops.settings import settings
from ugv_mlops.storage import get_bytes, parse_s3_uri

CAMERA_LIMIT = 24
SERIES_POINTS = 240
LIDAR_POINTS = 4000


def list_missions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT m.mission_id, m.vehicle_id, m.classification, m.created_at,
                       count(s.sample_id)::int AS samples,
                       count(s.sample_id) FILTER (WHERE starts_with(s.modality, 'camera'))::int AS cameras,
                       count(s.sample_id) FILTER (WHERE s.modality = 'lidar')::int AS lidar,
                       count(s.sample_id) FILTER (
                         WHERE s.modality IN ('action', 'cmd_vel', 'imu', 'odom', 'gnss')
                       )::int AS series,
                       count(DISTINCT a.sample_id)::int AS labeled
                FROM mission m
                LEFT JOIN learning_sample s ON s.mission_id = m.mission_id
                LEFT JOIN annotation a ON a.sample_id = s.sample_id
                GROUP BY m.mission_id, m.vehicle_id, m.classification, m.created_at
                ORDER BY m.created_at DESC
                LIMIT 80
                """
            )
        ).mappings().all()
    catalog = {str(r["mission_id"]): dict(r) for r in rows}
    inbox = Path(settings.inbox_dir)
    if inbox.exists():
        for path in sorted(inbox.iterdir()):
            if not path.is_dir():
                continue
            catalog.setdefault(
                path.name,
                {
                    "mission_id": path.name,
                    "vehicle_id": None,
                    "classification": None,
                    "created_at": None,
                    "samples": 0,
                    "cameras": 0,
                    "lidar": 0,
                    "series": 0,
                    "labeled": 0,
                    "inbox_only": True,
                },
            )
    out = []
    for mid, rec in catalog.items():
        rec["mission_id"] = mid
        rec["inbox"] = (inbox / mid).is_dir()
        rec["ingested"] = int(rec.get("samples") or 0) > 0
        out.append(rec)
    ingested = [r for r in out if r.get("ingested")]
    pending = [r for r in out if not r.get("ingested")]
    ingested.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    pending.sort(key=lambda r: r["mission_id"])
    return ingested + pending


def mission_preview(mission_id: str) -> dict:
    with connect() as conn:
        mission = conn.execute(
            text("SELECT * FROM mission WHERE mission_id = :m"),
            {"m": mission_id},
        ).mappings().first()
        samples = conn.execute(
            text(
                """
                SELECT sample_id, ts, modality, object_uri, width, height, meta_json
                FROM learning_sample
                WHERE mission_id = :m
                ORDER BY ts NULLS LAST, created_at
                """
            ),
            {"m": mission_id},
        ).mappings().all()
        labeled = conn.execute(
            text(
                """
                SELECT count(DISTINCT a.sample_id)::int
                FROM annotation a
                JOIN learning_sample s ON s.sample_id = a.sample_id
                WHERE s.mission_id = :m
                """
            ),
            {"m": mission_id},
        ).scalar_one()
    modalities: dict[str, int] = {}
    cameras: list[dict] = []
    lidar_rows: list[dict] = []
    series_rows: list[dict] = []
    for row in samples:
        mod = str(row["modality"])
        modalities[mod] = modalities.get(mod, 0) + 1
        rec = {
            "sample_id": row["sample_id"],
            "ts": row["ts"],
            "modality": mod,
            "width": row["width"],
            "height": row["height"],
        }
        if mod.startswith("camera") and len(cameras) < CAMERA_LIMIT:
            cameras.append(rec)
        elif mod == "lidar":
            extra = row["meta_json"] or {}
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except json.JSONDecodeError:
                    extra = {}
            rec["n_points"] = extra.get("n_points")
            lidar_rows.append({**rec, "object_uri": row["object_uri"]})
        elif mod in {"action", "cmd_vel", "imu", "odom", "gnss"}:
            series_rows.append({**rec, "object_uri": row["object_uri"], "kind": mod})

    series = {}
    for rec in series_rows:
        kind = rec["kind"] if rec["kind"] != "cmd_vel" else "action"
        if kind in series:
            continue
        parsed = _load_series(rec["object_uri"])
        parsed["sample_id"] = rec["sample_id"]
        parsed["kind"] = kind
        series[kind] = parsed

    lidar = None
    for rec in lidar_rows:
        uri = rec.get("object_uri") or ""
        if str(uri).endswith(".jsonl"):
            continue
        lidar = _load_lidar(uri)
        lidar["sample_id"] = rec["sample_id"]
        lidar["ts"] = rec.get("ts")
        break

    topics = []
    bags = []
    try:
        from ugv_mlops.bag_pipeline import mission_detail, mission_topics

        detail = mission_detail(mission_id)
        bags = detail.get("bags") or []
        try:
            topics = (mission_topics(mission_id).get("topics") or [])[:40]
        except FileNotFoundError:
            topics = (detail.get("summary") or {}).get("topics") or []
    except Exception:
        detail = {}

    return {
        "mission_id": mission_id,
        "ingested": bool(samples),
        "vehicle_id": None if not mission else mission.get("vehicle_id"),
        "classification": None if not mission else mission.get("classification"),
        "created_at": None if not mission else mission.get("created_at"),
        "samples": len(samples),
        "labeled": int(labeled or 0),
        "modalities": modalities,
        "cameras": cameras,
        "lidar": lidar,
        "lidar_count": len(lidar_rows),
        "series": series,
        "topics": topics,
        "bags": bags,
        "inbox": str(Path(settings.inbox_dir) / mission_id),
        "stage": (detail or {}).get("stage"),
    }


def _load_series(uri: str) -> dict:
    try:
        bucket, key = parse_s3_uri(uri)
        raw = get_bytes(bucket, key)
        text_body = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return {"n": 0, "points": [], "error": str(exc)}
    rows = []
    for line in text_body.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(
            {
                "t": rec.get("ts"),
                "speed": rec.get("speed"),
                "steer": rec.get("steer"),
                "x": rec.get("x"),
                "y": rec.get("y"),
                "ax": rec.get("ax"),
                "ay": rec.get("ay"),
                "az": rec.get("az"),
                "lat": rec.get("lat"),
                "lon": rec.get("lon"),
            }
        )
    total = len(rows)
    if total > SERIES_POINTS:
        step = total / SERIES_POINTS
        rows = [rows[int(i * step)] for i in range(SERIES_POINTS)]
    return {"n": total, "points": rows}


def _load_lidar(uri: str) -> dict:
    try:
        bucket, key = parse_s3_uri(uri)
        raw = get_bytes(bucket, key)
        xyz = np.frombuffer(raw, dtype="<f4")
        if xyz.size < 3:
            return {"n": 0, "x": [], "y": [], "z": []}
        xyz = xyz[: xyz.size - (xyz.size % 3)].reshape(-1, 3)
        n = int(xyz.shape[0])
        if n > LIDAR_POINTS:
            idx = np.linspace(0, n - 1, LIDAR_POINTS).astype(np.int64)
            xyz = xyz[idx]
        return {
            "n": n,
            "shown": int(xyz.shape[0]),
            "x": xyz[:, 0].astype(float).tolist(),
            "y": xyz[:, 1].astype(float).tolist(),
            "z": xyz[:, 2].astype(float).tolist(),
        }
    except Exception as exc:
        return {"n": 0, "x": [], "y": [], "z": [], "error": str(exc)}

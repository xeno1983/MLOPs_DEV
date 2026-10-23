from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings


SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def ingest_mission_dir(mission_dir: Path, source_type: str = "dropzone") -> dict:
    return ingest_path(mission_dir, source_type)


def ingest_path(path: Path, source_type: str = "dropzone") -> dict:
    """Ingest a mission folder, a rosbag2 directory, or a .mcap/.db3/.bag file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    from ugv_mlops.bag_pipeline import import_mission, parse_mission, store_mission
    from ugv_mlops.rosbag import is_rosbag

    if path.is_file():
        mission_dir = path.parent
    elif is_rosbag(path) and (path / "metadata.yaml").exists() and not (path / "manifest.json").exists() and not any(path.glob("cameras/**/*")):
        mission_dir = path
    else:
        mission_dir = path

    manifest = _load_manifest(mission_dir, path)
    mission_id = manifest["mission_id"]
    vehicle_id = manifest.get("vehicle_id", "UGV-01")
    classification = manifest.get("classification", "UNCLASSIFIED")

    storage.ensure_buckets()
    imported = import_mission(path, mission_id)
    mission_id = imported["mission_id"]
    parsed = parse_mission(mission_id)
    parsed_dir = Path(parsed["parsed_dir"])
    raw_dir = Path(imported["raw_dir"])
    extracted_frames: list[dict] = list(parsed.get("frames") or [])
    lidar_files: list[dict] = list(parsed.get("lidar_files") or [])
    topics: list[dict] = list((parsed.get("summary") or {}).get("topics") or [])
    bag_files = [Path(p) for p in (parsed.get("bag_files") or [])]
    if not bag_files:
        bag_files = [p for p in raw_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mcap", ".bag", ".db3"}]
        meta = raw_dir / "metadata.yaml"
        if meta.exists():
            bag_files.append(meta)

    files_count = 0
    bytes_total = 0
    sample_ids: list[str] = []
    camera_ids: list[str] = []
    modalities: dict[str, int] = {}

    with connect() as conn:
        _ensure_mission(conn, mission_id, vehicle_id, classification, manifest)
        session_id = conn.execute(
            text(
                """
                INSERT INTO ingest_session (mission_id, source_type, status)
                VALUES (:mid, :src, 'running')
                RETURNING session_id
                """
            ),
            {"mid": mission_id, "src": source_type},
        ).scalar_one()
        try:
            for bag_file in bag_files:
                rel = f"rosbag/{bag_file.name}"
                uri = storage.put_file("raw", f"{mission_id}/{rel}", bag_file)
                sid, nbytes = _register_sample(
                    conn,
                    mission_id=mission_id,
                    vehicle_id=vehicle_id,
                    classification=classification,
                    ts=manifest.get("started_ts"),
                    modality="rosbag",
                    path=bag_file,
                    uri=uri,
                    extra={"relpath": rel, "session_id": str(session_id)},
                )
                sample_ids.append(sid)
                files_count += 1
                bytes_total += nbytes
                modalities["rosbag"] = modalities.get("rosbag", 0) + 1

            extracted_names = {Path(f["path"]).resolve() for f in extracted_frames}
            for frame in extracted_frames:
                fpath = Path(frame["path"])
                w, h = frame.get("width"), frame.get("height")
                if not w or not h:
                    w, h = _image_size(fpath)
                uri = storage.put_file(
                    "raw",
                    f"{mission_id}/{frame['relpath']}",
                    fpath,
                    content_type="image/jpeg",
                )
                sid, nbytes = _register_sample(
                    conn,
                    mission_id=mission_id,
                    vehicle_id=vehicle_id,
                    classification=classification,
                    ts=frame.get("ts") or manifest.get("started_ts"),
                    modality=frame["modality"],
                    path=fpath,
                    uri=uri,
                    width=w,
                    height=h,
                    extra={
                        "relpath": frame["relpath"],
                        "topic": frame["topic"],
                        "msgtype": frame["msgtype"],
                        "session_id": str(session_id),
                    },
                )
                sample_ids.append(sid)
                camera_ids.append(sid)
                files_count += 1
                bytes_total += nbytes
                modalities[frame["modality"]] = modalities.get(frame["modality"], 0) + 1

            for item in lidar_files:
                lid_path = Path(item["path"])
                if not lid_path.exists():
                    continue
                uri = storage.put_file(
                    "raw",
                    f"{mission_id}/{item['relpath']}",
                    lid_path,
                    content_type="application/octet-stream",
                )
                sid, nbytes = _register_sample(
                    conn,
                    mission_id=mission_id,
                    vehicle_id=vehicle_id,
                    classification=classification,
                    ts=item.get("ts") or manifest.get("started_ts"),
                    modality="lidar",
                    path=lid_path,
                    uri=uri,
                    extra={
                        "relpath": item["relpath"],
                        "topic": item.get("topic"),
                        "n_points": item.get("n_points"),
                        "session_id": str(session_id),
                    },
                )
                sample_ids.append(sid)
                files_count += 1
                bytes_total += nbytes
                modalities["lidar"] = modalities.get("lidar", 0) + 1

            for rec in parsed.get("series_files") or []:
                series_path = Path(rec["path"])
                if not series_path.exists() or rec.get("n", 0) == 0:
                    continue
                kind = rec["kind"]
                if kind == "keyframes":
                    kind = "lidar"
                rel = rec.get("relpath") or f"series/{series_path.name}"
                uri = storage.put_file(
                    "raw",
                    f"{mission_id}/{rel}",
                    series_path,
                    content_type="application/jsonl",
                )
                sid, nbytes = _register_sample(
                    conn,
                    mission_id=mission_id,
                    vehicle_id=vehicle_id,
                    classification=classification,
                    ts=manifest.get("started_ts"),
                    modality=kind if kind != "telemetry" else "odom",
                    path=series_path,
                    uri=uri,
                    extra={"n": rec.get("n"), "session_id": str(session_id), "relpath": rel},
                )
                sample_ids.append(sid)
                files_count += 1
                bytes_total += nbytes
                modalities[kind] = modalities.get(kind, 0) + int(rec.get("n") or 0)

            scan_roots: list[Path] = []
            for root in (mission_dir, raw_dir):
                if root.is_dir() and root.resolve() not in {r.resolve() for r in scan_roots}:
                    scan_roots.append(root)
            for root in scan_roots:
                for image in _iter_images(root):
                    if image.resolve() in extracted_names:
                        continue
                    if "extract" in image.parts:
                        continue
                    try:
                        rel = image.relative_to(root).as_posix()
                    except ValueError:
                        rel = image.name
                    uri = storage.put_file("raw", f"{mission_id}/{rel}", image, content_type=_image_content_type(image))
                    w, h = _image_size(image)
                    sid, nbytes = _register_sample(
                        conn,
                        mission_id=mission_id,
                        vehicle_id=vehicle_id,
                        classification=classification,
                        ts=_ts_from_image(image, manifest),
                        modality=_modality_from_path(rel),
                        path=image,
                        uri=uri,
                        width=w,
                        height=h,
                        extra={"relpath": rel, "session_id": str(session_id)},
                    )
                    sample_ids.append(sid)
                    camera_ids.append(sid)
                    files_count += 1
                    bytes_total += nbytes
                    mod = _modality_from_path(rel)
                    modalities[mod] = modalities.get(mod, 0) + 1

            for jsonl in list(mission_dir.glob("series/*.jsonl")) + list(mission_dir.glob("*.jsonl")):
                if jsonl.name == "labels.jsonl":
                    continue
                kind = jsonl.stem
                if kind not in {"imu", "gnss", "odom", "action", "lidar", "telemetry"}:
                    continue
                uri = storage.put_file("raw", f"{mission_id}/series/{jsonl.name}", jsonl, content_type="application/jsonl")
                sid, nbytes = _register_sample(
                    conn,
                    mission_id=mission_id,
                    vehicle_id=vehicle_id,
                    classification=classification,
                    ts=manifest.get("started_ts"),
                    modality=kind if kind != "telemetry" else "odom",
                    path=jsonl,
                    uri=uri,
                    extra={"relpath": jsonl.name, "session_id": str(session_id), "source": "inbox"},
                )
                sample_ids.append(sid)
                files_count += 1
                bytes_total += nbytes
                n_rows = sum(1 for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip())
                modalities[kind] = modalities.get(kind, 0) + n_rows

            labels_path = mission_dir / "labels.jsonl"
            if labels_path.exists():
                _ingest_labels(conn, labels_path)

            conn.execute(
                text(
                    """
                    UPDATE ingest_session
                    SET status = 'completed', files_count = :n, bytes_total = :b, ended_ts = now()
                    WHERE session_id = :sid
                    """
                ),
                {"n": files_count, "b": bytes_total, "sid": session_id},
            )
            audit(
                conn,
                "ingest.completed",
                "mission",
                mission_id,
                {"files": files_count, "bytes": bytes_total, "samples": len(sample_ids), "modalities": modalities, "topics": topics[:40]},
            )
        except Exception as exc:
            conn.execute(
                text(
                    """
                    UPDATE ingest_session
                    SET status = 'failed', error = :err, ended_ts = now()
                    WHERE session_id = :sid
                    """
                ),
                {"err": str(exc), "sid": session_id},
            )
            raise

    result = {
        "mission_id": mission_id,
        "session_id": str(session_id),
        "files_count": files_count,
        "bytes_total": bytes_total,
        "sample_ids": sample_ids,
        "modalities": modalities,
        "bags": [str(b) for b in bag_files],
        "topics": topics,
    }
    try:
        from ugv_mlops.integrations import push_samples_to_labelstudio

        result["labelstudio"] = push_samples_to_labelstudio(camera_ids[:400])
    except Exception as exc:
        result["labelstudio"] = {"ok": False, "error": str(exc)}
    try:
        result["dvc"] = store_mission(mission_id, push=True)
    except Exception as exc:
        try:
            from ugv_mlops.dvc_sync import publish_mission

            result["dvc"] = publish_mission(mission_id)
            result["dvc"]["fallback"] = str(exc)
        except Exception as exc2:
            result["dvc"] = {"ok": False, "error": str(exc2), "parse_store": str(exc)}
    result["bag"] = {
        "raw_dir": str(raw_dir),
        "parsed_dir": str(parsed_dir),
        "stage": parsed.get("stage"),
        "summary": parsed.get("summary"),
    }
    return result


def _load_manifest(mission_dir: Path, path: Path) -> dict:
    manifest_path = mission_dir / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    name = path.stem if path.is_file() else mission_dir.name
    return {
        "mission_id": name,
        "vehicle_id": "UGV-01",
        "classification": "UNCLASSIFIED",
        "started_ts": datetime.now(timezone.utc).isoformat(),
        "source": "rosbag2" if path.suffix.lower() in {".mcap", ".bag", ".db3"} or (mission_dir / "metadata.yaml").exists() else "folder",
        "notes": "manifest.json が無かったため自動生成",
    }


def _ensure_mission(conn, mission_id: str, vehicle_id: str, classification: str, manifest: dict) -> None:
    conn.execute(
        text(
            """
            INSERT INTO vehicle (vehicle_id, platform_type)
            VALUES (:vid, 'ugv')
            ON CONFLICT (vehicle_id) DO NOTHING
            """
        ),
        {"vid": vehicle_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO mission (mission_id, vehicle_id, classification, started_ts, theater, notes)
            VALUES (:mid, :vid, :cls, :ts, :theater, :notes)
            ON CONFLICT (mission_id) DO UPDATE
              SET vehicle_id = EXCLUDED.vehicle_id
            """
        ),
        {
            "mid": mission_id,
            "vid": vehicle_id,
            "cls": classification,
            "ts": manifest.get("started_ts") or datetime.now(timezone.utc).isoformat(),
            "theater": manifest.get("theater"),
            "notes": manifest.get("notes"),
        },
    )


def _register_sample(
    conn,
    *,
    mission_id: str,
    vehicle_id: str,
    classification: str,
    ts: str | None,
    modality: str,
    path: Path,
    uri: str,
    extra: dict,
    width: int | None = None,
    height: int | None = None,
) -> tuple[str, int]:
    digest = sha256_file(path)
    sample_id = f"smp_{digest[:16]}"
    conn.execute(
        text(
            """
            INSERT INTO learning_sample (
                sample_id, mission_id, vehicle_id, ts, modality, object_uri,
                sha256, width, height, classification, declassified, pii_scrubbed, meta_json
            ) VALUES (
                :sid, :mid, :vid, :ts, :mod, :uri,
                :sha, :w, :h, :cls, :declass, TRUE, CAST(:meta AS jsonb)
            )
            ON CONFLICT (sample_id) DO NOTHING
            """
        ),
        {
            "sid": sample_id,
            "mid": mission_id,
            "vid": vehicle_id,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "mod": modality,
            "uri": uri,
            "sha": digest,
            "w": width,
            "h": height,
            "cls": classification,
            "declass": classification.upper() in {"UNCLASSIFIED", "UNCLAS"},
            "meta": json.dumps(extra, ensure_ascii=False, default=str),
        },
    )
    return sample_id, path.stat().st_size


def _iter_images(root: Path):
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGES:
            yield path


def _modality_from_path(rel: str) -> str:
    lower = rel.lower()
    if "lidar" in lower:
        return "lidar"
    if "rear" in lower:
        return "camera_rear"
    if "left" in lower:
        return "camera_left"
    if "right" in lower:
        return "camera_right"
    return "camera_front"


def _image_content_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as img:
        return img.size


def _ts_from_image(path: Path, manifest: dict) -> str:
    stem = path.stem.split("_")[-1]
    if stem.isdigit():
        value = int(stem)
        if value > 10**15:
            return datetime.fromtimestamp(value / 1e9, tz=timezone.utc).isoformat()
        if value > 10**9:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return manifest.get("started_ts") or datetime.now(timezone.utc).isoformat()


def _ingest_labels(conn, labels_path: Path) -> None:
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        sample_id = rec["sample_id"]
        conn.execute(
            text(
                """
                INSERT INTO annotation (sample_id, label_type, label_json, annotator, tool, gold)
                VALUES (:sid, :lt, CAST(:lj AS jsonb), :ann, :tool, :gold)
                """
            ),
            {
                "sid": sample_id,
                "lt": rec.get("label_type", "class"),
                "lj": json.dumps(rec.get("label_json", rec)),
                "ann": rec.get("annotator", "seed"),
                "tool": rec.get("tool", "synthetic"),
                "gold": bool(rec.get("gold", True)),
            },
        )


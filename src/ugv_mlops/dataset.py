from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings
from ugv_mlops.storage import parse_s3_uri


def next_version(dataset_id: str) -> int:
    with connect() as conn:
        current = conn.execute(
            text("SELECT COALESCE(MAX(version), 0) FROM dataset_version WHERE dataset_id = :d"),
            {"d": dataset_id},
        ).scalar_one()
    return int(current) + 1


def build_dataset(
    dataset_id: str,
    name: str,
    task: str = "obstacle_cls",
    format_name: str = "imagefolder",
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
) -> dict:
    """Build a versioned dataset. task=obstacle_cls uses labels; task=e2e aligns bag sensors."""
    if task in {"e2e", "e2e_sequence", "end_to_end"}:
        from ugv_mlops.e2e import build_e2e_dataset

        return build_e2e_dataset(dataset_id, name, train_ratio, val_ratio)
    if task in {"detection", "yolox", "yolox_nano"}:
        from ugv_mlops.detect_dataset import build_detection_dataset

        return build_detection_dataset(dataset_id, name, train_ratio, val_ratio)

    try:
        from ugv_mlops.integrations import pull_labelstudio_annotations

        pull_labelstudio_annotations()
    except Exception:
        pass

    work = settings.work_path / "datasets" / dataset_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.sample_id, s.object_uri, s.modality, a.label_json
                FROM learning_sample s
                JOIN annotation a ON a.sample_id = s.sample_id
                WHERE a.label_type = 'class'
                  AND (s.declassified = TRUE OR s.classification = 'UNCLASSIFIED')
                ORDER BY s.sample_id
                """
            )
        ).mappings().all()
        if not rows:
            raise RuntimeError("no labeled unclassified samples available")

        conn.execute(
            text(
                """
                INSERT INTO dataset (dataset_id, name, task, description)
                VALUES (:id, :name, :task, :desc)
                ON CONFLICT (dataset_id) DO UPDATE
                  SET name = EXCLUDED.name, task = EXCLUDED.task
                """
            ),
            {"id": dataset_id, "name": name, "task": task, "desc": "auto-built from catalog annotations"},
        )

    version = next_version(dataset_id)
    version_id = f"{dataset_id}-v{version}"
    items = []
    for idx, row in enumerate(rows):
        split = _split(idx, len(rows), train_ratio, val_ratio)
        label = row["label_json"].get("class") if isinstance(row["label_json"], dict) else None
        if label is None:
            continue
        split_dir = work / split / str(label)
        split_dir.mkdir(parents=True, exist_ok=True)
        bucket, key = parse_s3_uri(row["object_uri"])
        dest = split_dir / f"{row['sample_id']}.jpg"
        storage.get_file(bucket, key, dest)
        items.append((row["sample_id"], split, label))

    stats = {
        "n": len(items),
        "splits": dict(Counter(s for _, s, _ in items)),
        "classes": dict(Counter(c for _, _, c in items)),
        "task": task,
        "format": format_name,
    }
    meta_path = work / "dataset.json"
    meta_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    archive = shutil.make_archive(str(work), "zip", root_dir=work)
    archive_path = Path(archive)
    digest = sha256_file(archive_path)
    uri = storage.put_file("datasets", f"{dataset_id}/{version_id}.zip", archive_path, "application/zip")

    with connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dataset_version (
                    dataset_version_id, dataset_id, version, format, object_uri, sha256, stats_json
                ) VALUES (
                    :id, :ds, :ver, :fmt, :uri, :sha, CAST(:stats AS jsonb)
                )
                """
            ),
            {
                "id": version_id,
                "ds": dataset_id,
                "ver": version,
                "fmt": format_name,
                "uri": uri,
                "sha": digest,
                "stats": json.dumps(stats),
            },
        )
        for sample_id, split, _label in items:
            conn.execute(
                text(
                    """
                    INSERT INTO dataset_item (dataset_version_id, sample_id, split)
                    VALUES (:dv, :sid, :split)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"dv": version_id, "sid": sample_id, "split": split},
            )
        audit(conn, "dataset.built", "dataset_version", version_id, stats)

    result = {
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "version": version,
        "object_uri": uri,
        "sha256": digest,
        "stats": stats,
    }
    from ugv_mlops.integrations import attach_dataset_remotes

    result.update(attach_dataset_remotes(version_id, uri))
    return result


def _split(idx: int, n: int, train_ratio: float, val_ratio: float) -> str:
    cut_train = int(n * train_ratio)
    cut_val = int(n * (train_ratio + val_ratio))
    if idx < cut_train:
        return "train"
    if idx < cut_val:
        return "val"
    return "test"

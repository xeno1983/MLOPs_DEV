from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image
from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.coco_constants import COCO_CLASSES
from ugv_mlops.dataset import next_version
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings
from ugv_mlops.storage import parse_s3_uri


def build_detection_dataset(
    dataset_id: str,
    name: str,
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
) -> dict:
    """Assemble bbox-labeled camera samples into a COCO-format dataset zip."""
    work = settings.work_path / "datasets" / dataset_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.sample_id, s.object_uri, s.width, s.height, a.label_json
                FROM learning_sample s
                JOIN annotation a ON a.sample_id = s.sample_id
                WHERE a.label_type = 'bbox'
                  AND s.modality LIKE 'camera%'
                  AND (s.declassified = TRUE OR s.classification = 'UNCLASSIFIED')
                ORDER BY s.sample_id
                """
            )
        ).mappings().all()
    grouped: dict[str, dict] = {}
    for row in rows:
        rec = grouped.setdefault(
            row["sample_id"],
            {"object_uri": row["object_uri"], "width": row["width"], "height": row["height"], "boxes": []},
        )
        rec["boxes"].extend(_boxes_from_label(row["label_json"]))
    items = [(sid, rec) for sid, rec in grouped.items() if rec["boxes"]]
    if len(items) < 8:
        raise RuntimeError(
            f"検出用の bbox 付き画像が足りません（{len(items)}）。COCO128 を prepare して ingest してください。"
        )

    n = len(items)
    images_meta = []
    annotations = []
    used = []
    ann_id = 1
    class_counter: Counter[str] = Counter()
    split_counter: Counter[str] = Counter()
    for idx, (sample_id, rec) in enumerate(items):
        split = _split(idx, n, train_ratio, val_ratio)
        split_dir = work / "images" / split
        split_dir.mkdir(parents=True, exist_ok=True)
        dest = split_dir / f"{sample_id}.jpg"
        bucket, key = parse_s3_uri(rec["object_uri"])
        storage.get_file(bucket, key, dest)
        with Image.open(dest) as img:
            width, height = img.size
        image_id = idx + 1
        images_meta.append(
            {
                "id": image_id,
                "file_name": f"images/{split}/{sample_id}.jpg",
                "width": width,
                "height": height,
                "split": split,
                "sample_id": sample_id,
            }
        )
        for box in rec["boxes"]:
            cls_id = int(box.get("class_id", 0))
            name = box.get("class") or (COCO_CLASSES[cls_id] if 0 <= cls_id < len(COCO_CLASSES) else "unknown")
            x, y, w, h = float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])
            annotations.append(
                {
                    "id": ann_id,
                    "image_id": image_id,
                    "category_id": cls_id,
                    "bbox": [x, y, w, h],
                    "area": max(w * h, 1.0),
                    "iscrowd": 0,
                    "class_name": name,
                }
            )
            ann_id += 1
            class_counter[name] += 1
        split_counter[split] += 1
        used.append((sample_id, split))

    categories = [{"id": i, "name": name} for i, name in enumerate(COCO_CLASSES)]
    coco = {"images": images_meta, "annotations": annotations, "categories": categories}
    (work / "annotations").mkdir(exist_ok=True)
    (work / "annotations" / "instances.json").write_text(json.dumps(coco), encoding="utf-8")
    stats = {
        "n": n,
        "task": "detection",
        "format": "coco-detection-v1",
        "splits": dict(split_counter),
        "classes": dict(class_counter.most_common(20)),
        "n_boxes": len(annotations),
        "model": "yolox-nano",
    }
    (work / "dataset.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    version = next_version(dataset_id)
    version_id = f"{dataset_id}-v{version}"
    archive = shutil.make_archive(str(work), "zip", root_dir=work)
    archive_path = Path(archive)
    digest = sha256_file(archive_path)
    uri = storage.put_file("datasets", f"{dataset_id}/{version_id}.zip", archive_path, "application/zip")
    with connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dataset (dataset_id, name, task, description)
                VALUES (:id, :name, :task, :desc)
                ON CONFLICT (dataset_id) DO UPDATE
                  SET name = EXCLUDED.name, task = EXCLUDED.task
                """
            ),
            {
                "id": dataset_id,
                "name": name,
                "task": "detection",
                "desc": "COCO-format detection set for YOLOX-Nano",
            },
        )
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
                "fmt": "coco-detection-v1",
                "uri": uri,
                "sha": digest,
                "stats": json.dumps(stats),
            },
        )
        for sample_id, split in used:
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


def _boxes_from_label(label_json) -> list[dict]:
    if not isinstance(label_json, dict):
        return []
    if isinstance(label_json.get("boxes"), list):
        return [b for b in label_json["boxes"] if isinstance(b, dict) and "w" in b]
    if all(k in label_json for k in ("x", "y", "w", "h")):
        return [label_json]
    return []


def _split(idx: int, n: int, train_ratio: float, val_ratio: float) -> str:
    cut_train = int(n * train_ratio)
    cut_val = int(n * (train_ratio + val_ratio))
    if idx < cut_train:
        return "train"
    if idx < cut_val:
        return "val"
    return "test"

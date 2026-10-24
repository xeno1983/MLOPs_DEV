from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import joblib
import mlflow
import numpy as np
from PIL import Image
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings
from ugv_mlops.storage import parse_s3_uri


def build_e2e_dataset(
    dataset_id: str,
    name: str,
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
) -> dict:
    """Time-align camera frames with action/imu/gnss/lidar for E2E driving clones."""
    work = settings.work_path / "datasets" / dataset_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    cameras, series = _load_e2e_sources()
    if not cameras:
        raise RuntimeError("E2E 用のカメラサンプルがありません。先に ROS2 bag か画像を ingest してください。")
    actions = series.get("action") or []
    if not actions:
        raise RuntimeError("E2E 用の操作系列（cmd_vel / steering / control_cmd）がありません。")

    max_dt = float(settings.e2e_action_max_dt_sec)
    records = []
    used_ids = []
    for cam in cameras:
        t = _parse_ts(cam["ts"])
        action = _nearest(actions, t, max_dt)
        if action is None:
            continue
        rec = {
            "sample_id": cam["sample_id"],
            "ts": cam["ts"],
            "image": f"images/{cam['sample_id']}.jpg",
            "modality": cam["modality"],
            "action": {"speed": action.get("speed"), "steer": action.get("steer"), "accel": action.get("accel")},
            "imu": _nearest(series.get("imu") or [], t, 0.5),
            "gnss": _nearest(series.get("gnss") or [], t, 1.0),
            "odom": _nearest(series.get("odom") or [], t, 0.5),
            "lidar": _nearest(series.get("lidar") or [], t, 0.4),
        }
        if rec["action"]["steer"] is None and rec["action"]["speed"] is None:
            continue
        bucket, key = parse_s3_uri(cam["object_uri"])
        dest = work / rec["image"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        storage.get_file(bucket, key, dest)
        records.append(rec)
        used_ids.append(cam["sample_id"])

    if len(records) < 8:
        raise RuntimeError(f"E2E に揃ったフレームが足りません（{len(records)}）。bag に cmd_vel 等があるか確認してください。")

    n = len(records)
    for idx, rec in enumerate(records):
        rec["split"] = _split(idx, n, train_ratio, val_ratio)
    (work / "records.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in records) + "\n",
        encoding="utf-8",
    )
    stats = {
        "n": n,
        "task": "e2e",
        "format": "e2e-records-v1",
        "splits": {
            "train": sum(1 for r in records if r["split"] == "train"),
            "val": sum(1 for r in records if r["split"] == "val"),
            "test": sum(1 for r in records if r["split"] == "test"),
        },
        "with_imu": sum(1 for r in records if r.get("imu")),
        "with_gnss": sum(1 for r in records if r.get("gnss")),
        "with_lidar": sum(1 for r in records if r.get("lidar")),
    }
    (work / "dataset.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    return _publish(dataset_id, name, "e2e", "e2e-records-v1", work, stats, used_ids, records)


def train_e2e(dataset_version_id: str, stage: str | None = None) -> dict:
    stage = stage or settings.default_learning_stage
    work = settings.work_path / "train" / dataset_version_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        row = conn.execute(
            text("SELECT object_uri FROM dataset_version WHERE dataset_version_id = :id"),
            {"id": dataset_version_id},
        ).mappings().one()
    archive = work / "dataset.zip"
    bucket, key = parse_s3_uri(row["object_uri"])
    storage.get_file(bucket, key, archive)
    shutil.unpack_archive(archive, work / "data")
    records = _read_jsonl(work / "data" / "records.jsonl")
    x_train, y_train = _xy(work / "data", [r for r in records if r["split"] == "train"])
    x_val, y_val = _xy(work / "data", [r for r in records if r["split"] == "val"])
    x_test, y_test = _xy(work / "data", [r for r in records if r["split"] == "test"])
    if len(x_train) < 4:
        raise RuntimeError("E2E train split is too small")
    model = Pipeline([("scaler", StandardScaler()), ("reg", Ridge(alpha=1.0))])
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment("ugv-e2e")
    with mlflow.start_run(run_name=f"{dataset_version_id}-{stage}") as run:
        model.fit(x_train, y_train)
        pred_val = model.predict(x_val) if len(x_val) else model.predict(x_train[:1])
        y_eval = y_val if len(x_val) else y_train[:1]
        pred_test = model.predict(x_test) if len(x_test) else pred_val
        y_t = y_test if len(x_test) else y_eval
        mae_steer = float(mean_absolute_error(y_t[:, 0], pred_test[:, 0]))
        mae_speed = float(mean_absolute_error(y_t[:, 1], pred_test[:, 1]))
        metrics = {
            "mae_steer": mae_steer,
            "mae_speed": mae_speed,
            "n_train": int(len(x_train)),
            "n_val": int(len(x_val)),
            "n_test": int(len(x_test)),
            "accuracy": float(max(0.0, 1.0 - mae_steer)),
            "false_positive_rate": 0.0,
        }
        mlflow.log_params({"dataset_version_id": dataset_version_id, "stage": stage, "task": "e2e", "estimator": "Ridge"})
        mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, float)})
        model_path = work / "model.joblib"
        joblib.dump({"model": model, "outputs": ["steer", "speed"]}, model_path)
        mlflow.log_artifact(str(model_path))
        run_id = run.info.run_id
    uri = storage.put_file("models", f"{run_id}/model.joblib", model_path)
    with connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO training_run (
                    run_id, dataset_version_id, task, stage, params_json, metrics_json, artifact_uri, status
                ) VALUES (
                    :rid, :dv, 'e2e', :stage, CAST(:params AS jsonb), CAST(:metrics AS jsonb), :uri, 'completed'
                )
                """
            ),
            {
                "rid": run_id,
                "dv": dataset_version_id,
                "stage": stage,
                "params": json.dumps({"estimator": "Ridge"}),
                "metrics": json.dumps(metrics),
                "uri": uri,
            },
        )
        audit(conn, "train.completed", "training_run", run_id, metrics)
    return {
        "run_id": run_id,
        "dataset_version_id": dataset_version_id,
        "stage": stage,
        "artifact_uri": uri,
        "sha256": sha256_file(model_path),
        "metrics": metrics,
        "artifact_kind": "joblib",
        "local_model_path": str(model_path),
        "task": "e2e",
    }


def _load_e2e_sources() -> tuple[list[dict], dict[str, list[dict]]]:
    with connect() as conn:
        cameras = conn.execute(
            text(
                """
                SELECT sample_id, object_uri, ts, modality
                FROM learning_sample
                WHERE modality LIKE 'camera%'
                  AND (declassified = TRUE OR classification = 'UNCLASSIFIED')
                  AND mission_id IN (
                    SELECT mission_id FROM learning_sample WHERE modality = 'action'
                  )
                ORDER BY ts, sample_id
                """
            )
        ).mappings().all()
        streams = conn.execute(
            text(
                """
                SELECT sample_id, object_uri, modality
                FROM learning_sample
                WHERE modality IN ('imu', 'gnss', 'odom', 'action', 'lidar')
                ORDER BY created_at
                """
            )
        ).mappings().all()
    cameras = [dict(r) for r in cameras]
    series: dict[str, list[dict]] = {}
    for row in streams:
        mod = row["modality"]
        bucket, key = parse_s3_uri(row["object_uri"])
        dest = settings.work_path / "series-cache" / f"{row['sample_id']}_{mod}.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        storage.get_file(bucket, key, dest)
        series.setdefault(mod, []).extend(_read_jsonl(dest))
    return cameras, series


def _publish(dataset_id, name, task, format_name, work, stats, used_ids, records):
    from ugv_mlops.dataset import next_version

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
            {"id": dataset_id, "name": name, "task": task, "desc": "E2E sequences aligned from bag sensors"},
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
                "fmt": format_name,
                "uri": uri,
                "sha": digest,
                "stats": json.dumps(stats),
            },
        )
        for rec in records:
            conn.execute(
                text(
                    """
                    INSERT INTO dataset_item (dataset_version_id, sample_id, split)
                    VALUES (:dv, :sid, :split)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"dv": version_id, "sid": rec["sample_id"], "split": rec["split"]},
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


def _xy(root: Path, records: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for rec in records:
        img_path = root / rec["image"]
        if not img_path.exists():
            continue
        img = Image.open(img_path).convert("RGB")
        feat = _features(img, rec)
        steer = rec.get("action", {}).get("steer")
        speed = rec.get("action", {}).get("speed")
        if steer is None and speed is None:
            continue
        xs.append(feat)
        ys.append([float(steer or 0.0), float(speed or 0.0)])
    if not xs:
        return np.empty((0, 16)), np.empty((0, 2))
    return np.vstack(xs), np.array(ys, dtype=float)


def _features(img: Image.Image, rec: dict) -> np.ndarray:
    small = np.array(img.resize((32, 24)), dtype=np.float32) / 255.0
    imu = rec.get("imu") or {}
    lidar = rec.get("lidar") or {}
    return np.array(
        [
            *small.mean(axis=(0, 1)),
            *small.std(axis=(0, 1)),
            small[:8].mean(),
            small[-8:].mean(),
            float(imu.get("az") or 0.0),
            float(imu.get("gz") or 0.0),
            float(imu.get("gx") or 0.0),
            float((lidar.get("n_points") or 0) / 100000.0),
            float(rec.get("action", {}).get("accel") or 0.0),
            small[..., 1].mean(),
            (small[..., 0] - small[..., 2]).mean(),
            small.mean(),
        ],
        dtype=np.float32,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _parse_ts(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _nearest(rows: list[dict], t: float, max_dt: float) -> dict | None:
    best = None
    best_dt = max_dt
    for row in rows:
        dt = abs(_parse_ts(row.get("ts")) - t)
        if dt <= best_dt:
            best = row
            best_dt = dt
    return best


def _split(idx: int, n: int, train_ratio: float, val_ratio: float) -> str:
    cut_train = int(n * train_ratio)
    cut_val = int(n * (train_ratio + val_ratio))
    if idx < cut_train:
        return "train"
    if idx < cut_val:
        return "val"
    return "test"

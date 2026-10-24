from __future__ import annotations

import json
import shutil
from pathlib import Path

import joblib
import mlflow
import numpy as np
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings
from ugv_mlops.storage import parse_s3_uri


CLASSES = ("clear", "obstacle", "person")


def train_from_dataset(dataset_version_id: str, stage: str | None = None) -> dict:
    with connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT d.task
                FROM dataset_version v
                JOIN dataset d ON d.dataset_id = v.dataset_id
                WHERE v.dataset_version_id = :id
                """
            ),
            {"id": dataset_version_id},
        ).mappings().one()
    if row["task"] in {"e2e", "e2e_sequence", "end_to_end"}:
        from ugv_mlops.e2e import train_e2e

        return train_e2e(dataset_version_id, stage)
    if row["task"] in {"detection", "yolox", "yolox_nano"}:
        from ugv_mlops.yolox_train import train_yolox

        return train_yolox(dataset_version_id, stage)
    return train_obstacle_classifier(dataset_version_id, stage)


def train_obstacle_classifier(dataset_version_id: str, stage: str | None = None) -> dict:
    stage = stage or settings.default_learning_stage
    work = settings.work_path / "train" / dataset_version_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        row = conn.execute(
            text("SELECT object_uri, dataset_id FROM dataset_version WHERE dataset_version_id = :id"),
            {"id": dataset_version_id},
        ).mappings().one()
    bucket, key = parse_s3_uri(row["object_uri"])
    archive = work / "dataset.zip"
    storage.get_file(bucket, key, archive)
    shutil.unpack_archive(archive, work / "data")

    x_train, y_train = _load_split(work / "data" / "train")
    x_val, y_val = _load_split(work / "data" / "val")
    x_test, y_test = _load_split(work / "data" / "test")

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=400, class_weight="balanced")),
        ]
    )
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment("ugv-obstacle-cls")
    with mlflow.start_run(run_name=f"{dataset_version_id}-{stage}") as run:
        model.fit(x_train, y_train)
        val_pred = model.predict(x_val)
        test_pred = model.predict(x_test) if len(x_test) else val_pred
        val_acc = float(accuracy_score(y_val, val_pred))
        test_acc = float(accuracy_score(y_test, test_pred)) if len(x_test) else val_acc
        fpr = _false_positive_rate(y_test if len(x_test) else y_val, test_pred)
        metrics = {
            "val_accuracy": val_acc,
            "accuracy": test_acc,
            "false_positive_rate": fpr,
            "n_train": int(len(y_train)),
            "n_val": int(len(y_val)),
            "n_test": int(len(y_test)),
        }
        mlflow.log_params(
            {
                "dataset_version_id": dataset_version_id,
                "stage": stage,
                "estimator": "LogisticRegression",
                "task": "obstacle_cls",
            }
        )
        mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, float)})
        model_path = work / "model.joblib"
        joblib.dump({"model": model, "classes": list(CLASSES)}, model_path)
        mlflow.log_artifact(str(model_path))
        report = classification_report(
            y_test if len(x_test) else y_val,
            test_pred,
            labels=list(range(len(CLASSES))),
            target_names=list(CLASSES),
            output_dict=True,
            zero_division=0,
        )
        (work / "metrics.json").write_text(json.dumps({"metrics": metrics, "report": report}, indent=2), encoding="utf-8")
        mlflow.log_artifact(str(work / "metrics.json"))
        run_id = run.info.run_id

    uri = storage.put_file("models", f"{run_id}/model.joblib", model_path, "application/octet-stream")
    digest = sha256_file(model_path)
    with connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO training_run (
                    run_id, dataset_version_id, task, stage, params_json, metrics_json, artifact_uri, status
                ) VALUES (
                    :rid, :dv, 'obstacle_cls', :stage, CAST(:params AS jsonb), CAST(:metrics AS jsonb), :uri, 'completed'
                )
                """
            ),
            {
                "rid": run_id,
                "dv": dataset_version_id,
                "stage": stage,
                "params": json.dumps({"estimator": "LogisticRegression"}),
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
        "sha256": digest,
        "metrics": metrics,
        "artifact_kind": "joblib",
        "local_model_path": str(model_path),
    }


def _load_split(split_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    xs: list[np.ndarray] = []
    ys: list[int] = []
    if not split_dir.exists():
        return np.empty((0, 12)), np.empty((0,), dtype=int)
    for idx, name in enumerate(CLASSES):
        class_dir = split_dir / name
        if not class_dir.exists():
            continue
        for img_path in sorted(class_dir.glob("*.jpg")):
            xs.append(_features(Image.open(img_path).convert("RGB")))
            ys.append(idx)
    if not xs:
        return np.empty((0, 12)), np.empty((0,), dtype=int)
    return np.vstack(xs), np.array(ys)


def _features(img: Image.Image) -> np.ndarray:
    small = np.array(img.resize((32, 24)), dtype=np.float32) / 255.0
    h, w, _ = small.shape
    ground = small[int(h * 0.55) :, :, :]
    sky = small[: int(h * 0.45), :, :]
    return np.array(
        [
            *sky.mean(axis=(0, 1)),
            *ground.mean(axis=(0, 1)),
            *small.reshape(-1, 3).std(axis=0),
            ground[..., 1].mean(),
            (small[..., 0] - small[..., 2]).mean(),
            (small[..., 0] > 0.55).mean(),
            small.mean(),
        ],
        dtype=np.float32,
    )


def _false_positive_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """FPR for 'clear' predicted as something else is less critical than obstacle/person missed.
    Here FPR = predicted obstacle/person among true clear.
    """
    if len(y_true) == 0:
        return 1.0
    clear = 0
    true_clear = y_true == 0
    if not true_clear.any():
        return 0.0
    return float(((y_pred != 0) & true_clear).sum() / true_clear.sum())

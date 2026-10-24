from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import text

from ugv_mlops.db import audit, connect, ensure_schema
from ugv_mlops.settings import settings


def enqueue(kind: str, payload: dict) -> str:
    with connect() as conn:
        job_id = conn.execute(
            text(
                """
                INSERT INTO job (kind, payload_json, status)
                VALUES (:kind, CAST(:payload AS jsonb), 'queued')
                RETURNING job_id
                """
            ),
            {"kind": kind, "payload": json.dumps(payload)},
        ).scalar_one()
        audit(conn, "job.enqueued", "job", str(job_id), {"kind": kind})
    return str(job_id)


def run_once() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT job_id, kind, payload_json
                FROM job
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
        ).mappings().first()
        if not row:
            return None
        conn.execute(
            text("UPDATE job SET status = 'running', started_at = now() WHERE job_id = :id"),
            {"id": row["job_id"]},
        )
        job_id = row["job_id"]
        kind = row["kind"]
        payload = row["payload_json"]

    try:
        result = _dispatch(kind, payload)
        with connect() as conn:
            conn.execute(
                text(
                    """
                    UPDATE job
                    SET status = 'completed', result_json = CAST(:res AS jsonb), finished_at = now()
                    WHERE job_id = :id
                    """
                ),
                {"id": job_id, "res": json.dumps(result, default=str)},
            )
            audit(conn, "job.completed", "job", str(job_id), {"kind": kind})
        return {"job_id": str(job_id), "kind": kind, "result": result}
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                text(
                    """
                    UPDATE job
                    SET status = 'failed', error = :err, finished_at = now()
                    WHERE job_id = :id
                    """
                ),
                {"id": job_id, "err": str(exc)},
            )
            audit(conn, "job.failed", "job", str(job_id), {"error": str(exc)})
        raise


def _dispatch(kind: str, payload: dict) -> dict:
    if kind == "ingest":
        from ugv_mlops.ingest import ingest_mission_dir

        return ingest_mission_dir(Path(payload["path"]), payload.get("source_type", "dropzone"))
    if kind == "build_dataset":
        from ugv_mlops.dataset import build_dataset

        return build_dataset(
            payload["dataset_id"],
            payload.get("name") or payload["dataset_id"],
            payload.get("task", "obstacle_cls"),
        )
    if kind == "train":
        from ugv_mlops.train import train_from_dataset

        return train_from_dataset(payload["dataset_version_id"], payload.get("stage"))
    if kind == "gate":
        from ugv_mlops.gate import evaluate_and_promote

        return evaluate_and_promote(
            payload["run_id"],
            payload.get("gate_name", "staging"),
            payload.get("target_status", "staging"),
            payload.get("mission_mode", "training"),
        )
    if kind == "package":
        from ugv_mlops.package import build_update_package

        return build_update_package(payload["model_id"], payload.get("target_stage", "S3"))
    if kind == "demo":
        from ugv_mlops.demo import run_demo

        return run_demo()
    if kind == "demo_e2e":
        from ugv_mlops.demo import run_e2e_demo

        return run_e2e_demo()
    if kind == "demo_yolox":
        from ugv_mlops.demo import run_yolox_demo

        return run_yolox_demo()
    if kind == "dvc_publish":
        from ugv_mlops.dvc_sync import publish_dataset, publish_mission

        if payload.get("dataset_version_id"):
            return publish_dataset(payload["dataset_version_id"], payload["object_uri"], push=payload.get("push", True))
        return publish_mission(payload["mission_id"], push=payload.get("push", True))
    if kind == "dvc_pull":
        from ugv_mlops.dvc_sync import pull

        return pull(payload.get("target"))
    if kind == "bag_parse":
        from ugv_mlops.bag_pipeline import parse_mission

        return parse_mission(payload["mission_id"])
    if kind == "bag_track":
        from ugv_mlops.bag_pipeline import track_mission

        return track_mission(payload["mission_id"], push=payload.get("push", False))
    if kind == "bag_store":
        from ugv_mlops.bag_pipeline import store_mission

        return store_mission(payload["mission_id"], push=payload.get("push", True))
    if kind == "annotate_init":
        from ugv_mlops.annotate_io import init_session

        return init_session(payload["mission_id"], payload.get("kind", "camera"), payload.get("limit", 200))
    if kind == "annotate_import_masks":
        from ugv_mlops.annotate_io import import_masks

        return import_masks(payload["session"], payload.get("masks_dir") or "", payload.get("tool", "sam-freespace"))
    raise ValueError(f"unknown job kind: {kind}")


def loop() -> None:
    settings.work_path.mkdir(parents=True, exist_ok=True)
    Path(settings.signing_key_path).parent.mkdir(parents=True, exist_ok=True)
    from ugv_mlops.gate import ensure_signing_keys

    ensure_signing_keys()
    ensure_schema()
    while True:
        ran = run_once()
        if not ran:
            time.sleep(2)


if __name__ == "__main__":
    loop()

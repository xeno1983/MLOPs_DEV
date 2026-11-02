from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import text

from ugv_mlops.db import audit, connect, ensure_schema
from ugv_mlops.settings import settings


AUTOE2E_TASKS = {"autoe2e", "auto_e2e", "autoe2e_finetune"}


def runner_for(kind: str, payload: dict) -> str:
    """どのワーカーが実行するか。AutoE2E は GPU とホストのファイルが要るので host、他は docker。"""
    if kind == "build_dataset" and payload.get("task") in AUTOE2E_TASKS:
        return "host"
    if kind == "train" and payload.get("dataset_version_id"):
        with connect() as conn:
            task = conn.execute(
                text(
                    """
                    SELECT d.task FROM dataset_version v
                    JOIN dataset d ON d.dataset_id = v.dataset_id
                    WHERE v.dataset_version_id = :id
                    """
                ),
                {"id": payload["dataset_version_id"]},
            ).scalar()
        if task in AUTOE2E_TASKS:
            return "host"
    return "docker"


def enqueue(kind: str, payload: dict) -> str:
    payload = {**payload, "_runner": payload.get("_runner") or runner_for(kind, payload)}
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
        audit(conn, "job.enqueued", "job", str(job_id), {"kind": kind, "runner": payload["_runner"]})
    return str(job_id)


def run_once(runner: str | None = None) -> dict | None:
    runner = runner or settings.job_runner
    with connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT job_id, kind, payload_json
                FROM job
                WHERE status = 'queued'
                  AND COALESCE(payload_json->>'_runner', 'docker') = :runner
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            ),
            {"runner": runner},
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
        if isinstance(payload, str):
            payload = json.loads(payload)

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
            mission_id=payload.get("mission_id"),
        )
    if kind == "train" and payload.get("_runner") == "host":
        # ホストワーカーの venv には sklearn / joblib がないので、train.py（それらを import する）を経由しない。
        from ugv_mlops.autoe2e import train_autoe2e

        return train_autoe2e(payload["dataset_version_id"], payload.get("stage"), payload.get("params"))
    if kind == "train":
        from ugv_mlops.train import train_from_dataset

        return train_from_dataset(payload["dataset_version_id"], payload.get("stage"), payload.get("params"))
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
    if kind == "demo_rosbag":
        from ugv_mlops.demo import run_av_rosbag_demo

        return run_av_rosbag_demo()
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

        return init_session(
            payload["mission_id"],
            payload.get("kind", "auto"),
            payload.get("limit", 200),
            payload.get("tool", "labelstudio"),
        )
    if kind == "annotate_import_masks":
        from ugv_mlops.annotate_io import import_masks

        return import_masks(payload["session"], payload.get("masks_dir") or "", payload.get("tool", "sam-freespace"))
    if kind == "annotate_import_jsonl":
        from ugv_mlops.annotate_io import import_jsonl

        return import_jsonl(payload["path"], payload.get("tool", "salt"))
    if kind == "data_qa":
        from ugv_mlops.pipeline_qa import run_data_qa

        return run_data_qa(
            payload["mission_id"],
            confirm=payload.get("confirm", True),
            note=payload.get("note") or "",
        )
    if kind == "label_review":
        from ugv_mlops.pipeline_qa import run_label_review

        return run_label_review(
            payload["mission_id"],
            confirm=payload.get("confirm", True),
            note=payload.get("note") or "",
        )
    if kind == "sim_generate_ack":
        from ugv_mlops.pipeline_qa import ack_sim_generate

        return ack_sim_generate(
            payload.get("note") or "",
            mission_id=payload.get("mission_id"),
        )
    if kind == "carla_import":
        from ugv_mlops.carla_bridge import import_bag_to_inbox

        return import_bag_to_inbox(
            payload["source"],
            mission_id=payload.get("mission_id"),
            note=payload.get("note") or "",
        )
    if kind == "lighten":
        from ugv_mlops.lighten import export_model

        return export_model(
            payload["model_id"],
            kind=payload.get("kind", "onnx"),
            target_stage=payload.get("target_stage"),
        )
    raise ValueError(f"unknown job kind: {kind}")


def loop(runner: str | None = None, keep_going: bool = False) -> None:
    """runner=docker は compose の worker、runner=host は GPU ホストワーカー（署名鍵は触らない）。"""
    runner = runner or settings.job_runner
    settings.work_path.mkdir(parents=True, exist_ok=True)
    if runner == "docker":
        Path(settings.signing_key_path).parent.mkdir(parents=True, exist_ok=True)
        from ugv_mlops.gate import ensure_signing_keys

        ensure_signing_keys()
    ensure_schema()
    while True:
        try:
            ran = run_once(runner)
        except Exception as exc:
            if not keep_going:
                raise
            # run_once が job を failed にして再送出する。ワーカーは次のジョブへ進む。
            print(f"[{runner}] job failed: {exc}", flush=True)
            continue
        if not ran:
            time.sleep(2)


if __name__ == "__main__":
    loop()

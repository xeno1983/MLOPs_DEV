from __future__ import annotations

from pathlib import Path

from ugv_mlops.dataset import build_dataset
from ugv_mlops.gate import ensure_signing_keys, evaluate_and_promote
from ugv_mlops.ingest import ingest_mission_dir
from ugv_mlops.package import build_update_package
from ugv_mlops.settings import settings
from ugv_mlops.sync import enqueue_sync, send_chunk
from ugv_mlops.synthetic import generate_mission
from ugv_mlops.train import train_obstacle_classifier


def run_demo(n_images: int = 60) -> dict:
    """End-to-end smoke: synthetic mission → dataset → train → gate → signed package → chunked sync."""
    ensure_signing_keys()
    mission_dir = settings.inbox_path / "M-DEMO-001"
    generate_mission(mission_dir, n_images=n_images)
    ingested = ingest_mission_dir(mission_dir)
    dataset = build_dataset("ds-obstacle-demo", "UGV obstacle classification demo")
    trained = train_obstacle_classifier(dataset["dataset_version_id"], settings.default_learning_stage)
    gated = evaluate_and_promote(trained["run_id"], gate_name="staging", target_status="staging")
    if not gated["passed"]:
        return {
            "ok": False,
            "ingested": ingested,
            "dataset": dataset,
            "trained": trained,
            "gated": gated,
        }
    packaged = build_update_package(gated["model_id"], target_stage="S3")
    sync = enqueue_sync(packaged["package_id"], "UGV-01")
    chunks = []
    while True:
        step = send_chunk(sync["tx_id"], max_bytes=4096)
        chunks.append(step)
        if step["done"]:
            break
        if len(chunks) > 10_000:
            break
    return {
        "ok": True,
        "ingested": {k: ingested[k] for k in ("mission_id", "files_count", "bytes_total")},
        "dataset": dataset,
        "trained": {"run_id": trained["run_id"], "metrics": trained["metrics"]},
        "gated": gated,
        "packaged": packaged,
        "sync": {"tx_id": sync["tx_id"], "chunks": len(chunks), "final": chunks[-1] if chunks else None},
    }


def run_e2e_demo(n_frames: int = 40) -> dict:
    ensure_signing_keys()
    mission_dir = settings.inbox_path / "M-E2E-001"
    from ugv_mlops.synthetic import generate_e2e_mission
    from ugv_mlops.ingest import ingest_path
    from ugv_mlops.train import train_from_dataset

    generate_e2e_mission(mission_dir, n_frames=n_frames)
    ingested = ingest_path(mission_dir, source_type="synthetic_e2e")
    dataset = build_dataset("ds-e2e-demo", "UGV E2E demo", task="e2e")
    trained = train_from_dataset(dataset["dataset_version_id"], settings.default_learning_stage)
    gated = evaluate_and_promote(trained["run_id"], gate_name="staging", target_status="staging")
    return {
        "ok": bool(gated.get("passed")),
        "ingested": {k: ingested.get(k) for k in ("mission_id", "files_count", "modalities")},
        "dataset": dataset,
        "trained": {"run_id": trained["run_id"], "metrics": trained["metrics"]},
        "gated": gated,
    }


def run_yolox_demo(max_images: int = 48) -> dict:
    """Public COCO128 + YOLOX-Nano: ingest → dataset → train → gate → package → sync."""
    ensure_signing_keys()
    from ugv_mlops.ingest import ingest_path
    from ugv_mlops.package import build_update_package
    from ugv_mlops.sync import enqueue_sync, send_chunk
    from ugv_mlops.train import train_from_dataset
    from ugv_mlops.yolox_sample import MISSION_ID, prepare_coco128

    prepared = prepare_coco128(settings.inbox_path / MISSION_ID, max_images=max_images)
    ingested = ingest_path(settings.inbox_path / MISSION_ID, source_type="coco128_public")
    dataset = build_dataset("ds-yolox-coco128", "YOLOX COCO128 public sample", task="detection")
    trained = train_from_dataset(dataset["dataset_version_id"], settings.default_learning_stage)
    gated = evaluate_and_promote(trained["run_id"], gate_name="staging", target_status="staging")
    result = {
        "ok": bool(gated.get("passed")),
        "prepared": prepared,
        "ingested": {k: ingested.get(k) for k in ("mission_id", "files_count", "modalities")},
        "dataset": dataset,
        "trained": {"run_id": trained["run_id"], "metrics": trained["metrics"]},
        "gated": gated,
    }
    if not gated.get("passed"):
        return result
    packaged = build_update_package(gated["model_id"], target_stage="S3")
    sync = enqueue_sync(packaged["package_id"], "UGV-01")
    chunks = []
    while True:
        step = send_chunk(sync["tx_id"], max_bytes=65536)
        chunks.append(step)
        if step["done"] or len(chunks) > 10_000:
            break
    result["ok"] = True
    result["packaged"] = packaged
    result["sync"] = {"tx_id": sync["tx_id"], "chunks": len(chunks), "final": chunks[-1] if chunks else None}
    return result


def run_av_rosbag_demo(max_seconds: float = 12.0) -> dict:
    """Public CostNav rosbag2: ingest → E2E dataset → train → gate → package → sync."""
    ensure_signing_keys()
    from ugv_mlops.av_rosbag_sample import MISSION_ID, prepare_av_rosbag
    from ugv_mlops.ingest import ingest_path
    from ugv_mlops.package import build_update_package
    from ugv_mlops.sync import enqueue_sync, send_chunk
    from ugv_mlops.train import train_from_dataset

    prepared = prepare_av_rosbag(settings.inbox_path / MISSION_ID, max_seconds=max_seconds)
    ingested = ingest_path(settings.inbox_path / MISSION_ID, source_type="costnav_rosbag2")
    dataset = build_dataset(
        "ds-av-rosbag",
        "CostNav public rosbag2 E2E sample",
        task="e2e",
        mission_id=MISSION_ID,
    )
    trained = train_from_dataset(dataset["dataset_version_id"], settings.default_learning_stage)
    gated = evaluate_and_promote(trained["run_id"], gate_name="smoke", target_status="staging")
    result = {
        "ok": bool(gated.get("passed")),
        "prepared": prepared,
        "ingested": {k: ingested.get(k) for k in ("mission_id", "files_count", "modalities")},
        "dataset": dataset,
        "trained": {"run_id": trained["run_id"], "metrics": trained["metrics"]},
        "gated": gated,
    }
    if not gated.get("passed"):
        return result
    packaged = build_update_package(gated["model_id"], target_stage="S3")
    sync = enqueue_sync(packaged["package_id"], "UGV-01")
    chunks = []
    while True:
        step = send_chunk(sync["tx_id"], max_bytes=65536)
        chunks.append(step)
        if step["done"] or len(chunks) > 10_000:
            break
    result["ok"] = True
    result["packaged"] = packaged
    result["sync"] = {"tx_id": sync["tx_id"], "chunks": len(chunks), "final": chunks[-1] if chunks else None}
    return result

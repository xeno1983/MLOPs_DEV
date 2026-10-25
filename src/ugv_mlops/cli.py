from __future__ import annotations

import json
from pathlib import Path

import typer

from ugv_mlops.settings import settings

app = typer.Typer(help="On-prem UGV MLOps CLI")


@app.command()
def demo(n_images: int = 60) -> None:
    from ugv_mlops.demo import run_demo

    result = run_demo(n_images=n_images)
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    raise typer.Exit(0 if result.get("ok") else 1)


@app.command()
def ingest(path: Path, source_type: str = "dropzone") -> None:
    from ugv_mlops.ingest import ingest_path

    typer.echo(json.dumps(ingest_path(path, source_type), indent=2, default=str))


@app.command("bag-topics")
def bag_topics(path: Path) -> None:
    from ugv_mlops.rosbag import list_topics

    typer.echo(json.dumps(list_topics(path), indent=2, ensure_ascii=False))


@app.command("demo-e2e")
def demo_e2e(n_frames: int = 40) -> None:
    from ugv_mlops.demo import run_e2e_demo

    result = run_e2e_demo(n_frames=n_frames)
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    raise typer.Exit(0 if result.get("ok") else 1)


@app.command("prepare-yolox")
def prepare_yolox(max_images: int = 48) -> None:
    from ugv_mlops.yolox_sample import prepare_coco128

    typer.echo(json.dumps(prepare_coco128(max_images=max_images), indent=2, default=str, ensure_ascii=False))


@app.command("demo-yolox")
def demo_yolox(max_images: int = 48) -> None:
    from ugv_mlops.demo import run_yolox_demo

    result = run_yolox_demo(max_images=max_images)
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    raise typer.Exit(0 if result.get("ok") else 1)


@app.command("prepare-av-rosbag")
def prepare_av_rosbag_cmd(max_seconds: float = 12.0) -> None:
    from ugv_mlops.av_rosbag_sample import prepare_av_rosbag

    typer.echo(json.dumps(prepare_av_rosbag(max_seconds=max_seconds), indent=2, default=str, ensure_ascii=False))


@app.command("demo-rosbag")
def demo_rosbag(max_seconds: float = 12.0) -> None:
    from ugv_mlops.demo import run_av_rosbag_demo

    result = run_av_rosbag_demo(max_seconds=max_seconds)
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    raise typer.Exit(0 if result.get("ok") else 1)


@app.command("build-dataset")
def build_dataset_cmd(dataset_id: str, name: str = "", task: str = "obstacle_cls") -> None:
    from ugv_mlops.dataset import build_dataset

    typer.echo(json.dumps(build_dataset(dataset_id, name or dataset_id, task), indent=2, default=str))


@app.command()
def train(dataset_version_id: str, stage: str = "") -> None:
    from ugv_mlops.train import train_from_dataset

    typer.echo(json.dumps(train_from_dataset(dataset_version_id, stage or None), indent=2, default=str))


@app.command()
def gate(run_id: str, gate_name: str = "staging") -> None:
    from ugv_mlops.gate import evaluate_and_promote

    typer.echo(json.dumps(evaluate_and_promote(run_id, gate_name), indent=2, default=str))


@app.command()
def package(model_id: str, target_stage: str = "S3") -> None:
    from ugv_mlops.package import build_update_package

    typer.echo(json.dumps(build_update_package(model_id, target_stage), indent=2, default=str))


@app.command("issue-open")
def issue_open(
    model_id: str,
    title: str,
    severity: str = "medium",
    category: str = "other",
    description: str = "",
    vehicle_id: str = "",
    assignee: str = "",
) -> None:
    from ugv_mlops.issues import create_issue

    result = create_issue(
        {
            "model_id": model_id,
            "title": title,
            "severity": severity,
            "category": category,
            "description": description,
            "vehicle_id": vehicle_id or None,
            "assignee": assignee or None,
        }
    )
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))


@app.command("issue-list")
def issue_list(status: str = "", model_id: str = "") -> None:
    from ugv_mlops.issues import list_issues

    result = list_issues(status=status or None, model_id=model_id or None)
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))


@app.command("issue-show")
def issue_show(issue_id: str) -> None:
    from ugv_mlops.issues import get_issue

    typer.echo(json.dumps(get_issue(issue_id), indent=2, default=str, ensure_ascii=False))


@app.command("issue-update")
def issue_update(
    issue_id: str,
    status: str = "",
    resolution: str = "",
    note: str = "",
    assignee: str = "",
) -> None:
    from ugv_mlops.issues import update_issue

    payload: dict = {}
    if status:
        payload["status"] = status
    if resolution:
        payload["resolution"] = resolution
    if note:
        payload["note"] = note
    if assignee:
        payload["assignee"] = assignee
    result = update_issue(issue_id, payload)
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))


@app.command("bag-import")
def bag_import_cmd(path: Path, mission_id: str = "") -> None:
    from ugv_mlops.bag_pipeline import import_mission

    typer.echo(json.dumps(import_mission(path, mission_id or None), indent=2, default=str, ensure_ascii=False))


@app.command("bag-parse")
def bag_parse_cmd(mission_id: str) -> None:
    from ugv_mlops.bag_pipeline import parse_mission

    typer.echo(json.dumps(parse_mission(mission_id), indent=2, default=str, ensure_ascii=False))


@app.command("bag-track")
def bag_track_cmd(mission_id: str, push: bool = False) -> None:
    from ugv_mlops.bag_pipeline import track_mission

    typer.echo(json.dumps(track_mission(mission_id, push=push), indent=2, default=str, ensure_ascii=False))


@app.command("bag-store")
def bag_store_cmd(mission_id: str, push: bool = True) -> None:
    from ugv_mlops.bag_pipeline import store_mission

    typer.echo(json.dumps(store_mission(mission_id, push=push), indent=2, default=str, ensure_ascii=False))


@app.command("bag-list")
def bag_list_cmd() -> None:
    from ugv_mlops.bag_pipeline import list_missions

    typer.echo(json.dumps(list_missions(), indent=2, default=str, ensure_ascii=False))


@app.command("dvc-status")
def dvc_status_cmd() -> None:
    from ugv_mlops.dvc_sync import status as dvc_status

    typer.echo(json.dumps(dvc_status(), indent=2, default=str, ensure_ascii=False))


@app.command("dvc-publish")
def dvc_publish_cmd(
    mission_id: str = "",
    dataset_version_id: str = "",
    object_uri: str = "",
    push: bool = True,
) -> None:
    from ugv_mlops.dvc_sync import publish_dataset, publish_mission

    if dataset_version_id:
        if not object_uri:
            raise typer.BadParameter("dataset_version_id には object_uri が必要です")
        result = publish_dataset(dataset_version_id, object_uri, push=push)
    elif mission_id:
        result = publish_mission(mission_id, push=push)
    else:
        raise typer.BadParameter("mission_id か dataset_version_id を指定してください")
    typer.echo(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    raise typer.Exit(0 if result.get("ok") else 1)


@app.command("annotate-status")
def annotate_status_cmd() -> None:
    from ugv_mlops.annotate_io import status as annotate_status

    typer.echo(json.dumps(annotate_status(), indent=2, default=str, ensure_ascii=False))


@app.command("annotate-init")
def annotate_init_cmd(
    mission_id: str,
    kind: str = "auto",
    limit: int = 200,
    tool: str = "labelstudio",
) -> None:
    from ugv_mlops.annotate_io import init_session

    typer.echo(
        json.dumps(init_session(mission_id, kind, limit, tool), indent=2, default=str, ensure_ascii=False)
    )


@app.command("annotate-import-masks")
def annotate_import_masks_cmd(session: str, masks_dir: str = "", tool: str = "sam-freespace") -> None:
    from ugv_mlops.annotate_io import import_masks

    typer.echo(json.dumps(import_masks(session, masks_dir, tool), indent=2, default=str, ensure_ascii=False))


@app.command("annotate-import-jsonl")
def annotate_import_jsonl_cmd(path: Path, tool: str = "salt") -> None:
    from ugv_mlops.annotate_io import import_jsonl

    typer.echo(json.dumps(import_jsonl(path, tool), indent=2, default=str, ensure_ascii=False))


@app.command()
def keys() -> None:
    from ugv_mlops.gate import ensure_signing_keys

    ensure_signing_keys()
    typer.echo(f"signing key: {settings.signing_key_path}")
    typer.echo(f"public key:  {settings.signing_pub_path}")


if __name__ == "__main__":
    app()

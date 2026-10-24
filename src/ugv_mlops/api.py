from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel
from sqlalchemy import text

from ugv_mlops.db import connect, ensure_schema
from ugv_mlops.jobs import enqueue
from ugv_mlops.settings import settings
from ugv_mlops.storage import ensure_buckets


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema()
    ensure_buckets()
    yield


app = FastAPI(
    title="UGV MLOps Catalog",
    description="On-prem dataset catalog, training jobs, promotion gates, and edge sync",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_ENQUEUED = Counter("ugv_jobs_enqueued_total", "Jobs enqueued", ["kind"])


class IngestReq(BaseModel):
    path: str
    source_type: str = "dropzone"


class DatasetReq(BaseModel):
    dataset_id: str
    name: str | None = None
    task: str = "obstacle_cls"


class TrainReq(BaseModel):
    dataset_version_id: str
    stage: str | None = None


class GateReq(BaseModel):
    run_id: str
    gate_name: str = "staging"
    target_status: str = "staging"
    mission_mode: str = "training"


class PackageReq(BaseModel):
    model_id: str
    target_stage: str = "S3"


class SyncReq(BaseModel):
    package_id: str
    vehicle_id: str = "UGV-01"


class DvcPublishReq(BaseModel):
    mission_id: str | None = None
    dataset_version_id: str | None = None
    object_uri: str | None = None
    push: bool = True


class MissionImportReq(BaseModel):
    source_path: str
    mission_id: str = ""


class AnnotateInitReq(BaseModel):
    mission_id: str
    kind: str = "camera"
    limit: int = 200


class AnnotateImportReq(BaseModel):
    session: str
    masks_dir: str = ""
    tool: str = "sam-freespace"


class IssueCreateReq(BaseModel):
    model_id: str
    title: str
    description: str = ""
    category: str = "other"
    severity: str = "medium"
    source: str = "operator"
    package_id: str | None = None
    vehicle_id: str | None = None
    related_sample_id: str | None = None
    related_run_id: str | None = None
    opened_by: str | None = None
    assignee: str | None = None


class IssuePatchReq(BaseModel):
    status: str | None = None
    assignee: str | None = None
    resolution: str | None = None
    vehicle_id: str | None = None
    package_id: str | None = None
    note: str | None = None
    actor: str | None = None


class IssueCommentReq(BaseModel):
    note: str
    actor: str | None = None


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> str:
    return """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>UGV MLOps Catalog</title>
  <style>
    body { font-family: "Segoe UI", "Yu Gothic UI", sans-serif; background:#0e1418; color:#e7eef4; margin:48px; }
    a { color:#3dd6c6; }
    p { line-height:1.6; }
  </style>
</head>
<body>
  <h1>Catalog API</h1>
  <p>このポートは API です。画面はポータルを開いてください。</p>
  <p><a href="http://localhost:8080/">http://localhost:8080/</a> — ダッシュボード / 8工程 / 課題</p>
  <p><a href="/docs">/docs</a> — OpenAPI</p>
  <p><a href="/health">/health</a> — 疎通確認</p>
</body>
</html>
"""


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict:
    try:
        with connect() as conn:
            conn.execute(text("SELECT 1"))
        db = "ok"
    except Exception as exc:
        db = f"error: {exc}"
    return {"status": "ok" if db == "ok" else "degraded", "db": db, "stage": settings.default_learning_stage}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
def status() -> dict:
    with connect() as conn:
        samples = conn.execute(text("SELECT count(*) FROM learning_sample")).scalar_one()
        datasets = conn.execute(text("SELECT count(*) FROM dataset_version")).scalar_one()
        models = conn.execute(text("SELECT count(*) FROM model_version")).scalar_one()
        jobs = conn.execute(
            text("SELECT status, count(*) AS n FROM job GROUP BY status")
        ).mappings().all()
        syncs = conn.execute(
            text("SELECT status, count(*) AS n FROM sync_transaction GROUP BY status")
        ).mappings().all()
    return {
        "samples": samples,
        "dataset_versions": datasets,
        "models": models,
        "jobs": {r["status"]: r["n"] for r in jobs},
        "sync": {r["status"]: r["n"] for r in syncs},
    }


def _count(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one() or 0)


@app.get("/pipeline")
def pipeline() -> dict:
    """One-screen flow: counts, next action, and latest job for the portal."""
    ensure_schema()
    with connect() as conn:
        missions = _count(conn, "SELECT count(*) FROM mission")
        samples = _count(conn, "SELECT count(*) FROM learning_sample")
        labeled = _count(conn, "SELECT count(DISTINCT sample_id) FROM annotation")
        datasets = _count(conn, "SELECT count(*) FROM dataset_version")
        runs = _count(conn, "SELECT count(*) FROM training_run")
        models = _count(conn, "SELECT count(*) FROM model_version")
        passed = _count(
            conn,
            "SELECT count(*) FROM model_version WHERE status IN ('staging','canary','production')",
        )
        packages = _count(conn, "SELECT count(*) FROM update_package")
        acked = _count(conn, "SELECT count(*) FROM sync_transaction WHERE status = 'acked'")
        latest_job = conn.execute(
            text(
                """
                SELECT job_id, kind, status, error, created_at, finished_at
                FROM job
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()
        latest_model = conn.execute(
            text(
                """
                SELECT model_id, status, stage, metrics_json, created_at
                FROM model_version
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()
        issues_open = _count(
            conn,
            "SELECT count(*) FROM model_issue WHERE status IN ('open', 'in_progress')",
        )

    steps = [
        {
            "id": "collect",
            "n": 1,
            "title": "帰還データを置く",
            "tool": "data/inbox",
            "count": missions,
            "unit": "ミッション",
            "how": "UGV帰還データを data/inbox/<mission>/ へ。ROS2 bag（metadata.yaml + .db3 / .mcap）でも、画像フォルダでも可。",
        },
        {
            "id": "ingest",
            "n": 2,
            "title": "カタログへ取り込む",
            "tool": "Catalog / Garage raw",
            "count": samples,
            "unit": "サンプル",
            "how": "POST /ingest（取込→追跡→パース→保管）。bag なら cameras/、lidar/*.bin、localization、cmd_vel を Garage と DVC へ。段階だけなら /missions。",
        },
        {
            "id": "annotate",
            "n": 3,
            "title": "ラベルを付ける",
            "tool": "Label Studio",
            "count": labeled,
            "unit": "ラベル済み",
            "how": "必須。クラス/bbox は Label Studio。Freespace は SAM、点群は SALT、確認は Xtreme1。E2E だけ cmd_vel が教師。",
        },
        {
            "id": "dataset",
            "n": 4,
            "title": "学習用データセットを切る",
            "tool": "Garage datasets",
            "count": datasets,
            "unit": "版",
            "how": "POST /datasets/build。知覚は task=obstacle_cls、検出は task=detection（YOLOX）、E2E は task=e2e。",
        },
        {
            "id": "train",
            "n": 5,
            "title": "学習する",
            "tool": "MLflow",
            "count": runs,
            "unit": "実験",
            "how": "POST /train。分類は精度、検出は YOLOX の mAP@0.5、E2E は steer/speed の MAE。",
        },
        {
            "id": "gate",
            "n": 6,
            "title": "評価して通す",
            "tool": "ゲート + 署名",
            "count": passed,
            "unit": "合格モデル",
            "how": "POST /gate。分類は精度・FPR、検出は map50、E2E は MAE。通ったものだけ署名される。",
        },
        {
            "id": "package",
            "n": 7,
            "title": "車載パッケージを作る",
            "tool": "Garage packages",
            "count": packages,
            "unit": "パッケージ",
            "how": "POST /packages。S1 は小さい成果物のみ。フル重みは S3/S4。",
        },
        {
            "id": "sync",
            "n": 8,
            "title": "UGV へ差分同期",
            "tool": "チャンク配送",
            "count": acked,
            "unit": "配送完了",
            "how": "POST /sync のあと、リンクが開いている間だけ chunk を送る。失敗時はロールバック。",
        },
    ]
    for step in steps:
        step["done"] = int(step["count"] or 0) > 0

    if samples == 0:
        hint = "まずデモを実行するか、data/inbox にミッションフォルダを置いて取り込んでください。"
        hint_step = "collect"
    elif labeled == 0:
        hint = "サンプルはあります。Label Studio でラベルを付けてください。"
        hint_step = "annotate"
    elif datasets == 0:
        hint = "ラベル済みです。データセットの版を切ってください。"
        hint_step = "dataset"
    elif runs == 0:
        hint = "データセットがあります。学習を回してください。"
        hint_step = "train"
    elif passed == 0:
        hint = "学習済みです。ゲートで評価・署名してください。"
        hint_step = "gate"
    elif packages == 0:
        hint = "合格モデルがあります。車載パッケージを作ってください。"
        hint_step = "package"
    elif acked == 0:
        hint = "パッケージがあります。UGV 向け同期を開始してください。"
        hint_step = "sync"
    else:
        hint = "閉ループが一巡しています。新しいミッションを inbox に足すと次の版が切れます。"
        hint_step = "sync"
    if issues_open > 0:
        hint = f"未解決の課題が {issues_open} 件あります。ポータルの「課題」タブでモデルに紐づけて追跡してください。"

    return {
        "learning_stage": settings.default_learning_stage,
        "cost": {
            "software_jpy": 0,
            "cloud_api_jpy": 0,
            "note": "OSS オンプレのため利用料・API課金は発生しません。電気・ディスク・GPU・作業工数のみです。",
        },
        "hint": hint,
        "hint_step": hint_step,
        "steps": steps,
        "totals": {
            "missions": missions,
            "samples": samples,
            "labeled": labeled,
            "datasets": datasets,
            "runs": runs,
            "models": models,
            "packages": packages,
            "acked": acked,
            "issues_open": issues_open,
        },
        "latest_job": dict(latest_job) if latest_job else None,
        "latest_model": dict(latest_model) if latest_model else None,
        "integrations": _integrations_status(),
    }


def _row(row) -> dict | None:
    if row is None:
        return None
    return jsonable_encoder(dict(row))


@app.get("/dashboard")
def dashboard() -> dict:
    """Aggregated KPIs, breakdowns, and recent activity for the portal dashboard."""
    ensure_schema()
    with connect() as conn:
        missions = _count(conn, "SELECT count(*) FROM mission")
        samples = _count(conn, "SELECT count(*) FROM learning_sample")
        labeled = _count(conn, "SELECT count(DISTINCT sample_id) FROM annotation")
        datasets = _count(conn, "SELECT count(*) FROM dataset_version")
        runs = _count(conn, "SELECT count(*) FROM training_run")
        models = _count(conn, "SELECT count(*) FROM model_version")
        passed = _count(
            conn,
            "SELECT count(*) FROM model_version WHERE status IN ('staging','canary','production')",
        )
        rejected = _count(conn, "SELECT count(*) FROM model_version WHERE status = 'rejected'")
        packages = _count(conn, "SELECT count(*) FROM update_package")
        vehicles = _count(conn, "SELECT count(*) FROM vehicle")
        jobs_total = _count(conn, "SELECT count(*) FROM job")
        jobs_failed = _count(conn, "SELECT count(*) FROM job WHERE status = 'failed'")
        sync_acked = _count(conn, "SELECT count(*) FROM sync_transaction WHERE status = 'acked'")
        sync_pending = _count(
            conn,
            "SELECT count(*) FROM sync_transaction WHERE status IN ('pending','sending','partial')",
        )
        modalities = conn.execute(
            text(
                """
                SELECT modality, count(*) AS n
                FROM learning_sample
                GROUP BY modality
                ORDER BY n DESC
                """
            )
        ).mappings().all()
        classifications = conn.execute(
            text(
                """
                SELECT classification, count(*) AS n
                FROM learning_sample
                GROUP BY classification
                ORDER BY n DESC
                """
            )
        ).mappings().all()
        tasks = conn.execute(
            text(
                """
                SELECT COALESCE(task, 'unknown') AS task, count(*) AS n
                FROM dataset
                GROUP BY task
                ORDER BY n DESC
                """
            )
        ).mappings().all()
        jobs_by_status = conn.execute(
            text("SELECT status, count(*) AS n FROM job GROUP BY status ORDER BY n DESC")
        ).mappings().all()
        models_by_status = conn.execute(
            text("SELECT status, count(*) AS n FROM model_version GROUP BY status ORDER BY n DESC")
        ).mappings().all()
        sync_by_status = conn.execute(
            text("SELECT status, count(*) AS n FROM sync_transaction GROUP BY status ORDER BY n DESC")
        ).mappings().all()
        activity = conn.execute(
            text(
                """
                SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day, count(*) AS n
                FROM learning_sample
                GROUP BY 1
                ORDER BY 1 DESC
                LIMIT 14
                """
            )
        ).mappings().all()
        mission_rows = conn.execute(
            text(
                """
                SELECT m.mission_id, m.vehicle_id, m.classification, m.theater, m.created_at,
                       (SELECT count(*) FROM learning_sample s WHERE s.mission_id = m.mission_id) AS samples
                FROM mission m
                ORDER BY m.created_at DESC
                LIMIT 15
                """
            )
        ).mappings().all()
        run_rows = conn.execute(
            text(
                """
                SELECT run_id, dataset_version_id, task, stage, status, metrics_json, created_at
                FROM training_run
                ORDER BY created_at DESC
                LIMIT 15
                """
            )
        ).mappings().all()
        model_rows = conn.execute(
            text(
                """
                SELECT model_id, name, version, stage, status, artifact_kind, metrics_json, created_at
                FROM model_version
                ORDER BY created_at DESC
                LIMIT 15
                """
            )
        ).mappings().all()
        job_rows = conn.execute(
            text(
                """
                SELECT job_id, kind, status, error, created_at, started_at, finished_at
                FROM job
                ORDER BY created_at DESC
                LIMIT 20
                """
            )
        ).mappings().all()
        dataset_rows = conn.execute(
            text(
                """
                SELECT d.dataset_id, d.name, d.task, v.dataset_version_id, v.version, v.stats_json, v.created_at
                FROM dataset d
                LEFT JOIN LATERAL (
                    SELECT * FROM dataset_version dv
                    WHERE dv.dataset_id = d.dataset_id
                    ORDER BY dv.version DESC
                    LIMIT 1
                ) v ON TRUE
                ORDER BY d.created_at DESC
                LIMIT 15
                """
            )
        ).mappings().all()
        ingest_rows = conn.execute(
            text(
                """
                SELECT session_id, mission_id, source_type, status, files_count, bytes_total, started_ts, error
                FROM ingest_session
                ORDER BY started_ts DESC
                LIMIT 10
                """
            )
        ).mappings().all()
        issues_open = _count(conn, "SELECT count(*) FROM model_issue WHERE status = 'open'")
        issues_in_progress = _count(
            conn, "SELECT count(*) FROM model_issue WHERE status = 'in_progress'"
        )
        issues_resolved = _count(conn, "SELECT count(*) FROM model_issue WHERE status = 'resolved'")
        issues_by_status = conn.execute(
            text("SELECT status, count(*) AS n FROM model_issue GROUP BY status ORDER BY n DESC")
        ).mappings().all()
        issue_rows = conn.execute(
            text(
                """
                SELECT i.issue_id, i.title, i.severity, i.status, i.category, i.model_id,
                       i.assignee, i.created_at, i.updated_at, m.name AS model_name
                FROM model_issue i
                JOIN model_version m ON m.model_id = i.model_id
                ORDER BY
                  CASE i.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                  CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                  i.created_at DESC
                LIMIT 12
                """
            )
        ).mappings().all()

    return {
        "learning_stage": settings.default_learning_stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "kpis": {
            "vehicles": vehicles,
            "missions": missions,
            "samples": samples,
            "labeled": labeled,
            "datasets": datasets,
            "runs": runs,
            "models": models,
            "passed": passed,
            "rejected": rejected,
            "packages": packages,
            "jobs": jobs_total,
            "jobs_failed": jobs_failed,
            "sync_acked": sync_acked,
            "sync_pending": sync_pending,
            "issues_open": issues_open,
            "issues_in_progress": issues_in_progress,
            "issues_resolved": issues_resolved,
        },
        "breakdowns": {
            "modalities": [_row(r) for r in modalities],
            "classifications": [_row(r) for r in classifications],
            "tasks": [_row(r) for r in tasks],
            "jobs": [_row(r) for r in jobs_by_status],
            "models": [_row(r) for r in models_by_status],
            "sync": [_row(r) for r in sync_by_status],
            "issues": [_row(r) for r in issues_by_status],
            "activity": list(reversed([_row(r) for r in activity])),
        },
        "recent": {
            "missions": [_row(r) for r in mission_rows],
            "runs": [_row(r) for r in run_rows],
            "models": [_row(r) for r in model_rows],
            "jobs": [_row(r) for r in job_rows],
            "datasets": [_row(r) for r in dataset_rows],
            "ingests": [_row(r) for r in ingest_rows],
            "issues": [_row(r) for r in issue_rows],
        },
        "integrations": _integrations_status(),
    }


def _integrations_status() -> dict:
    from ugv_mlops.integrations import probe

    return probe()


@app.get("/jobs")
def list_jobs(limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT job_id, kind, status, error, created_at, finished_at
                FROM job
                ORDER BY created_at DESC
                LIMIT :n
                """
            ),
            {"n": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/samples")
def list_samples(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT sample_id, mission_id, vehicle_id, modality, classification, object_uri, created_at
                FROM learning_sample
                ORDER BY created_at DESC
                LIMIT :n
                """
            ),
            {"n": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/datasets")
def list_datasets() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT d.dataset_id, d.name, d.task, v.dataset_version_id, v.version, v.stats_json, v.created_at
                FROM dataset d
                LEFT JOIN dataset_version v ON v.dataset_id = d.dataset_id
                ORDER BY d.created_at DESC, v.version DESC
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/models")
def list_models() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT model_id, name, version, stage, status, artifact_kind, metrics_json, created_at
                FROM model_version
                ORDER BY created_at DESC
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/models/deployed")
def deployed_models() -> list[dict]:
    from ugv_mlops.issues import list_deployed_models

    return jsonable_encoder(list_deployed_models())


@app.get("/issues")
def issues(
    status: str | None = None,
    model_id: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> dict:
    from ugv_mlops.issues import list_issues

    return jsonable_encoder(list_issues(status, model_id, severity, limit))


@app.get("/issues/{issue_id}")
def issue_get(issue_id: str) -> dict:
    from ugv_mlops.issues import get_issue

    return jsonable_encoder(get_issue(issue_id))


@app.post("/issues")
def issue_create(req: IssueCreateReq) -> dict:
    from ugv_mlops.issues import create_issue

    return jsonable_encoder(create_issue(req.model_dump()))


@app.patch("/issues/{issue_id}")
def issue_patch(issue_id: str, req: IssuePatchReq) -> dict:
    from ugv_mlops.issues import update_issue

    return jsonable_encoder(update_issue(issue_id, req.model_dump(exclude_none=True)))


@app.post("/issues/{issue_id}/comments")
def issue_comment(issue_id: str, req: IssueCommentReq) -> dict:
    from ugv_mlops.issues import add_comment

    return jsonable_encoder(add_comment(issue_id, req.note, req.actor))


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(text("SELECT * FROM job WHERE job_id = :id"), {"id": job_id}).mappings().first()
    if not row:
        raise HTTPException(404, "job not found")
    return dict(row)


@app.get("/samples/{sample_id}/file")
def sample_file(sample_id: str):
    from fastapi.responses import StreamingResponse

    from ugv_mlops.storage import client, parse_s3_uri

    with connect() as conn:
        row = conn.execute(
            text("SELECT object_uri FROM learning_sample WHERE sample_id = :id"),
            {"id": sample_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "sample not found")
    bucket, key = parse_s3_uri(row["object_uri"])
    obj = client().get_object(bucket, key)
    media = "image/jpeg"
    if key.lower().endswith(".png"):
        media = "image/png"
    return StreamingResponse(obj.stream(32 * 1024), media_type=media)


@app.get("/integrations")
def integrations() -> dict:
    from ugv_mlops.integrations import probe

    return probe()


@app.get("/dvc")
def dvc_status() -> dict:
    from ugv_mlops.bag_pipeline import list_missions
    from ugv_mlops.dvc_sync import status as dvc_status_fn

    st = dvc_status_fn()
    st["missions"] = list_missions()
    return st


@app.get("/missions")
def missions() -> dict:
    from ugv_mlops.bag_pipeline import list_missions

    return {"missions": list_missions()}


@app.get("/missions/{mission_id}")
def get_mission(mission_id: str) -> dict:
    from ugv_mlops.bag_pipeline import mission_detail

    detail = mission_detail(mission_id)
    if not detail["raw_exists"] and not detail["parsed_exists"]:
        raise HTTPException(404, "ミッションが見つかりません")
    return detail


@app.get("/missions/{mission_id}/topics")
def mission_topics(mission_id: str) -> dict:
    from ugv_mlops.bag_pipeline import mission_topics as topics_fn

    try:
        return topics_fn(mission_id)
    except FileNotFoundError:
        raise HTTPException(404, "ミッションがありません")


@app.post("/missions/import")
def mission_import(req: MissionImportReq) -> dict:
    from ugv_mlops.bag_pipeline import import_mission

    src = Path(req.source_path.strip().strip('"'))
    if not src.exists():
        raise HTTPException(400, f"パスが見つかりません: {src}")
    return import_mission(src, req.mission_id or None)


@app.post("/missions/{mission_id}/parse")
def mission_parse(mission_id: str) -> dict:
    JOBS_ENQUEUED.labels("bag_parse").inc()
    return {"job_id": enqueue("bag_parse", {"mission_id": mission_id})}


@app.post("/missions/{mission_id}/track")
def mission_track(mission_id: str) -> dict:
    JOBS_ENQUEUED.labels("bag_track").inc()
    return {"job_id": enqueue("bag_track", {"mission_id": mission_id})}


@app.post("/missions/{mission_id}/store")
def mission_store(mission_id: str, push: bool = True) -> dict:
    JOBS_ENQUEUED.labels("bag_store").inc()
    return {"job_id": enqueue("bag_store", {"mission_id": mission_id, "push": push})}


@app.post("/dvc/publish")
def dvc_publish(req: DvcPublishReq) -> dict:
    if not req.mission_id and not (req.dataset_version_id and req.object_uri):
        raise HTTPException(400, "mission_id か dataset_version_id+object_uri が必要です")
    JOBS_ENQUEUED.labels("dvc_publish").inc()
    return {"job_id": enqueue("dvc_publish", req.model_dump())}


@app.post("/dvc/pull")
def dvc_pull(target: str | None = None) -> dict:
    JOBS_ENQUEUED.labels("dvc_pull").inc()
    return {"job_id": enqueue("dvc_pull", {"target": target})}


@app.get("/annotate")
def annotate_status() -> dict:
    from ugv_mlops.annotate_io import status as annotate_status_fn

    return annotate_status_fn()


@app.post("/annotate/init")
def annotate_init(req: AnnotateInitReq) -> dict:
    JOBS_ENQUEUED.labels("annotate_init").inc()
    return {"job_id": enqueue("annotate_init", req.model_dump())}


@app.post("/annotate/import-masks")
def annotate_import(req: AnnotateImportReq) -> dict:
    JOBS_ENQUEUED.labels("annotate_import_masks").inc()
    return {"job_id": enqueue("annotate_import_masks", req.model_dump())}


@app.post("/ingest")
def ingest(req: IngestReq) -> dict:
    if not Path(req.path).exists():
        raise HTTPException(400, f"path not found: {req.path}")
    JOBS_ENQUEUED.labels("ingest").inc()
    return {"job_id": enqueue("ingest", req.model_dump())}


@app.post("/datasets/build")
def build(req: DatasetReq) -> dict:
    JOBS_ENQUEUED.labels("build_dataset").inc()
    return {"job_id": enqueue("build_dataset", req.model_dump())}


@app.post("/train")
def train(req: TrainReq) -> dict:
    JOBS_ENQUEUED.labels("train").inc()
    return {"job_id": enqueue("train", req.model_dump())}


@app.post("/gate")
def gate(req: GateReq) -> dict:
    JOBS_ENQUEUED.labels("gate").inc()
    return {"job_id": enqueue("gate", req.model_dump())}


@app.post("/packages")
def packages(req: PackageReq) -> dict:
    JOBS_ENQUEUED.labels("package").inc()
    return {"job_id": enqueue("package", req.model_dump())}


@app.post("/sync")
def sync(req: SyncReq) -> dict:
    from ugv_mlops.sync import enqueue_sync

    return enqueue_sync(req.package_id, req.vehicle_id)


@app.post("/sync/{tx_id}/chunk")
def sync_chunk(tx_id: str, max_bytes: int = 65536) -> dict:
    from ugv_mlops.sync import send_chunk

    return send_chunk(tx_id, max_bytes)


@app.post("/demo")
def demo() -> dict:
    JOBS_ENQUEUED.labels("demo").inc()
    return {"job_id": enqueue("demo", {})}


@app.post("/demo-e2e")
def demo_e2e() -> dict:
    JOBS_ENQUEUED.labels("demo_e2e").inc()
    return {"job_id": enqueue("demo_e2e", {})}


@app.post("/demo-yolox")
def demo_yolox() -> dict:
    JOBS_ENQUEUED.labels("demo_yolox").inc()
    return {"job_id": enqueue("demo_yolox", {})}

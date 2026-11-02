"""AutoE2E（Autoware auto_e2e / Reactive）のオフロード微調整を Catalog のパイプラインに載せる。

    ROS2 bag ──build_dataset(task=autoe2e)──► scene zip (Garage datasets, format autoe2e-scenes-v1)
                                                   │
                                      train ◄──────┘  (GPU ホストワーカー)
                                         │
              MLflow run ◄── 指標 ──► training_run ── gate ── model_version ── package

実体の切出し（tools/rosbag2_autoe2e）と学習（tools/auto_e2e_finetune_offroad.py）は
Autoware_e2e リポジトリにあり、ここからは **サブプロセスで呼ぶだけ**（コードは複製しない）。
CUDA が要るため compose の docker worker では動かない。runner=host のジョブとして
`python -m ugv_mlops.host_worker` が拾う（jobs.runner_for 参照）。
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings
from ugv_mlops.storage import parse_s3_uri

TASK = "autoe2e"
FORMAT = "autoe2e-scenes-v1"
ARTIFACT_KIND = "full_weights"
ALLOWED_STAGES = {"S3", "S4"}  # artifact_policy.yaml: full_weights は S3/S4 のみ
EXTRACT_REL = Path("tools") / "rosbag2_autoe2e" / "cli.py"
TRAINER_REL = Path("tools") / "auto_e2e_finetune_offroad.py"

Log = Callable[[str], None]


def _log(line: str) -> None:
    print(line, flush=True)


# ---------------------------------------------------------------- environment


def autoware_root() -> Path:
    root = Path(settings.autoware_e2e_root).expanduser()
    if not (root / TRAINER_REL).is_file():
        raise RuntimeError(
            f"Autoware_e2e が見つかりません: {root}（{TRAINER_REL} がない）。"
            ".env の AUTOWARE_E2E_ROOT を Autoware_e2e の実体に合わせてください。"
        )
    return root.resolve()


def python_exe(root: Path) -> Path:
    if settings.autoe2e_python:
        py = Path(settings.autoe2e_python)
    elif os.name == "nt":
        py = root / ".venv-autoe2e" / "Scripts" / "python.exe"
    else:
        py = root / ".venv-autoe2e" / "bin" / "python"
    if not py.is_file():
        raise RuntimeError(
            f"AutoE2E 用 Python が見つかりません: {py}。"
            "Autoware_e2e/docs/reference/AUTO_E2E_OFFROAD.md の Stage 0 で .venv-autoe2e を作ってください。"
        )
    return py


def rig_path(root: Path) -> Path:
    rig = Path(settings.autoe2e_rig) if settings.autoe2e_rig else root / "config" / "rosbag2_autoe2e" / "rig.yaml"
    if not rig.is_file():
        raise RuntimeError(
            f"rig 設定がありません: {rig}。config/rosbag2_autoe2e/rig.example.yaml をコピーして"
            "トピックとフレームを自分の車両に合わせてください。"
        )
    return rig


def _run(cmd: list[str], cwd: Path, log_file: Path | None = None) -> int:
    """子プロセスの出力を流しつつ（任意で）ファイルにも残す。"""
    _log("$ " + " ".join(cmd))
    sink = log_file.open("w", encoding="utf-8") if log_file else None
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip()
            _log(line)
            if sink:
                sink.write(line + "\n")
                sink.flush()
        return proc.wait()
    finally:
        if sink:
            sink.close()


# -------------------------------------------------------------------- dataset


def _bags_for(mission_id: str) -> list[Path]:
    from ugv_mlops.bag_pipeline import raw_dir
    from ugv_mlops.rosbag import discover_bags

    for base in (raw_dir(mission_id), settings.inbox_path / mission_id):
        if base.exists():
            bags = [b for b in discover_bags(base) if b.is_dir() or b.suffix == ".mcap"]
            if bags:
                return bags
    raise RuntimeError(
        f"{mission_id}: rosbag2（metadata.yaml のあるフォルダか .mcap）が data/dvc/raw/missions にも data/inbox にもありません。"
        "AutoE2E は展開済み JPEG ではなく bag 原本から切り出します。"
    )


def _scene_dirs(base: Path) -> list[Path]:
    return sorted(p.parent for p in base.rglob("meta.json") if (p.parent / "poses.txt").is_file())


def _scene_stats(scenes: list[Path]) -> dict:
    frames = samples = 0
    rows = []
    for d in scenes:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        frames += int(meta.get("frames", 0))
        samples += max(0, int(meta.get("valid_samples", 0)))
        rows.append({"scene": d.name, "frames": int(meta.get("frames", 0)), "valid_samples": int(meta.get("valid_samples", 0))})
    return {"scenes": len(scenes), "frames": frames, "samples": samples, "scene_list": rows}


def build_autoe2e_dataset(dataset_id: str, name: str, mission_id: str | None = None) -> dict:
    """bag → AutoE2E scene → zip → Garage datasets → dataset_version。"""
    from ugv_mlops.dataset import next_version

    if not mission_id:
        raise RuntimeError("task=autoe2e には mission_id が必要です（切り出す bag のミッション）。")
    root = autoware_root()
    py = python_exe(root)
    rig = rig_path(root)
    bags = _bags_for(mission_id)

    work = settings.work_path / "datasets" / dataset_id
    if work.exists():
        shutil.rmtree(work)
    scenes_dir = work / "scenes" / mission_id
    scenes_dir.mkdir(parents=True)

    cmd = [
        str(py), str(root / EXTRACT_REL), "extract",
        *[str(b) for b in bags],
        "--rig", str(rig), "--prefix", mission_id, "--out", str(scenes_dir), "--force",
    ]
    code = _run(cmd, root)
    if code != 0:
        raise RuntimeError(f"AutoE2E 切出しに失敗しました（exit {code}）")
    scenes = _scene_dirs(scenes_dir)
    if not scenes:
        raise RuntimeError("scene が 1 つも出ませんでした（短すぎる・停止が長い・pose/camera が足りない）。ログを確認してください。")

    stats = {"task": TASK, "format": FORMAT, "mission_id": mission_id, "bags": [b.name for b in bags], **_scene_stats(scenes)}
    stats["rig_sha256"] = sha256_file(rig)
    (work / "dataset.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    # JPEG は既に圧縮済みなので zip は格納のみ（速度優先）。
    import zipfile

    archive = work.parent / f"{dataset_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(work).as_posix())
    digest = sha256_file(archive)
    version = next_version(dataset_id)
    version_id = f"{dataset_id}-v{version}"
    uri = storage.put_file("datasets", f"{dataset_id}/{version_id}.zip", archive, "application/zip")
    archive.unlink(missing_ok=True)

    with connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dataset (dataset_id, name, task, description)
                VALUES (:id, :name, :task, :desc)
                ON CONFLICT (dataset_id) DO UPDATE SET name = EXCLUDED.name, task = EXCLUDED.task
                """
            ),
            {"id": dataset_id, "name": name, "task": TASK, "desc": "AutoE2E scenes cut from rosbag2 (6 cams + native front + poses)"},
        )
        conn.execute(
            text(
                """
                INSERT INTO dataset_version (dataset_version_id, dataset_id, version, format, object_uri, sha256, stats_json)
                VALUES (:id, :ds, :ver, :fmt, :uri, :sha, CAST(:stats AS jsonb))
                """
            ),
            {"id": version_id, "ds": dataset_id, "ver": version, "fmt": FORMAT, "uri": uri, "sha": digest, "stats": json.dumps(stats)},
        )
        # dataset_item は learning_sample（ingest のファイル単位）への FK なので scene では作らない。
        audit(conn, "dataset.built", "dataset_version", version_id, {k: stats[k] for k in ("task", "scenes", "frames", "samples")})

    result = {
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "version": version,
        "object_uri": uri,
        "sha256": digest,
        "stats": {k: v for k, v in stats.items() if k != "scene_list"},
    }
    from ugv_mlops.integrations import attach_dataset_remotes

    result.update(attach_dataset_remotes(version_id, uri))
    return result


# ---------------------------------------------------------------------- train


def _read_json(path: Path) -> dict | list | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _collect(out: Path) -> dict:
    """トレーナーの成果（train_config.json / history.json / checkpoint）から Catalog 用の指標を組む。"""
    cfg = _read_json(out / "train_config.json") or {}
    hist = _read_json(out / "history.json") or []
    if not hist:
        raise RuntimeError("history.json がありません（学習が 1 epoch も完了していません）")
    base = next((e["val"] for e in hist if e["epoch"] == 0), None)
    trained = [e for e in hist if e["epoch"] > 0]
    if not trained:
        raise RuntimeError("学習 epoch が 0 です")
    best_ckpt = out / "best" / "checkpoint.pt"
    last_ckpt = out / "last" / "checkpoint.pt"
    if best_ckpt.is_file():
        tag, path = "best", best_ckpt
        # best/ は「親（または以前の best）より良かった epoch」。履歴上の最小 ADE がそれ。
        chosen = min(trained, key=lambda e: e["val"]["ade_6p4s_m"])
    elif last_ckpt.is_file():
        tag, path = "last", last_ckpt
        chosen = trained[-1]
    else:
        raise RuntimeError("checkpoint が出力されていません")
    val = chosen["val"]
    cv = cfg.get("constant_velocity_baseline") or {}
    n_val = int(cfg.get("val_samples") or 0)
    metrics: dict = {
        "ade_6p4s_m": float(val["ade_6p4s_m"]),
        "fde_6p4s_m": float(val["fde_6p4s_m"]),
        "n_train": int(cfg.get("train_samples") or 0),
        "n_val": n_val,
        "n_test": n_val,  # ゲートの holdout 判定用。AutoE2E の保留検証がそのまま holdout
        "epochs_run": len(trained),
        "best_epoch": int(chosen["epoch"]),
        "artifact_tag": tag,
    }
    if base is not None:
        metrics["parent_ade_6p4s_m"] = float(base["ade_6p4s_m"])
        metrics["parent_fde_6p4s_m"] = float(base["fde_6p4s_m"])
        metrics["improved_over_parent"] = bool(val["ade_6p4s_m"] < base["ade_6p4s_m"])
    if cv.get("ade_6p4s_m") is not None:
        metrics["cv_ade_6p4s_m"] = float(cv["ade_6p4s_m"])
        metrics["cv_fde_6p4s_m"] = float(cv.get("fde_6p4s_m", 0.0))
    return {"metrics": metrics, "checkpoint": path, "tag": tag, "config": cfg, "history": hist}


def _end_mlflow(active: bool, status: str) -> None:
    """MLflow の後始末の失敗（コンソール文字コード等）で、完了した学習をジョブ失敗にしない。"""
    if not active:
        return
    import mlflow

    try:
        mlflow.end_run(status=status)
    except Exception as exc:
        _log(f"MLflow end_run に失敗（無視）: {exc}")


def train_autoe2e(dataset_version_id: str, stage: str | None = None, params: dict | None = None) -> dict:
    import mlflow

    stage = stage or settings.default_learning_stage
    if stage not in ALLOWED_STAGES:
        raise PermissionError(f"AutoE2E のフル重みは学習段 {sorted(ALLOWED_STAGES)} のみ（指定: {stage}）。artifact_policy.yaml を参照。")
    params = params or {}
    root = autoware_root()
    py = python_exe(root)

    work = settings.work_path / "train" / dataset_version_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with connect() as conn:
        row = conn.execute(
            text("SELECT object_uri, format, stats_json FROM dataset_version WHERE dataset_version_id = :id"),
            {"id": dataset_version_id},
        ).mappings().one()
    if row["format"] != FORMAT:
        raise RuntimeError(f"データセット形式が違います: {row['format']}（{FORMAT} が必要）")
    archive = work / "dataset.zip"
    storage.get_file(*parse_s3_uri(row["object_uri"]), archive)
    shutil.unpack_archive(archive, work / "data")
    archive.unlink(missing_ok=True)
    scenes = _scene_dirs(work / "data")
    if not scenes:
        raise RuntimeError("データセットに scene がありません")

    epochs = int(params.get("epochs") or settings.autoe2e_epochs)
    stride = int(params.get("stride") or settings.autoe2e_stride)
    accum = int(params.get("accum_steps") or settings.autoe2e_accum_steps)
    extra = shlex.split(str(params.get("extra") or settings.autoe2e_extra_args), posix=(os.name != "nt"))
    out = work / "out"
    cmd = [
        str(py), str(root / TRAINER_REL),
        "--scenes", *[str(s) for s in scenes],
        "--out", str(out),
        "--epochs", str(epochs), "--stride", str(stride), "--accum-steps", str(accum),
        *extra,
    ]

    mlflow_ok = True
    try:
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment("ugv-autoe2e")
        run_ctx = mlflow.start_run(run_name=f"{dataset_version_id}-{stage}")
        run_id = run_ctx.info.run_id
    except Exception as exc:  # MLflow が落ちていても数時間の学習を無駄にしない
        mlflow_ok = False
        run_id = uuid.uuid4().hex
        _log(f"MLflow に接続できません（run は Catalog にだけ記録します）: {exc}")

    t0 = time.time()
    try:
        code = _run(cmd, root, work / "train.log")
        if code != 0:
            raise RuntimeError(f"AutoE2E 学習に失敗しました（exit {code}）。ログ: {work / 'train.log'}")
        got = _collect(out)
        metrics, ckpt = got["metrics"], got["checkpoint"]
        cfg = got["config"]
        args = cfg.get("args") or {}
        run_params = {
            "dataset_version_id": dataset_version_id, "stage": stage, "task": TASK, "recipe": "kitscenes_finetune_mapless_v1",
            "epochs": epochs, "stride": stride, "accum_steps": accum,
            "lr": args.get("lr"), "precision": cfg.get("precision"),
            "parent": args.get("parent"), "parent_sha256": args.get("parent_sha256"),
            "dataset_manifest_sha256": cfg.get("dataset_manifest_sha256"),
            "split": json.dumps(cfg.get("split")), "cmd": " ".join(cmd),
        }
        if mlflow_ok:
            try:
                mlflow.log_params({k: v for k, v in run_params.items() if v is not None})
                mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, float)})
                for e in got["history"]:
                    if e["epoch"] > 0:
                        mlflow.log_metrics(
                            {"val_ade": e["val"]["ade_6p4s_m"], "val_fde": e["val"]["fde_6p4s_m"],
                             **({"train_trajectory": e["train"]["trajectory"]} if e.get("train") else {})},
                            step=int(e["epoch"]),
                        )
                for small in ("train_config.json", "history.json"):  # 335MB の checkpoint は MLflow に載せない（Garage models）
                    if (out / small).is_file():
                        mlflow.log_artifact(str(out / small))
                if (work / "train.log").is_file():
                    mlflow.log_artifact(str(work / "train.log"))
            except Exception as exc:
                _log(f"MLflow への記録に一部失敗: {exc}")
        digest = sha256_file(ckpt)
        metrics["checkpoint_sha256"] = digest
        metrics["train_seconds"] = round(time.time() - t0, 1)
        uri = storage.put_file("models", f"{run_id}/checkpoint.pt", ckpt)
        storage.put_file("models", f"{run_id}/train_config.json", out / "train_config.json", "application/json")
        with connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO training_run (
                        run_id, dataset_version_id, task, stage, params_json, metrics_json, artifact_uri, status
                    ) VALUES (
                        :rid, :dv, :task, :stage, CAST(:params AS jsonb), CAST(:metrics AS jsonb), :uri, 'completed'
                    )
                    """
                ),
                {
                    "rid": run_id, "dv": dataset_version_id, "task": TASK, "stage": stage,
                    "params": json.dumps({k: v for k, v in run_params.items() if v is not None}, default=str),
                    "metrics": json.dumps(metrics), "uri": uri,
                },
            )
            audit(conn, "train.completed", "training_run", run_id, metrics)
        _end_mlflow(mlflow_ok, "FINISHED")
    except BaseException:
        _end_mlflow(mlflow_ok, "FAILED")
        raise

    return {
        "run_id": run_id,
        "dataset_version_id": dataset_version_id,
        "stage": stage,
        "artifact_uri": uri,
        "sha256": digest,
        "metrics": metrics,
        "artifact_kind": ARTIFACT_KIND,
        "task": TASK,
        "local_model_path": str(ckpt),
    }

"""取込データ確認（工程4）とラベル確認（工程6）、シミュ確認（工程1）。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from ugv_mlops.db import audit, connect
from ugv_mlops.preview import mission_preview
from ugv_mlops.settings import settings


def run_data_qa(mission_id: str, confirm: bool = True, note: str = "") -> dict:
    """Check ingested mission content and record operator confirmation.

    Hard-fails (raises) when confirm=True but content is insufficient for any path.
    """
    prev = mission_preview(mission_id)
    modalities = prev.get("modalities") or {}
    cameras = int(sum(v for k, v in modalities.items() if str(k).startswith("camera")))
    if cameras == 0:
        cameras = int(len(prev.get("cameras") or []))
    actions = int(modalities.get("action") or modalities.get("cmd_vel") or 0)
    if actions == 0 and (prev.get("series") or {}).get("action"):
        actions = int((prev["series"]["action"] or {}).get("n") or 0)
    lidar = int(modalities.get("lidar") or 0)
    samples = int(prev.get("samples") or 0)
    labeled = int(prev.get("labeled") or 0)
    checks = {
        "ingested": bool(prev.get("ingested")),
        "has_camera": cameras > 0,
        "has_action": actions > 0,
        "has_lidar": lidar > 0,
        "cameras": cameras,
        "actions": actions,
        "lidar": lidar,
        "topics": len(prev.get("topics") or []),
        "samples": samples,
        "labeled": labeled,
    }
    warnings: list[str] = []
    errors: list[str] = []

    if not checks["ingested"]:
        errors.append("まだ取込されていません")
    if samples <= 0:
        errors.append("サンプルが 0 件です（空 bag / パース失敗の可能性）")

    # Path inference: action/cmd_vel → E2E; else perception (needs camera)
    e2e_path = checks["has_action"]
    if e2e_path:
        if not checks["has_camera"]:
            warnings.append("E2E ですがカメラがありません（通常は必要）")
        if not checks["has_action"]:
            errors.append("E2E 経路なのに action/cmd_vel がありません")
    else:
        if not checks["has_camera"]:
            errors.append("カメラがありません（知覚・検出では必須）")
        if labeled <= 0:
            warnings.append("ラベルがまだありません。知覚なら工程5で付与、E2E なら cmd_vel 付き bag を入れてください")
        if checks["has_lidar"] and not checks["has_camera"]:
            errors.append("LiDAR のみでは知覚データセットを切れません（カメラが必要）")

    auto_ok = not errors
    passed = bool(confirm) and auto_ok
    with connect() as conn:
        audit(
            conn,
            "data_qa.passed" if passed else "data_qa.recorded",
            "mission",
            mission_id,
            {
                "checks": checks,
                "warnings": warnings,
                "errors": errors,
                "note": note,
                "confirm": confirm,
                "e2e_path": e2e_path,
            },
        )
    if confirm and not auto_ok:
        raise RuntimeError("データ確認不合格: " + "; ".join(errors))
    return {
        "ok": passed,
        "mission_id": mission_id,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "passed": passed,
        "e2e_path": e2e_path,
        "next": "知覚なら工程5、E2E（cmd_vel 教師）なら工程7へ",
    }


def run_label_review(mission_id: str, confirm: bool = True, note: str = "") -> dict:
    """Record that the operator reviewed labels / sensor alignment (Xtreme1)."""
    prev = mission_preview(mission_id)
    modalities = prev.get("modalities") or {}
    labeled = int(prev.get("labeled") or 0)
    actions = int(modalities.get("action") or modalities.get("cmd_vel") or 0)
    if actions == 0 and (prev.get("series") or {}).get("action"):
        actions = int((prev["series"]["action"] or {}).get("n") or 0)
    e2e_ok = actions > 0
    perception_ok = labeled > 0
    warnings: list[str] = []
    errors: list[str] = []
    if not perception_ok and not e2e_ok:
        errors.append(
            "ラベルも action/cmd_vel もありません。知覚なら Label Studio、E2E なら cmd_vel を確認してください"
        )
    passed = bool(confirm) and not errors
    with connect() as conn:
        audit(
            conn,
            "label_review.passed" if passed else "label_review.recorded",
            "mission",
            mission_id,
            {
                "labeled": labeled,
                "actions": actions,
                "e2e_path": e2e_ok and not perception_ok,
                "note": note,
                "confirm": confirm,
                "errors": errors,
            },
        )
    if confirm and errors:
        raise RuntimeError("ラベル確認不合格: " + "; ".join(errors))
    return {
        "ok": passed,
        "mission_id": mission_id,
        "labeled": labeled,
        "actions": actions,
        "passed": passed,
        "warnings": warnings,
        "errors": errors,
        "open_url": "http://127.0.0.1:8190",
        "next": "工程7でデータセットを切る",
    }


def ack_sim_generate(note: str = "", mission_id: str | None = None) -> dict:
    """Operator acknowledges external CARLA / Autoware sim run; bags go to inbox.

    If mission_id is given, require a bag-like layout under data/inbox/<mission_id>/.
    """
    inbox = settings.inbox_path
    proof: dict = {"inbox": str(inbox), "mission_id": mission_id}
    if mission_id:
        mid = mission_id.strip().replace("..", "").replace("/", "").replace("\\", "")
        folder = inbox / mid
        if not folder.is_dir():
            raise FileNotFoundError(f"inbox にミッションがありません: {folder}")
        bag_ok = _looks_like_bag(folder)
        files = [p.name for p in folder.iterdir()][:40]
        proof.update({"folder": str(folder), "files": files, "bag_ok": bag_ok})
        if not bag_ok:
            raise RuntimeError(
                f"{folder} に rosbag2（metadata.yaml + .db3/.mcap）または画像が見つかりません"
            )
    else:
        # Soft check: warn via return if inbox empty, but still allow skip-path ack
        dirs = [p.name for p in inbox.iterdir()] if inbox.is_dir() else []
        proof["inbox_missions"] = dirs[:50]
        if not dirs:
            note = (note or "") + " | warning:inbox_empty"

    with connect() as conn:
        audit(
            conn,
            "pipeline.sim_generate",
            "pipeline",
            mission_id or "sim_generate",
            {
                "note": note or "CARLA/Autoware_e2e で bag を data/inbox へ置く手順を確認した",
                "docs": "docs/pipeline-stages.md",
                "proof": proof,
            },
        )
        n = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE action = 'pipeline.sim_generate'")
        ).scalar_one()
    return {
        "ok": True,
        "acked": True,
        "count": int(n),
        "mission_id": mission_id,
        "proof": proof,
        "next": "data/inbox/<mission_id>/ に bag を置いて工程2へ",
        "howto": "Autoware_e2e の OFFROAD_CARLA_HYBRID.md に従い rosbag2 を録り、inbox へコピーする。",
    }


def _looks_like_bag(folder: Path) -> bool:
    names = {p.name.lower() for p in folder.rglob("*") if p.is_file()}
    has_meta = "metadata.yaml" in names
    has_db = any(n.endswith(".db3") or n.endswith(".mcap") for n in names)
    has_images = any(n.endswith((".jpg", ".jpeg", ".png")) for n in names)
    return (has_meta and has_db) or has_images or has_db


def count_audit(action: str) -> int:
    with connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM audit_log WHERE action = :a"),
                {"a": action},
            ).scalar_one()
            or 0
        )


def count_light_models() -> int:
    with connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM model_version
                    WHERE artifact_kind IN ('onnx', 'adapter', 'distilled_student', 'threshold', 'calibration')
                      AND status IN ('staging', 'canary', 'production')
                    """
                )
            ).scalar_one()
            or 0
        )

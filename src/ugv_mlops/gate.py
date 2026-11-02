from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import text

from ugv_mlops.db import audit, connect
from ugv_mlops.settings import settings
from ugv_mlops.stages import assert_artifact_allowed, gate_thresholds


def ensure_signing_keys() -> None:
    key_path = Path(settings.signing_key_path)
    pub_path = Path(settings.signing_pub_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists() and pub_path.exists():
        return
    private = Ed25519PrivateKey.generate()
    key_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass


def sign_bytes(data: bytes) -> str:
    ensure_signing_keys()
    pem = Path(settings.signing_key_path).read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("signing key is not Ed25519")
    return key.sign(data).hex()


def verify_bytes(data: bytes, signature_hex: str) -> bool:
    pem = Path(settings.signing_pub_path).read_bytes()
    pub = serialization.load_pem_public_key(pem)
    if not isinstance(pub, Ed25519PublicKey):
        raise TypeError("public key is not Ed25519")
    try:
        pub.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


AUTOE2E_TASKS = {"autoe2e", "auto_e2e", "autoe2e_finetune"}


def _autoe2e_reasons(metrics: dict, thresholds: dict) -> list[str]:
    """軌道予測（ADE/FDE, 6.4 s）のゲート。絶対値より「親と定速度に勝てているか」を既定にする。"""
    reasons: list[str] = []
    ade = metrics.get("ade_6p4s_m")
    if ade is None:
        return ["ade_6p4s_m がありません"]
    ade = float(ade)
    if thresholds.get("require_improved_over_parent", True):
        if "improved_over_parent" not in metrics:
            reasons.append("親モデルとの比較がありません（--skip-baseline の学習はゲート不可）")
        elif not metrics["improved_over_parent"]:
            reasons.append(
                f"ADE {ade:.3f} m が親 {float(metrics.get('parent_ade_6p4s_m', 0.0)):.3f} m を下回っていません"
            )
    ratio = thresholds.get("max_ade_vs_cv_ratio")
    cv = metrics.get("cv_ade_6p4s_m")
    if ratio is not None and cv:
        limit = float(ratio) * float(cv)
        if ade > limit:
            reasons.append(f"ADE {ade:.3f} m > 定速度ベースライン {float(cv):.3f} m x {ratio} = {limit:.3f} m")
    max_ade = thresholds.get("max_ade_6p4s_m")
    if max_ade is not None and ade > float(max_ade):
        reasons.append(f"ade_6p4s_m {ade:.3f} > {max_ade}")
    max_fde = thresholds.get("max_fde_6p4s_m")
    if max_fde is not None and float(metrics.get("fde_6p4s_m", 99.0)) > float(max_fde):
        reasons.append(f"fde_6p4s_m {float(metrics.get('fde_6p4s_m', 99.0)):.3f} > {max_fde}")
    return reasons


def evaluate_and_promote(
    run_id: str,
    gate_name: str = "staging",
    target_status: str = "staging",
    mission_mode: str = "training",
    decided_by: str = "operator",
) -> dict:
    with connect() as conn:
        run = conn.execute(
            text("SELECT * FROM training_run WHERE run_id = :id"),
            {"id": run_id},
        ).mappings().one()
        metrics = run["metrics_json"] or {}
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        stage = run["stage"]
        task = run["task"]
        if task in {"detection", "yolox", "yolox_nano"} or task in AUTOE2E_TASKS:
            kind = "full_weights"
        elif task in {"e2e", "e2e_sequence", "end_to_end"}:
            kind = "joblib"
        else:
            kind = "joblib"
        size_bytes = 0
        try:
            from ugv_mlops.storage import get_bytes, parse_s3_uri

            if run.get("artifact_uri"):
                bkt, key = parse_s3_uri(run["artifact_uri"])
                size_bytes = len(get_bytes(bkt, key))
        except Exception as exc:
            reasons_pre = [f"artifact fetch failed: {exc}"]
        else:
            reasons_pre = []
        thresholds = gate_thresholds(gate_name)
        reasons: list[str] = list(reasons_pre)

        try:
            assert_artifact_allowed(stage, kind, size_bytes)
        except PermissionError as exc:
            reasons.append(str(exc))

        if task in AUTOE2E_TASKS:
            reasons.extend(_autoe2e_reasons(metrics, thresholds))
        elif "map50" in metrics:
            min_map = float(thresholds.get("min_map50", 0.05))
            if float(metrics.get("map50", 0.0)) < min_map:
                reasons.append(f"map50 {metrics['map50']:.3f} < {min_map}")
        elif "mae_steer" in metrics:
            max_steer = float(thresholds.get("max_mae_steer", 0.5))
            max_speed = float(thresholds.get("max_mae_speed", 2.0))
            if float(metrics.get("mae_steer", 99)) > max_steer:
                reasons.append(f"mae_steer {metrics['mae_steer']:.3f} > {max_steer}")
            if float(metrics.get("mae_speed", 99)) > max_speed:
                reasons.append(f"mae_speed {metrics['mae_speed']:.3f} > {max_speed}")
        else:
            min_acc = float(thresholds.get("min_accuracy", 0.0))
            max_fpr = float(thresholds.get("max_false_positive_rate", 1.0))
            acc = float(metrics.get("accuracy", 0.0))
            fpr = float(metrics.get("false_positive_rate", 1.0))
            if acc < min_acc:
                reasons.append(f"accuracy {acc:.3f} < {min_acc}")
            if fpr > max_fpr:
                reasons.append(f"false_positive_rate {fpr:.3f} > {max_fpr}")
        allowed_modes = thresholds.get("allowed_mission_modes") or []
        if allowed_modes and mission_mode not in allowed_modes:
            reasons.append(f"mission_mode {mission_mode} not in {allowed_modes}")
        if thresholds.get("require_holdout") and int(metrics.get("n_test", 0)) == 0:
            reasons.append("holdout split is empty")

        payload = json.dumps(
            {"run_id": run_id, "metrics": metrics, "stage": stage, "artifact_uri": run["artifact_uri"]},
            sort_keys=True,
        ).encode()
        signature = None
        if thresholds.get("require_signature") or True:
            signature = sign_bytes(payload)
            if thresholds.get("require_integrity") and not verify_bytes(payload, signature):
                reasons.append("integrity verification failed")

        passed = not reasons
        model_id = f"mdl_{run_id[:12]}"
        status = target_status if passed else "rejected"
        model_name = (
            "ugv-yolox"
            if task in {"detection", "yolox", "yolox_nano"}
            else "ugv-autoe2e"
            if task in AUTOE2E_TASKS
            else "ugv-e2e"
            if task in {"e2e", "e2e_sequence", "end_to_end"}
            else "ugv-obstacle-cls"
        )
        conn.execute(
            text(
                """
                INSERT INTO model_version (
                    model_id, name, version, run_id, stage, status, artifact_kind,
                    artifact_uri, metrics_json, signature_b64
                ) VALUES (
                    :mid, :name, :ver, :rid, :stage, :status, :kind,
                    :uri, CAST(:metrics AS jsonb), :sig
                )
                ON CONFLICT (model_id) DO UPDATE
                  SET status = EXCLUDED.status, signature_b64 = EXCLUDED.signature_b64
                """
            ),
            {
                "mid": model_id,
                "name": model_name,
                "ver": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                "rid": run_id,
                "stage": stage,
                "status": status,
                "kind": kind,
                "uri": run["artifact_uri"],
                "metrics": json.dumps(metrics),
                "sig": signature,
            },
        )
        gate_json = {
            "gate": gate_name,
            "passed": passed,
            "reasons": reasons,
            "mission_mode": mission_mode,
            "thresholds": thresholds,
        }
        conn.execute(
            text(
                """
                INSERT INTO promotion_event (model_id, from_status, to_status, gate_json, decided_by)
                VALUES (:mid, 'none', :to, CAST(:gate AS jsonb), :who)
                """
            ),
            {"mid": model_id, "to": status, "gate": json.dumps(gate_json), "who": decided_by},
        )
        audit(conn, "gate.evaluated", "model_version", model_id, gate_json)

    return {
        "model_id": model_id,
        "passed": passed,
        "status": status,
        "reasons": reasons,
        "signature": signature,
        "metrics": metrics,
    }

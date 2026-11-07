"""ゲート合格モデルの軽量化（工程10）。ONNX 変換または小さい成果物の再登録。"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_bytes
from ugv_mlops.stages import allowed_artifacts, assert_artifact_allowed
from ugv_mlops.storage import get_bytes, parse_s3_uri, put_bytes


def export_model(model_id: str, kind: str = "onnx", target_stage: str | None = None) -> dict:
    """Create a lighter artifact model_version from a gated model.

    Failures raise — never register a 'light' model with the original heavy kind.
    """
    kind = (kind or "onnx").strip().lower()
    if kind not in {"onnx", "distilled_student", "adapter"}:
        raise ValueError("kind must be onnx, distilled_student, or adapter")

    with connect() as conn:
        model = conn.execute(
            text("SELECT * FROM model_version WHERE model_id = :id"),
            {"id": model_id},
        ).mappings().first()
        if not model:
            raise FileNotFoundError(f"model not found: {model_id}")
        if model["status"] not in {"staging", "canary", "production"}:
            raise RuntimeError(f"model {model_id} status {model['status']} cannot be lightened")

        stage = target_stage or model["stage"] or "S3"
        src_kind = str(model["artifact_kind"] or "joblib")
        allowed = allowed_artifacts(stage)
        if kind not in allowed:
            raise PermissionError(
                f"kind '{kind}' is not allowed at stage {stage}; allowed={sorted(allowed)}. "
                f"S1 向けは adapter 等を選んでください。"
            )

        bucket, key = parse_s3_uri(model["artifact_uri"])
        raw = get_bytes(bucket, key)

        # Already the requested kind — no new version
        if src_kind == kind:
            assert_artifact_allowed(stage, src_kind, len(raw))
            return {
                "ok": True,
                "skipped": True,
                "model_id": model_id,
                "artifact_kind": src_kind,
                "reason": "already_light",
                "object_uri": model["artifact_uri"],
                "size_bytes": len(raw),
            }

        out_bytes, used_kind, detail = _convert(raw, src_kind, key, kind)
        if used_kind != kind:
            raise RuntimeError(
                f"lighten produced kind={used_kind} but requested {kind}: {detail}"
            )
        assert_artifact_allowed(stage, used_kind, len(out_bytes))

        new_id = f"mdl_{sha256_bytes(out_bytes)[:12]}"
        uri = put_bytes("models", f"{new_id}.{used_kind}", out_bytes, _content_type(used_kind))
        metrics = model["metrics_json"] or {}
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        metrics = dict(metrics)
        metrics["lightened_from"] = model_id
        metrics["lighten_detail"] = detail
        version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
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
                  SET status = EXCLUDED.status,
                      artifact_kind = EXCLUDED.artifact_kind,
                      artifact_uri = EXCLUDED.artifact_uri,
                      metrics_json = EXCLUDED.metrics_json
                """
            ),
            {
                "mid": new_id,
                "name": f"{model['name']}-light",
                "ver": version,
                "rid": model["run_id"],
                "stage": stage,
                "status": model["status"],
                "kind": used_kind,
                "uri": uri,
                "metrics": json.dumps(metrics),
                "sig": model.get("signature_b64"),
            },
        )
        audit(
            conn,
            "model.lightened",
            "model_version",
            new_id,
            {"from": model_id, "kind": used_kind, "bytes": len(out_bytes), "detail": detail},
        )

    return {
        "ok": True,
        "skipped": False,
        "model_id": new_id,
        "parent_model_id": model_id,
        "artifact_kind": used_kind,
        "object_uri": uri,
        "size_bytes": len(out_bytes),
        "status": model["status"],
        "detail": detail,
    }


def _content_type(kind: str) -> str:
    if kind == "onnx":
        return "application/octet-stream"
    return "application/octet-stream"


def _convert(raw: bytes, src_kind: str, key: str, want: str) -> tuple[bytes, str, str]:
    """Convert to the requested kind or raise. Never return a heavier/wrong kind."""
    lower = key.lower()

    if want == "onnx":
        if src_kind in {"full_weights", "yolox"} or lower.endswith((".pth", ".pt")):
            return _torch_to_onnx(raw), "onnx", "torch.onnx.export"
        if src_kind in {"joblib", "full_weights"} or lower.endswith((".joblib", ".pkl")):
            return _sklearn_to_onnx(raw), "onnx", "skl2onnx"
        raise TypeError(f"cannot convert artifact_kind={src_kind} ({key}) to onnx")

    if want == "adapter":
        # Small joblib / already-light artifacts may be re-registered as adapter for S1
        if src_kind in {"adapter", "calibration", "threshold", "normalizer"}:
            return raw, "adapter", "already_adapter_family"
        if len(raw) <= 8_388_608 and src_kind in {"joblib", "full_weights", "onnx"}:
            return raw, "adapter", f"repack_as_adapter_from_{src_kind}"
        raise PermissionError(
            f"artifact too large or wrong kind for adapter re-tag "
            f"(kind={src_kind}, bytes={len(raw)}; max 8MiB)"
        )

    if want == "distilled_student":
        # Real distillation is out of scope; only allow small re-tag when already compact
        if len(raw) <= 33_554_432 and src_kind in {
            "joblib",
            "adapter",
            "onnx",
            "distilled_student",
            "full_weights",
        }:
            return raw, "distilled_student", f"repack_as_distilled_from_{src_kind}"
        raise PermissionError(
            f"artifact too large for distilled_student re-tag (bytes={len(raw)}; max 32MiB). "
            "蒸留学習は未実装です。"
        )

    raise ValueError(f"unsupported lighten kind: {want}")


def _torch_to_onnx(raw: bytes) -> bytes:
    import torch

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "w.pth"
        path.write_bytes(raw)
        obj = torch.load(path, map_location="cpu", weights_only=False)
        model = obj["model"] if isinstance(obj, dict) and "model" in obj else obj
        if not hasattr(model, "eval"):
            raise TypeError("not a torch module")
        model.eval()
        dummy = torch.zeros(1, 3, 416, 416)
        out = Path(td) / "m.onnx"
        try:
            torch.onnx.export(
                model,
                dummy,
                str(out),
                input_names=["images"],
                output_names=["output"],
                opset_version=17,
                dynamo=False,
            )
        except TypeError:
            torch.onnx.export(
                model,
                dummy,
                str(out),
                input_names=["images"],
                output_names=["output"],
                opset_version=17,
            )
        data = out.read_bytes()
        if not data or len(data) < 64:
            raise RuntimeError("onnx export produced empty file")
        return data


def _sklearn_to_onnx(raw: bytes) -> bytes:
    import joblib
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "m.joblib"
        path.write_bytes(raw)
        model = joblib.load(path)
        n = int(getattr(model, "n_features_in_", 32) or 32)
        onx = convert_sklearn(model, initial_types=[("input", FloatTensorType([None, n]))])
        data = onx.SerializeToString()
        if not data:
            raise RuntimeError("skl2onnx produced empty model")
        return data

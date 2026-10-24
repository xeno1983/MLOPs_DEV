from __future__ import annotations

import json
import zipfile
from io import BytesIO

from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.gate import sign_bytes
from ugv_mlops.hashing import sha256_bytes
from ugv_mlops.stages import assert_artifact_allowed, max_bytes
from ugv_mlops.storage import parse_s3_uri


def build_update_package(model_id: str, target_stage: str = "S1") -> dict:
    """Create a signed, size-limited update package for intermittent delivery to a UGV."""
    with connect() as conn:
        model = conn.execute(
            text("SELECT * FROM model_version WHERE model_id = :id"),
            {"id": model_id},
        ).mappings().one()
        if model["status"] not in {"staging", "canary", "production"}:
            raise RuntimeError(f"model {model_id} status {model['status']} cannot be packaged")

        bucket, key = parse_s3_uri(model["artifact_uri"])
        from ugv_mlops.storage import client

        data = client().get_object(bucket, key).read()
        kind = model["artifact_kind"]
        assert_artifact_allowed(target_stage, kind, len(data))

        manifest = {
            "package_schema": "ugv-update-v1",
            "model_id": model_id,
            "name": model["name"],
            "version": model["version"],
            "source_stage": model["stage"],
            "target_stage": target_stage,
            "artifact_kind": kind,
            "artifact_sha256": sha256_bytes(data),
            "rollback_to": None,
            "metrics": model["metrics_json"],
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
        signature = sign_bytes(manifest_bytes)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_bytes)
            zf.writestr("manifest.sig", signature)
            zf.writestr(f"artifact.{kind}", data)
        payload = buf.getvalue()
        if max_bytes(target_stage) and len(payload) > max_bytes(target_stage):
            raise PermissionError(
                f"package {len(payload)} bytes exceeds {target_stage} limit {max_bytes(target_stage)}"
            )
        package_id = f"pkg_{sha256_bytes(payload)[:16]}"
        uri = storage.put_bytes("packages", f"{package_id}.zip", payload, "application/zip")
        conn.execute(
            text(
                """
                INSERT INTO update_package (
                    package_id, model_id, target_stage, object_uri, sha256, size_bytes,
                    manifest_json, signature_b64
                ) VALUES (
                    :pid, :mid, :stage, :uri, :sha, :sz, CAST(:man AS jsonb), :sig
                )
                """
            ),
            {
                "pid": package_id,
                "mid": model_id,
                "stage": target_stage,
                "uri": uri,
                "sha": sha256_bytes(payload),
                "sz": len(payload),
                "man": manifest_bytes.decode(),
                "sig": signature,
            },
        )
        audit(conn, "package.built", "update_package", package_id, {"target_stage": target_stage, "bytes": len(payload)})

    return {
        "package_id": package_id,
        "object_uri": uri,
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
        "target_stage": target_stage,
        "signature": signature,
    }

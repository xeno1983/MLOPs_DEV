"""パッケージのチャンク配送（工程11）。Garage の zip を車両 inbox へ実書き込みする。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from ugv_mlops.db import audit, connect
from ugv_mlops.settings import settings
from ugv_mlops.storage import get_bytes, parse_s3_uri


def _staging_dir(tx_id: str) -> Path:
    d = settings.work_path / "sync_staging" / tx_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _vehicle_inbox(vehicle_id: str) -> Path:
    safe = "".join(c for c in vehicle_id if c.isalnum() or c in "-_") or "UGV"
    d = settings.work_path / "vehicles" / safe / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def enqueue_sync(package_id: str, vehicle_id: str) -> dict:
    """Fetch package bytes from Garage into local staging, then open a sync transaction."""
    tx_id = f"tx_{package_id}_{vehicle_id}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    with connect() as conn:
        pkg = conn.execute(
            text("SELECT package_id, object_uri, size_bytes, sha256 FROM update_package WHERE package_id = :id"),
            {"id": package_id},
        ).mappings().first()
        if not pkg:
            raise FileNotFoundError(f"package not found: {package_id}")
        if not (pkg.get("object_uri") or "").strip():
            raise RuntimeError(f"package {package_id} has no object_uri")
        expected_sha = (pkg.get("sha256") or "").strip()
        if not expected_sha:
            raise RuntimeError(f"package {package_id} has no sha256 (refuse sync)")
        bucket, key = parse_s3_uri(pkg["object_uri"])
        data = get_bytes(bucket, key)
        expected = int(pkg["size_bytes"] or 0)
        if expected <= 0:
            raise RuntimeError(f"package {package_id} size_bytes missing or 0")
        if len(data) != expected:
            raise RuntimeError(f"package size mismatch: storage={len(data)} db={expected}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha:
            raise RuntimeError(f"package sha256 mismatch: got={digest} expected={expected_sha}")

        stage = _staging_dir(tx_id)
        src = stage / "package.zip"
        src.write_bytes(data)
        dest = _vehicle_inbox(vehicle_id) / f"{package_id}.zip.partial"
        if dest.exists():
            dest.unlink()
        dest.write_bytes(b"")  # create empty partial; chunks append

        conn.execute(
            text(
                """
                INSERT INTO sync_transaction (tx_id, package_id, vehicle_id, status)
                VALUES (:tx, :pkg, :vid, 'pending')
                """
            ),
            {"tx": tx_id, "pkg": package_id, "vid": vehicle_id},
        )
        audit(
            conn,
            "sync.enqueued",
            "sync_transaction",
            tx_id,
            {
                "vehicle_id": vehicle_id,
                "bytes": len(data),
                "staging": str(src),
                "dest_partial": str(dest),
            },
        )
    return {
        "tx_id": tx_id,
        "package_id": package_id,
        "vehicle_id": vehicle_id,
        "size_bytes": len(data),
        "delivery_dir": str(_vehicle_inbox(vehicle_id)),
    }


def send_chunk(tx_id: str, max_bytes: int = 65536) -> dict:
    """Copy the next chunk from staging into the vehicle inbox partial file."""
    with connect() as conn:
        tx = conn.execute(
            text("SELECT * FROM sync_transaction WHERE tx_id = :id"),
            {"id": tx_id},
        ).mappings().one()
        pkg = conn.execute(
            text("SELECT package_id, size_bytes, sha256 FROM update_package WHERE package_id = :id"),
            {"id": tx["package_id"]},
        ).mappings().one()
        total = int(pkg["size_bytes"] or 0)
        offset = int(tx["resume_offset"] or 0)
        if tx["status"] in {"acked", "rolled_back"}:
            return {"tx_id": tx_id, "status": tx["status"], "done": True, "bytes_sent": offset, "size_bytes": total}

        src = _staging_dir(tx_id) / "package.zip"
        if not src.is_file():
            raise FileNotFoundError(f"staging package missing for {tx_id}: {src}")
        raw = src.read_bytes()
        if total <= 0:
            total = len(raw)
        dest = _vehicle_inbox(tx["vehicle_id"]) / f"{pkg['package_id']}.zip.partial"

        chunk_len = min(max_bytes, max(total - offset, 0))
        if chunk_len > 0:
            piece = raw[offset : offset + chunk_len]
            with dest.open("ab") as fh:
                fh.write(piece)
        new_offset = offset + chunk_len
        status = "acked" if new_offset >= total else "partial"
        delivery_path = None
        if status == "acked":
            final = _vehicle_inbox(tx["vehicle_id"]) / f"{pkg['package_id']}.zip"
            if final.exists():
                final.unlink()
            dest.replace(final)
            expected_sha = (pkg.get("sha256") or "").strip()
            got = hashlib.sha256(final.read_bytes()).hexdigest()
            if not expected_sha or got != expected_sha:
                conn.execute(
                    text(
                        """
                        UPDATE sync_transaction
                        SET status = 'rolled_back', bytes_sent = :sent, resume_offset = :off, updated_at = now()
                        WHERE tx_id = :id
                        """
                    ),
                    {"sent": new_offset, "off": new_offset, "id": tx_id},
                )
                audit(
                    conn,
                    "sync.integrity_failed",
                    "sync_transaction",
                    tx_id,
                    {"got": got, "expected": expected_sha or None},
                )
                raise RuntimeError(
                    f"delivery integrity failed: got={got} expected={expected_sha or '(missing)'}"
                )
            delivery_path = str(final)
            # cleanup staging
            try:
                src.unlink(missing_ok=True)
            except OSError:
                pass

        conn.execute(
            text(
                """
                UPDATE sync_transaction
                SET status = :st, bytes_sent = :sent, resume_offset = :off, updated_at = now()
                WHERE tx_id = :id
                """
            ),
            {"st": status, "sent": new_offset, "off": new_offset, "id": tx_id},
        )
        audit(
            conn,
            "sync.chunk",
            "sync_transaction",
            tx_id,
            {"offset": new_offset, "status": status, "chunk": chunk_len, "delivery_path": delivery_path},
        )
        return {
            "tx_id": tx_id,
            "status": status,
            "bytes_sent": new_offset,
            "size_bytes": total,
            "done": status == "acked",
            "delivery_path": delivery_path,
            "delivery_dir": str(_vehicle_inbox(tx["vehicle_id"])),
        }


def rollback(tx_id: str) -> dict:
    with connect() as conn:
        tx = conn.execute(
            text("SELECT * FROM sync_transaction WHERE tx_id = :id"),
            {"id": tx_id},
        ).mappings().first()
        conn.execute(
            text(
                """
                UPDATE sync_transaction
                SET status = 'rolled_back', updated_at = now()
                WHERE tx_id = :id
                """
            ),
            {"id": tx_id},
        )
        if tx:
            for path in (
                _vehicle_inbox(tx["vehicle_id"]) / f"{tx['package_id']}.zip.partial",
                _staging_dir(tx_id) / "package.zip",
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        audit(conn, "sync.rollback", "sync_transaction", tx_id, {})
    return {"tx_id": tx_id, "status": "rolled_back"}

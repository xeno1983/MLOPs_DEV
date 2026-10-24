from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from ugv_mlops.db import audit, connect


def enqueue_sync(package_id: str, vehicle_id: str) -> dict:
    tx_id = f"tx_{package_id}_{vehicle_id}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    with connect() as conn:
        pkg = conn.execute(
            text("SELECT size_bytes FROM update_package WHERE package_id = :id"),
            {"id": package_id},
        ).mappings().one()
        conn.execute(
            text(
                """
                INSERT INTO sync_transaction (tx_id, package_id, vehicle_id, status)
                VALUES (:tx, :pkg, :vid, 'pending')
                """
            ),
            {"tx": tx_id, "pkg": package_id, "vid": vehicle_id},
        )
        audit(conn, "sync.enqueued", "sync_transaction", tx_id, {"vehicle_id": vehicle_id})
    return {"tx_id": tx_id, "package_id": package_id, "vehicle_id": vehicle_id, "size_bytes": pkg["size_bytes"]}


def send_chunk(tx_id: str, max_bytes: int = 65536) -> dict:
    """Simulate D-DIL delivery: send one chunk, persist resume offset, ack when complete."""
    with connect() as conn:
        tx = conn.execute(
            text("SELECT * FROM sync_transaction WHERE tx_id = :id"),
            {"id": tx_id},
        ).mappings().one()
        pkg = conn.execute(
            text("SELECT size_bytes FROM update_package WHERE package_id = :id"),
            {"id": tx["package_id"]},
        ).mappings().one()
        total = int(pkg["size_bytes"] or 0)
        offset = int(tx["resume_offset"] or 0)
        if tx["status"] in {"acked", "rolled_back"}:
            return {"tx_id": tx_id, "status": tx["status"], "done": True}
        chunk = min(max_bytes, max(total - offset, 0))
        new_offset = offset + chunk
        status = "acked" if new_offset >= total else "partial"
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
        audit(conn, "sync.chunk", "sync_transaction", tx_id, {"offset": new_offset, "status": status})
        return {
            "tx_id": tx_id,
            "status": status,
            "bytes_sent": new_offset,
            "size_bytes": total,
            "done": status == "acked",
        }


def rollback(tx_id: str) -> dict:
    with connect() as conn:
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
        audit(conn, "sync.rollback", "sync_transaction", tx_id, {})
    return {"tx_id": tx_id, "status": "rolled_back"}

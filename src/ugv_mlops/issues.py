from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text

from ugv_mlops.db import audit, connect, ensure_schema
from ugv_mlops.settings import settings

OPEN_STATUSES = ("open", "in_progress")
CLOSED_STATUSES = ("resolved", "wont_fix", "duplicate")
ALL_STATUSES = OPEN_STATUSES + CLOSED_STATUSES
SEVERITIES = ("low", "medium", "high", "critical")
CATEGORIES = (
    "false_positive",
    "false_negative",
    "drift",
    "latency",
    "safety",
    "packaging",
    "sync",
    "other",
)


def list_deployed_models() -> list[dict]:
    """Models that passed a gate and/or were packaged for a vehicle."""
    ensure_schema()
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    m.model_id, m.name, m.version, m.stage, m.status, m.artifact_kind,
                    m.metrics_json, m.created_at, m.run_id,
                    p.package_id, p.target_stage,
                    s.vehicle_id AS last_vehicle, s.status AS last_sync, s.updated_at AS last_sync_at,
                    (SELECT count(*) FROM model_issue i
                      WHERE i.model_id = m.model_id AND i.status IN ('open', 'in_progress')) AS open_issues
                FROM model_version m
                LEFT JOIN LATERAL (
                    SELECT package_id, target_stage, created_at
                    FROM update_package
                    WHERE model_id = m.model_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) p ON TRUE
                LEFT JOIN LATERAL (
                    SELECT st.vehicle_id, st.status, st.updated_at
                    FROM sync_transaction st
                    JOIN update_package up ON up.package_id = st.package_id
                    WHERE up.model_id = m.model_id
                    ORDER BY st.updated_at DESC
                    LIMIT 1
                ) s ON TRUE
                WHERE m.status IN ('staging', 'canary', 'production')
                   OR p.package_id IS NOT NULL
                ORDER BY m.created_at DESC
                """
            )
        ).mappings().all()
    return [
        {**dict(r), "open_issues": int(r["open_issues"] or 0)}
        for r in rows
    ]


def list_issues(
    status: str | None = None,
    model_id: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> dict:
    ensure_schema()
    clauses = ["TRUE"]
    params: dict = {"n": limit}
    if status:
        clauses.append("i.status = :status")
        params["status"] = status
    if model_id:
        clauses.append("i.model_id = :mid")
        params["mid"] = model_id
    if severity:
        clauses.append("i.severity = :sev")
        params["sev"] = severity
    where = " AND ".join(clauses)
    with connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT i.*, m.name AS model_name, m.stage AS model_stage, m.status AS model_status
                FROM model_issue i
                JOIN model_version m ON m.model_id = i.model_id
                WHERE {where}
                ORDER BY
                  CASE i.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                  CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                  i.created_at DESC
                LIMIT :n
                """
            ),
            params,
        ).mappings().all()
        counts = conn.execute(
            text("SELECT status, count(*) AS n FROM model_issue GROUP BY status")
        ).mappings().all()
    by_status = {r["status"]: int(r["n"]) for r in counts}
    open_n = int(by_status.get("open", 0) + by_status.get("in_progress", 0))
    return {
        "issues": [dict(r) for r in rows],
        "counts": {
            "open": int(by_status.get("open", 0)),
            "in_progress": int(by_status.get("in_progress", 0)),
            "resolved": int(by_status.get("resolved", 0)),
            "wont_fix": int(by_status.get("wont_fix", 0)),
            "duplicate": int(by_status.get("duplicate", 0)),
            "active": open_n,
            "total": sum(by_status.values()),
        },
    }


def get_issue(issue_id: str) -> dict:
    ensure_schema()
    with connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT i.*, m.name AS model_name, m.stage AS model_stage, m.status AS model_status,
                       m.run_id AS model_run_id
                FROM model_issue i
                JOIN model_version m ON m.model_id = i.model_id
                WHERE i.issue_id = :id
                """
            ),
            {"id": issue_id},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "issue not found")
        events = conn.execute(
            text(
                """
                SELECT event_id, actor, action, from_status, to_status, note, detail_json, ts
                FROM model_issue_event
                WHERE issue_id = :id
                ORDER BY ts
                """
            ),
            {"id": issue_id},
        ).mappings().all()
    issue = dict(row)
    issue["events"] = [dict(e) for e in events]
    return issue


def create_issue(payload: dict) -> dict:
    ensure_schema()
    model_id = (payload.get("model_id") or "").strip()
    title = (payload.get("title") or "").strip()
    if not model_id or not title:
        raise HTTPException(400, "model_id と title は必須です")
    severity = payload.get("severity") or "medium"
    if severity not in SEVERITIES:
        raise HTTPException(400, f"severity must be one of {SEVERITIES}")
    category = payload.get("category") or "other"
    if category not in CATEGORIES:
        category = "other"
    issue_id = "iss_" + uuid.uuid4().hex[:12]
    opened_by = payload.get("opened_by") or settings.actor
    with connect() as conn:
        model = conn.execute(
            text("SELECT model_id, run_id, status FROM model_version WHERE model_id = :id"),
            {"id": model_id},
        ).mappings().first()
        if not model:
            raise HTTPException(404, f"model not found: {model_id}")
        package_id = payload.get("package_id") or _latest_package(conn, model_id)
        vehicle_id = payload.get("vehicle_id") or None
        if vehicle_id:
            conn.execute(
                text("INSERT INTO vehicle (vehicle_id, platform_type) VALUES (:v, 'ugv') ON CONFLICT DO NOTHING"),
                {"v": vehicle_id},
            )
        conn.execute(
            text(
                """
                INSERT INTO model_issue (
                    issue_id, model_id, package_id, vehicle_id, title, description,
                    category, severity, status, source, related_sample_id, related_run_id,
                    opened_by, assignee
                ) VALUES (
                    :iid, :mid, :pid, :vid, :title, :desc,
                    :cat, :sev, 'open', :src, :sid, :rid,
                    :who, :asg
                )
                """
            ),
            {
                "iid": issue_id,
                "mid": model_id,
                "pid": package_id,
                "vid": vehicle_id,
                "title": title,
                "desc": payload.get("description") or "",
                "cat": category,
                "sev": severity,
                "src": payload.get("source") or "operator",
                "sid": payload.get("related_sample_id"),
                "rid": payload.get("related_run_id") or model["run_id"],
                "who": opened_by,
                "asg": payload.get("assignee"),
            },
        )
        _add_event(conn, issue_id, opened_by, "opened", None, "open", payload.get("description") or title)
        audit(conn, "issue.opened", "model_issue", issue_id, {"model_id": model_id, "title": title})
    return get_issue(issue_id)


def update_issue(issue_id: str, payload: dict) -> dict:
    ensure_schema()
    actor = payload.get("actor") or settings.actor
    with connect() as conn:
        current = conn.execute(
            text("SELECT * FROM model_issue WHERE issue_id = :id"),
            {"id": issue_id},
        ).mappings().first()
        if not current:
            raise HTTPException(404, "issue not found")
        new_status = payload.get("status") or current["status"]
        if new_status not in ALL_STATUSES:
            raise HTTPException(400, f"status must be one of {ALL_STATUSES}")
        closed = new_status in CLOSED_STATUSES
        reopened = current["status"] in CLOSED_STATUSES and new_status in OPEN_STATUSES
        resolution = payload.get("resolution")
        if resolution is None:
            resolution = current["resolution"]
        if closed and new_status == "resolved" and not (resolution or payload.get("note")):
            raise HTTPException(400, "解決するには resolution または note が必要です")
        resolved_at = current["resolved_at"]
        if closed and current["status"] not in CLOSED_STATUSES:
            resolved_at = datetime.now(timezone.utc)
        if reopened:
            resolved_at = None
        conn.execute(
            text(
                """
                UPDATE model_issue
                SET status = :st,
                    assignee = COALESCE(:asg, assignee),
                    resolution = :res,
                    vehicle_id = COALESCE(:vid, vehicle_id),
                    package_id = COALESCE(:pid, package_id),
                    updated_at = now(),
                    resolved_at = :rat
                WHERE issue_id = :id
                """
            ),
            {
                "st": new_status,
                "asg": payload.get("assignee"),
                "res": resolution,
                "vid": payload.get("vehicle_id"),
                "pid": payload.get("package_id"),
                "rat": resolved_at,
                "id": issue_id,
            },
        )
        action = "commented"
        if new_status != current["status"]:
            action = "reopened" if reopened else "status_changed"
            if new_status == "resolved":
                action = "resolved"
            elif new_status == "wont_fix":
                action = "wont_fix"
            elif new_status == "in_progress":
                action = "started"
        note = payload.get("note") or resolution or ""
        _add_event(conn, issue_id, actor, action, current["status"], new_status, note)
        audit(conn, f"issue.{action}", "model_issue", issue_id, {"from": current["status"], "to": new_status})
    return get_issue(issue_id)


def add_comment(issue_id: str, note: str, actor: str | None = None) -> dict:
    return update_issue(issue_id, {"note": note, "actor": actor or settings.actor})


def _latest_package(conn, model_id: str) -> str | None:
    return conn.execute(
        text(
            """
            SELECT package_id FROM update_package
            WHERE model_id = :id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"id": model_id},
    ).scalar_one_or_none()


def _add_event(conn, issue_id: str, actor: str, action: str, from_status: str | None, to_status: str | None, note: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO model_issue_event (issue_id, actor, action, from_status, to_status, note, detail_json)
            VALUES (:iid, :who, :act, :fr, :to, :note, CAST(:detail AS jsonb))
            """
        ),
        {
            "iid": issue_id,
            "who": actor,
            "act": action,
            "fr": from_status,
            "to": to_status,
            "note": note or "",
            "detail": json.dumps({"action": action}, ensure_ascii=False),
        },
    )

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from ugv_mlops.settings import settings

ISSUE_DDL = """
CREATE TABLE IF NOT EXISTS model_issue (
    issue_id            TEXT PRIMARY KEY,
    model_id            TEXT NOT NULL REFERENCES model_version (model_id),
    package_id          TEXT REFERENCES update_package (package_id),
    vehicle_id          TEXT REFERENCES vehicle (vehicle_id),
    title               TEXT NOT NULL,
    description         TEXT,
    category            TEXT NOT NULL DEFAULT 'other',
    severity            TEXT NOT NULL DEFAULT 'medium'
                        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'in_progress', 'resolved', 'wont_fix', 'duplicate')),
    source              TEXT NOT NULL DEFAULT 'operator',
    related_sample_id   TEXT,
    related_run_id      TEXT,
    resolution          TEXT,
    opened_by           TEXT,
    assignee            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS model_issue_event (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id        TEXT NOT NULL REFERENCES model_issue (issue_id) ON DELETE CASCADE,
    actor           TEXT,
    action          TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    note            TEXT,
    detail_json     JSONB,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_issue_model ON model_issue (model_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issue_status ON model_issue (status, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issue_event ON model_issue_event (issue_id, ts);
"""


def make_engine() -> Engine:
    return create_engine(settings.catalog_database_url, pool_pre_ping=True)


engine = make_engine()


@contextmanager
def connect() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn


def ensure_schema() -> None:
    """Apply additive schema on already-initialized volumes."""
    with engine.begin() as conn:
        for stmt in ISSUE_DDL.split(";"):
            s = stmt.strip()
            if s:
                conn.exec_driver_sql(s)


def audit(conn: Connection, action: str, object_type: str | None, object_id: str | None, detail: dict | None = None) -> None:
    conn.execute(
        text(
            """
            INSERT INTO audit_log (actor, action, object_type, object_id, detail_json)
            VALUES (:actor, :action, :object_type, :object_id, CAST(:detail AS jsonb))
            """
        ),
        {
            "actor": settings.actor,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "detail": _json(detail),
        },
    )


def _json(value: dict | None) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False)

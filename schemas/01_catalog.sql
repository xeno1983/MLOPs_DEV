-- UAIS Layer 3 学習DBを、オンプレ MLOps カタログとして拡張したもの。
-- 層0 エッジ / 層1 一次 / 層2 二次 からのサンプルと、データセット・学習・昇格・差分同期を管理する。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE vehicle (
    vehicle_id      TEXT PRIMARY KEY,
    platform_type   TEXT NOT NULL DEFAULT 'ugv',
    unit            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mission (
    mission_id      TEXT PRIMARY KEY,
    vehicle_id      TEXT REFERENCES vehicle (vehicle_id),
    classification  TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
    started_ts      TIMESTAMPTZ,
    ended_ts        TIMESTAMPTZ,
    theater         TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingest_session (
    session_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id      TEXT REFERENCES mission (mission_id),
    source_type     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed')),
    bytes_total     BIGINT DEFAULT 0,
    files_count     INT DEFAULT 0,
    started_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_ts        TIMESTAMPTZ,
    error           TEXT
);

CREATE TABLE learning_sample (
    sample_id       TEXT PRIMARY KEY,
    mission_id      TEXT REFERENCES mission (mission_id),
    vehicle_id      TEXT,
    ts              TIMESTAMPTZ,
    modality        TEXT NOT NULL,
    object_uri      TEXT NOT NULL,
    sha256          TEXT,
    width           INT,
    height          INT,
    classification  TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
    declassified    BOOLEAN NOT NULL DEFAULT FALSE,
    pii_scrubbed    BOOLEAN NOT NULL DEFAULT FALSE,
    quality_json    JSONB,
    meta_json       JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE annotation (
    annotation_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id       TEXT NOT NULL REFERENCES learning_sample (sample_id) ON DELETE CASCADE,
    label_type      TEXT NOT NULL,
    label_json      JSONB NOT NULL,
    annotator       TEXT,
    tool            TEXT,
    gold            BOOLEAN NOT NULL DEFAULT FALSE,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dataset (
    dataset_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    task            TEXT NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dataset_version (
    dataset_version_id  TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES dataset (dataset_id),
    version             INT NOT NULL,
    format              TEXT NOT NULL,
    object_uri          TEXT,
    sha256              TEXT,
    stats_json          JSONB,
    parent_version_id   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, version)
);

CREATE TABLE dataset_item (
    dataset_version_id  TEXT NOT NULL REFERENCES dataset_version (dataset_version_id) ON DELETE CASCADE,
    sample_id           TEXT NOT NULL REFERENCES learning_sample (sample_id),
    split               TEXT NOT NULL CHECK (split IN ('train', 'val', 'test')),
    PRIMARY KEY (dataset_version_id, sample_id)
);

CREATE TABLE training_run (
    run_id              TEXT PRIMARY KEY,
    dataset_version_id  TEXT REFERENCES dataset_version (dataset_version_id),
    task                TEXT NOT NULL,
    stage               TEXT NOT NULL,
    params_json         JSONB,
    metrics_json        JSONB,
    artifact_uri        TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_version (
    model_id                TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    version                 TEXT NOT NULL,
    run_id                  TEXT REFERENCES training_run (run_id),
    stage                   TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'staging'
                            CHECK (status IN ('staging', 'canary', 'production', 'retired', 'rejected')),
    artifact_kind           TEXT NOT NULL,
    artifact_uri            TEXT,
    metrics_json            JSONB,
    signature_b64           TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE promotion_event (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id        TEXT NOT NULL REFERENCES model_version (model_id),
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    gate_json       JSONB,
    decided_by      TEXT,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE update_package (
    package_id      TEXT PRIMARY KEY,
    model_id        TEXT REFERENCES model_version (model_id),
    target_stage    TEXT NOT NULL,
    object_uri      TEXT,
    sha256          TEXT,
    size_bytes      BIGINT,
    manifest_json   JSONB,
    signature_b64   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sync_transaction (
    tx_id           TEXT PRIMARY KEY,
    package_id      TEXT REFERENCES update_package (package_id),
    vehicle_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sending', 'partial', 'acked', 'rolled_back', 'failed')),
    bytes_sent      BIGINT NOT NULL DEFAULT 0,
    resume_offset   BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job (
    job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    result_json     JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE TABLE audit_log (
    audit_id        BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT,
    action          TEXT NOT NULL,
    object_type     TEXT,
    object_id       TEXT,
    detail_json     JSONB
);

CREATE INDEX idx_sample_mission ON learning_sample (mission_id, ts);
CREATE INDEX idx_sample_modality ON learning_sample (modality);
CREATE INDEX idx_annotation_sample ON annotation (sample_id);
CREATE INDEX idx_job_queued ON job (created_at) WHERE status = 'queued';
CREATE INDEX idx_sync_pending ON sync_transaction (status, created_at);

CREATE TABLE model_issue (
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

CREATE TABLE model_issue_event (
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

CREATE INDEX idx_issue_model ON model_issue (model_id, status, created_at DESC);
CREATE INDEX idx_issue_status ON model_issue (status, severity, created_at DESC);
CREATE INDEX idx_issue_event ON model_issue_event (issue_id, ts);

INSERT INTO vehicle (vehicle_id, platform_type, unit)
VALUES ('UGV-01', 'ugv', 'demo-unit')
ON CONFLICT DO NOTHING;

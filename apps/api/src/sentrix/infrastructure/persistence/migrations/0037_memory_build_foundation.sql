-- S0 / M1: 构建版本、字段证据、阶段续办。
-- 只加表与索引，不改旧列；旧代码读新库时这些表为空，按 legacy 读取。
CREATE TABLE IF NOT EXISTS observation_builds (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    observation_id TEXT REFERENCES observations(id),
    observation_revision INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    build_key TEXT NOT NULL,
    input_hash TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    protocol_version TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    input_transform_json TEXT NOT NULL DEFAULT '{}',
    raw_response_ref TEXT NOT NULL DEFAULT '',
    error_category TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(build_key)
);
CREATE INDEX IF NOT EXISTS idx_observation_builds_asset_stage
    ON observation_builds(asset_id, stage, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_observation_builds_scope_status
    ON observation_builds(scope_id, status);

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    field_path TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT 'null',
    value_hash TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL,
    source_modality TEXT NOT NULL DEFAULT '',
    source_channel TEXT NOT NULL DEFAULT '',
    locator_json TEXT NOT NULL DEFAULT '{}',
    observation_revision INTEGER NOT NULL DEFAULT 0,
    build_id TEXT REFERENCES observation_builds(id),
    correction_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'effective',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_owner
    ON memory_evidence(scope_id, owner_kind, owner_id, field_path);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_build ON memory_evidence(build_id);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_status ON memory_evidence(status);

CREATE TABLE IF NOT EXISTS memory_build_tasks (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    stage TEXT NOT NULL,
    build_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(build_key, stage)
);
CREATE INDEX IF NOT EXISTS idx_memory_build_tasks_due
    ON memory_build_tasks(status, next_attempt_at);

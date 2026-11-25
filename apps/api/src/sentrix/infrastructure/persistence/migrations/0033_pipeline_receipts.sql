CREATE TABLE pipeline_receipts (
    operation_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    asset_ids_json TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_pipeline_receipts_scope ON pipeline_receipts(scope_id, started_at);

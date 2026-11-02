-- 持久导入作业：接收与离线处理分开记账。
-- “已接收”只在原件落盘且本表事务提交之后成立。

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    receive_seq INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    fence INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    error_category TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_items INTEGER NOT NULL DEFAULT 0,
    failed_items INTEGER NOT NULL DEFAULT 0,
    original_path TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_import_jobs_idempotency
    ON import_jobs(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_import_jobs_receive_seq
    ON import_jobs(scope_id, receive_seq);
CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_import_jobs_scope ON import_jobs(scope_id, receive_seq);

CREATE TABLE IF NOT EXISTS import_job_stages (
    job_id TEXT NOT NULL REFERENCES import_jobs(id),
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    error_category TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, stage)
);

CREATE TABLE IF NOT EXISTS upload_reservations (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    expected_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

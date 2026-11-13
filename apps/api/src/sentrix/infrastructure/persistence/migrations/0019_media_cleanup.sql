-- Transactional outbox: SQLite deletion commits with the cleanup work that follows it.
CREATE TABLE media_cleanup_jobs (
    asset_id TEXT PRIMARY KEY,
    asset_ids_json TEXT NOT NULL,
    paths_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    files_done INTEGER NOT NULL DEFAULT 0,
    vectors_done INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

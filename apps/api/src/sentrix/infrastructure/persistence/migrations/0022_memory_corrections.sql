CREATE TABLE memory_corrections (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_memory_corrections_scope ON memory_corrections(scope_id, created_at);
ALTER TABLE entities ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;

-- The correction row doubles as the durable job for a committed person name edit.
ALTER TABLE memory_corrections ADD COLUMN sync_mode TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE memory_corrections ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_corrections ADD COLUMN next_attempt_at TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_memory_corrections_due ON memory_corrections(sync_mode, status, next_attempt_at, scope_id);

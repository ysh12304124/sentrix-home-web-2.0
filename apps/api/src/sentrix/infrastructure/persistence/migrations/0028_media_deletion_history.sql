-- Keep a readable audit record alongside the durable external-cleanup outbox.
ALTER TABLE media_cleanup_jobs ADD COLUMN scope_id TEXT NOT NULL DEFAULT '';
ALTER TABLE media_cleanup_jobs ADD COLUMN file_name TEXT NOT NULL DEFAULT '';
ALTER TABLE media_cleanup_jobs ADD COLUMN media_type TEXT NOT NULL DEFAULT '';
ALTER TABLE media_cleanup_jobs ADD COLUMN deleted_at TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_media_cleanup_jobs_scope_deleted
ON media_cleanup_jobs(scope_id, deleted_at DESC);

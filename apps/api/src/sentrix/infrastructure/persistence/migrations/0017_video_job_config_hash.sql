-- Persist the model/pipeline configuration fingerprint used by a durable video job.
ALTER TABLE video_jobs ADD COLUMN config_hash TEXT NOT NULL DEFAULT '';

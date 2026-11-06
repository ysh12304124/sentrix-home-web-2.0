-- Durable video processing jobs and per-stage checkpoints.
CREATE TABLE video_jobs (
    job_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
    video_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    current_stage TEXT NOT NULL DEFAULT 'UPLOAD',
    overall_progress REAL NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    heartbeat_at TEXT,
    error_code TEXT,
    error_message TEXT,
    pipeline_version TEXT NOT NULL DEFAULT 'video-v1',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_video_jobs_status ON video_jobs(status, updated_at);
CREATE TABLE video_job_stages (
    job_id TEXT NOT NULL REFERENCES video_jobs(job_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    progress REAL NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    input_hash TEXT NOT NULL DEFAULT '',
    output_artifact TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY(job_id, stage)
);

CREATE TABLE video_memory_records (
    video_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (video_id, record_id)
);

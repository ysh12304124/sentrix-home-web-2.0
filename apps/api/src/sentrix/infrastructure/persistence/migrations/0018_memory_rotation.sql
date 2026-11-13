-- 回忆首页：每个用户内按 scope + 自然日保存稳定序列、游标与已看记录。

CREATE TABLE IF NOT EXISTS memory_rotation_sequences (
    sequence_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    day TEXT NOT NULL,
    event_ids_json TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_rotation_scope_day
    ON memory_rotation_sequences(scope_id, day);

CREATE TABLE IF NOT EXISTS memory_rotation_viewed (
    sequence_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    viewed_at TEXT NOT NULL,
    PRIMARY KEY (sequence_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_rotation_viewed_sequence
    ON memory_rotation_viewed(sequence_id, viewed_at);

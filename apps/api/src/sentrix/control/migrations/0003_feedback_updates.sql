-- 反馈的补充信息、幂等内容签名与附件元数据。

ALTER TABLE feedback ADD COLUMN content_signature TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS feedback_updates (
    id TEXT PRIMARY KEY,
    feedback_id TEXT NOT NULL REFERENCES feedback(id),
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_updates_feedback
    ON feedback_updates(feedback_id, created_at);

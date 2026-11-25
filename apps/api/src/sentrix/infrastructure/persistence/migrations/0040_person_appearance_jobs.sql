-- 确认 / 合并之后的外观取样任务（每个事件一次视觉模型调用，约 10 秒）。
-- 一个人一行：重复入队合并成一行，generation 递增，让跑到一半时新入队的任务在跑完后重跑一次。
-- 跑完即删行；status 只有 pending / running / failed。
CREATE TABLE IF NOT EXISTS person_appearance_jobs (
    person_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    generation INTEGER NOT NULL DEFAULT 1,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_person_appearance_jobs_due
    ON person_appearance_jobs(status, next_attempt_at);

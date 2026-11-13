-- 管理台重启操作的幂等记录：同一 Idempotency-Key 重放返回第一次的结果，
-- 不再次执行 systemctl restart；失败结果也保留，避免重试放大故障。

CREATE TABLE IF NOT EXISTS restart_requests (
    idempotency_key TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_restart_requests_time ON restart_requests(created_at);

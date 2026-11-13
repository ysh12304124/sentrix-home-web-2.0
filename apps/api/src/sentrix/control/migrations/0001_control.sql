-- 控制面自有数据库。账号、会话、反馈、审计、事件与采样都在这里，
-- 用户媒体与问答正文永不写入本库。

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
    password_algo TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_params_json TEXT NOT NULL DEFAULT '{}',
    api_url TEXT NOT NULL DEFAULT '',
    data_root TEXT NOT NULL DEFAULT '',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role, disabled);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    csrf_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL CHECK (kind IN ('problem', 'inaccurate', 'suggestion')),
    body TEXT NOT NULL,
    route TEXT NOT NULL DEFAULT '',
    scope_id TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL DEFAULT '',
    asset_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('received', 'triaging', 'processed')),
    assignee TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    internal_note TEXT NOT NULL DEFAULT '',
    reply TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_idempotency
    ON feedback(account_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_feedback_account ON feedback(account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status, created_at);

CREATE TABLE IF NOT EXISTS feedback_attachments (
    id TEXT PRIMARY KEY,
    feedback_id TEXT NOT NULL REFERENCES feedback(id),
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_attachments_feedback
    ON feedback_attachments(feedback_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    time TEXT NOT NULL,
    actor_account_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    request_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_audit_events_time ON audit_events(time);
CREATE INDEX IF NOT EXISTS idx_audit_events_actor ON audit_events(actor_account_id, time);

CREATE TABLE IF NOT EXISTS incidents (
    event_id TEXT PRIMARY KEY,
    time TEXT NOT NULL,
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    recovery_action TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, last_seen);
CREATE INDEX IF NOT EXISTS idx_incidents_user ON incidents(user_id, last_seen);

CREATE TABLE IF NOT EXISTS metric_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metric_samples_time ON metric_samples(sampled_at);
CREATE INDEX IF NOT EXISTS idx_metric_samples_source ON metric_samples(source, sampled_at);

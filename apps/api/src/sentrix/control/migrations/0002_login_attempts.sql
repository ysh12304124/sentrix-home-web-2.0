-- 失败登录限速与审计。只保留窗口内需要的记录，清理由登录路径顺带完成。

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    success INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_account
    ON login_attempts(account_id, ip, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip
    ON login_attempts(ip, attempted_at);

-- 用户关键操作的语义轨迹。只存固定事件名、路由模板、结果码和已有对象 ID；
-- 不存文件名、问答/反馈正文、原始媒体地址、异常文本或任意请求内容。
-- 账号与会话由认证上下文填写，客户端不能自报身份。

CREATE TABLE IF NOT EXISTS client_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    account_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    route TEXT NOT NULL,
    result TEXT NOT NULL,
    scope_id TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL DEFAULT '',
    asset_id TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_client_events_account ON client_events(account_id, id);
CREATE INDEX IF NOT EXISTS idx_client_events_time ON client_events(received_at);

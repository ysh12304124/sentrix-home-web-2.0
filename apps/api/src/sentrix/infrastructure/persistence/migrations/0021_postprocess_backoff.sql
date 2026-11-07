-- 批末后处理失败后的退避：避免持续失败时每轮空闲都立刻重报。
-- 失败的作业标 partial；成功后清回 completed。

ALTER TABLE scope_postprocess ADD COLUMN next_attempt_at TEXT;

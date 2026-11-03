-- 普通执行失败计数与执行代次分开：等待依赖不消耗额度，只有依赖可用但执行异常
-- 需要重试时才递增。attempt_no 继续作为实际执行代次与事件/结果集隔离标识。
ALTER TABLE agent_turns ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;

-- 结果集归属到具体回合尝试，失败尝试的临时结果可被清理。
ALTER TABLE agent_result_sets ADD COLUMN turn_id TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_result_sets ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_agent_result_sets_turn
    ON agent_result_sets(turn_id, attempt_no);

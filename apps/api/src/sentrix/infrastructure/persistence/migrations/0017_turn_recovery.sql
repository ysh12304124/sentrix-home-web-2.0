-- 问答回合的同回合恢复：幂等键、尝试号、租约/围栏与单调事件序号。
-- 扩展式迁移：旧事件先复制进 v2，不删除旧表。

ALTER TABLE agent_turns ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_turns ADD COLUMN execution_state TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_turns ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_turns ADD COLUMN lease_expires_at TEXT;
ALTER TABLE agent_turns ADD COLUMN fence INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_turns ADD COLUMN waiting_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_turns ADD COLUMN next_retry_at TEXT;
ALTER TABLE agent_turns ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_turns_idempotency
    ON agent_turns(conversation_id, idempotency_key) WHERE idempotency_key <> '';

-- 一回合只有一条最终助手消息。旧库里一个回合最多只有一条，不会挡住迁移。
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_messages_assistant_turn
    ON agent_conversation_messages(turn_id)
    WHERE role = 'assistant' AND turn_id <> '';

CREATE TABLE IF NOT EXISTS agent_turn_events_v2 (
    turn_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (turn_id, event_seq)
);

INSERT OR IGNORE INTO agent_turn_events_v2 (turn_id, event_seq, attempt_no, payload_json)
    SELECT turn_id, step_index, 1, payload_json FROM agent_turn_events;

CREATE INDEX IF NOT EXISTS idx_agent_turn_events_v2_turn
    ON agent_turn_events_v2(turn_id, event_seq);

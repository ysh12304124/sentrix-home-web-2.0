-- 相册后处理代际与导入重放所需的稳定键。
-- 每次资产提交成功把 dirty_generation 加一；空闲后按代际跑一次批末阶段。

CREATE TABLE IF NOT EXISTS scope_postprocess (
    scope_id TEXT PRIMARY KEY,
    dirty_generation INTEGER NOT NULL DEFAULT 0,
    processed_generation INTEGER NOT NULL DEFAULT 0,
    processing_generation INTEGER,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scope_postprocess_pending
    ON scope_postprocess(dirty_generation, processed_generation);

-- 文本向量独立表。语义与 memory_vectors 相同，但只住 semantic / episodic 两个空间。
--
-- 为什么分表：
--   1. 文本检索（search_exact）只扫文本行，不再和 visual/asset、visual/face_instance
--      混在一张表里；
--   2. 视频清理可以按表删，不必在同一条 SQL 里同时处理资产级与观察级向量；
--   3. 将来给文本接 ANN 时，可以只针对这张表建索引、独立搬迁。
--
-- CHECK 把「只有文本空间」钉在库层：visual 向量仍归 memory_vectors。
-- 本次迁移只建表，不搬数据；存量复制由 scripts/maintenance/text_vectors_migrate.py
-- 执行（可 dry-run、可审计），旧行在验证稳定前保留。
CREATE TABLE text_vectors (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    space TEXT NOT NULL CHECK (space IN ('semantic', 'episodic')),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(space, source_type, source_id, model_name)
);

CREATE INDEX idx_text_vectors_space_model ON text_vectors(space, model_name);
CREATE INDEX idx_text_vectors_source ON text_vectors(space, source_type, source_id);
CREATE INDEX idx_text_vectors_scope ON text_vectors(scope_id);

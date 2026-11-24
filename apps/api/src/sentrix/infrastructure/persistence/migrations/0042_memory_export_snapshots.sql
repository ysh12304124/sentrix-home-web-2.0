-- S6: 图片记忆导出快照。只记录只读快照与文件摘要，不复制素材正文。
CREATE TABLE memory_export_snapshots (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    schema_version TEXT NOT NULL DEFAULT 'image-memory-v1',
    compat_mode TEXT NOT NULL DEFAULT 'strict_v3',
    selection_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    directory TEXT NOT NULL DEFAULT '',
    files_json TEXT NOT NULL DEFAULT '[]',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    counts_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_memory_exports_scope ON memory_export_snapshots(scope_id, created_at DESC);
CREATE INDEX idx_memory_exports_status ON memory_export_snapshots(status);

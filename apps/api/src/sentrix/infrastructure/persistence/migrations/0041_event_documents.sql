-- S4/S5: 真事件文档、事件词法投影与内部 merge 重定向。
-- 文档只按 event+revision 追加，读取只认 active；成员、参与者、时地、覆盖与依赖
-- 都绑定当前有效证据，旧事件摘要不会被伪装成逐项可溯源。
CREATE TABLE event_documents (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    doc_version TEXT NOT NULL DEFAULT 'event-doc-v1',
    title TEXT NOT NULL DEFAULT '',
    short_summary TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summary_source TEXT NOT NULL DEFAULT 'deterministic',
    summary_state TEXT NOT NULL DEFAULT 'supported_by_content_items',
    content_items_json TEXT NOT NULL DEFAULT '[]',
    members_json TEXT NOT NULL DEFAULT '[]',
    participants_json TEXT NOT NULL DEFAULT '[]',
    person_refs_json TEXT NOT NULL DEFAULT '[]',
    time_json TEXT NOT NULL DEFAULT '{}',
    place_json TEXT NOT NULL DEFAULT '{}',
    coverage_json TEXT NOT NULL DEFAULT '{}',
    dependencies_json TEXT NOT NULL DEFAULT '{}',
    input_hash TEXT NOT NULL DEFAULT '',
    capture_start TEXT,
    capture_end TEXT,
    time_precision TEXT NOT NULL DEFAULT 'unknown',
    timezone_state TEXT NOT NULL DEFAULT 'unknown',
    member_dates_json TEXT NOT NULL DEFAULT '[]',
    physical_place_text TEXT NOT NULL DEFAULT '',
    scene_text TEXT NOT NULL DEFAULT '',
    place_conflict INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_id, event_id, revision)
);

CREATE INDEX idx_event_documents_event ON event_documents(event_id, status, revision DESC);
CREATE INDEX idx_event_documents_scope ON event_documents(scope_id, status);
CREATE INDEX idx_event_documents_capture ON event_documents(scope_id, capture_start);

CREATE TABLE event_redirects (
    scope_id TEXT NOT NULL,
    old_event_id TEXT NOT NULL,
    new_event_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope_id, old_event_id)
);

CREATE INDEX idx_event_redirects_new ON event_redirects(new_event_id);

CREATE TABLE event_search_terms (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    field_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    evidence_ref TEXT NOT NULL DEFAULT '',
    source_channel TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_event_search_terms_event ON event_search_terms(event_id);
CREATE INDEX idx_event_search_terms_scope ON event_search_terms(scope_id, field_type);

CREATE VIRTUAL TABLE event_search_fts USING fts5(
    tokens,
    term_id UNINDEXED,
    scope_id UNINDEXED,
    event_id UNINDEXED,
    document_id UNINDEXED,
    field_type UNINDEXED,
    normalized_value UNINDEXED,
    evidence_ref UNINDEXED
);

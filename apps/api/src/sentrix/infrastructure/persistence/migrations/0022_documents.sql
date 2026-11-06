CREATE TABLE documents (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    format TEXT NOT NULL,
    active_revision INTEGER NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE document_elements (
    id TEXT PRIMARY KEY,
    document_asset_id TEXT NOT NULL REFERENCES documents(asset_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    page_no INTEGER,
    section_path TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    confidence REAL,
    created_at TEXT NOT NULL,
    UNIQUE(document_asset_id, revision, ordinal)
);

CREATE INDEX idx_document_elements_revision
ON document_elements(document_asset_id, revision, ordinal);

CREATE TABLE document_chunks (
    id TEXT PRIMARY KEY,
    document_asset_id TEXT NOT NULL REFERENCES documents(asset_id) ON DELETE CASCADE,
    scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_element_id TEXT NOT NULL REFERENCES document_elements(id),
    end_element_id TEXT NOT NULL REFERENCES document_elements(id),
    section_path TEXT NOT NULL DEFAULT '',
    page_start INTEGER,
    page_end INTEGER,
    token_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_asset_id, revision, ordinal)
);

CREATE INDEX idx_document_chunks_scope ON document_chunks(scope_id);
CREATE INDEX idx_document_chunks_revision
ON document_chunks(document_asset_id, revision, ordinal);

CREATE VIRTUAL TABLE document_chunk_fts USING fts5(
    chunk_id UNINDEXED,
    document_asset_id UNINDEXED,
    scope_id UNINDEXED,
    text,
    tokenize = 'unicode61'
);

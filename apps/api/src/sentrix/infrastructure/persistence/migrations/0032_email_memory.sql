ALTER TABLE observations ADD COLUMN text_content TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN external_id TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX idx_email_external_id ON observations(scope_id,source_type,external_id)
    WHERE source_type='email' AND external_id!='';
CREATE TABLE email_import_state (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    indexed_revision INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT ''
);

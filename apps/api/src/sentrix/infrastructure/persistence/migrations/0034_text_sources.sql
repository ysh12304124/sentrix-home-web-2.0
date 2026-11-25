ALTER TABLE assets ADD COLUMN external_source_id TEXT;
CREATE UNIQUE INDEX idx_assets_external_source ON assets(scope_id, external_source_id)
    WHERE external_source_id IS NOT NULL;

ALTER TABLE observations ADD COLUMN source_text TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_observations_text_scope ON observations(scope_id) WHERE source_text != '';

-- Expose existing email bodies through the mainline source reader.
UPDATE observations SET source_text = text_content WHERE text_content != '';

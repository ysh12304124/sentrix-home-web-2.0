ALTER TABLE assets ADD COLUMN capture_time_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE asset_places ADD COLUMN nearest_evidence_json TEXT NOT NULL DEFAULT '{}';

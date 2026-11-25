ALTER TABLE asset_places ADD COLUMN precise_address TEXT NOT NULL DEFAULT '';
ALTER TABLE asset_places ADD COLUMN address_source TEXT NOT NULL DEFAULT '';
ALTER TABLE asset_places ADD COLUMN address_source_sha256 TEXT NOT NULL DEFAULT '';

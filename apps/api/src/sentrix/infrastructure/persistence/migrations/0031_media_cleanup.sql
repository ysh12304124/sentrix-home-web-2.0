-- 3C: migrate only deletion ledgers, retaining every historic identity and audit.
-- Child-first copies avoid deferred-FK counters and never disable foreign_keys.
CREATE TEMP TABLE backup_3c_media_deletions AS SELECT * FROM media_deletions;
CREATE TEMP TABLE backup_3c_media_deletion_members AS SELECT * FROM media_deletion_members;
CREATE TEMP TABLE backup_3c_media_deletion_plans AS SELECT * FROM media_deletion_plans;
CREATE TEMP TABLE backup_3c_media_reconciliations AS SELECT * FROM media_reconciliations;
CREATE TEMP TABLE backup_3c_media_reconciliation_steps AS SELECT * FROM media_reconciliation_steps;
CREATE TEMP TABLE backup_3c_media_evidence_revocations AS SELECT * FROM media_evidence_revocations;
DROP TRIGGER media_member_tombstones;
DROP TRIGGER media_plan_immutable_update;
DROP TRIGGER media_plan_immutable_delete;
DROP TRIGGER media_revocation_immutable_update;
DROP TRIGGER media_revocation_immutable_delete;
DELETE FROM media_reconciliation_steps;
DELETE FROM media_reconciliations;
DELETE FROM media_evidence_revocations;
DELETE FROM media_deletion_plans;
DELETE FROM media_deletion_members;
DELETE FROM media_deletions;
DROP TABLE media_deletion_members;
DROP TABLE media_deletions;
CREATE TABLE media_deletions (
 deletion_id TEXT PRIMARY KEY,
 scope_id TEXT NOT NULL,
 root_asset_id TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK (status IN ('isolated', 'failed')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 retry_count INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '',
 generation INTEGER NOT NULL,
 idempotency_key TEXT,
 UNIQUE(scope_id, idempotency_key)
);
CREATE TABLE media_deletion_members (
 deletion_id TEXT NOT NULL REFERENCES media_deletions(deletion_id),
 asset_id TEXT NOT NULL,
 generation INTEGER NOT NULL,
 PRIMARY KEY(deletion_id, asset_id)
);
INSERT INTO media_deletions SELECT * FROM backup_3c_media_deletions;
DROP TABLE backup_3c_media_deletions;
INSERT INTO media_deletion_members SELECT * FROM backup_3c_media_deletion_members;
DROP TABLE backup_3c_media_deletion_members;
INSERT INTO media_deletion_plans SELECT * FROM backup_3c_media_deletion_plans;
DROP TABLE backup_3c_media_deletion_plans;
INSERT INTO media_reconciliations SELECT * FROM backup_3c_media_reconciliations;
DROP TABLE backup_3c_media_reconciliations;
INSERT INTO media_reconciliation_steps SELECT * FROM backup_3c_media_reconciliation_steps;
DROP TABLE backup_3c_media_reconciliation_steps;
INSERT INTO media_evidence_revocations SELECT * FROM backup_3c_media_evidence_revocations;
DROP TABLE backup_3c_media_evidence_revocations;
CREATE TRIGGER media_member_tombstones AFTER INSERT ON media_deletion_members BEGIN
 INSERT OR IGNORE INTO media_tombstones SELECT 'asset',a.id,a.scope_id,NEW.deletion_id,NEW.generation,d.created_at
 FROM assets a JOIN media_deletions d ON d.deletion_id=NEW.deletion_id WHERE a.id=NEW.asset_id;
 INSERT OR IGNORE INTO media_tombstones SELECT 'observation',o.id,o.scope_id,NEW.deletion_id,NEW.generation,d.created_at
 FROM observations o JOIN media_deletions d ON d.deletion_id=NEW.deletion_id WHERE o.asset_id=NEW.asset_id;
 INSERT OR IGNORE INTO media_tombstones SELECT 'face_instance',f.id,a.scope_id,NEW.deletion_id,NEW.generation,d.created_at
 FROM face_instances f JOIN assets a ON a.id=f.asset_id JOIN media_deletions d ON d.deletion_id=NEW.deletion_id WHERE f.asset_id=NEW.asset_id;
END;
CREATE TRIGGER media_plan_immutable_update BEFORE UPDATE ON media_deletion_plans BEGIN SELECT RAISE(ABORT,'immutable_deletion_plan'); END;
CREATE TRIGGER media_plan_immutable_delete BEFORE DELETE ON media_deletion_plans BEGIN SELECT RAISE(ABORT,'immutable_deletion_plan'); END;
CREATE TRIGGER media_revocation_immutable_update BEFORE UPDATE ON media_evidence_revocations BEGIN SELECT RAISE(ABORT,'immutable_evidence_revocation'); END;
CREATE TRIGGER media_revocation_immutable_delete BEFORE DELETE ON media_evidence_revocations BEGIN SELECT RAISE(ABORT,'immutable_evidence_revocation'); END;

CREATE TABLE media_cleanups (
 deletion_id TEXT PRIMARY KEY REFERENCES media_deletions(deletion_id),
 plan_id TEXT NOT NULL REFERENCES media_deletion_plans(plan_id),
 status TEXT NOT NULL CHECK(status IN ('running','failed','blocked','storage_cleaned','storage_cleaned_with_retained')),
 sqlite_done INTEGER NOT NULL DEFAULT 0,
 retry_count INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '',
 manifest_json TEXT NOT NULL,
 validation_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE media_cleanup_resources (
 deletion_id TEXT NOT NULL REFERENCES media_cleanups(deletion_id),
 resource_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('file','qdrant','inventory')),
 status TEXT NOT NULL CHECK(status IN ('pending','failed','blocked','done','retained_shared','retained_external')),
 locator_json TEXT NOT NULL,
 path TEXT,
 resolved_path TEXT,
 attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL,
 PRIMARY KEY(deletion_id,resource_id)
);
CREATE INDEX idx_media_cleanup_resources_status ON media_cleanup_resources(deletion_id,status);
CREATE TABLE media_cleanup_steps (
 deletion_id TEXT NOT NULL REFERENCES media_cleanups(deletion_id),
 step TEXT NOT NULL,
 result_json TEXT NOT NULL,
 completed_at TEXT NOT NULL,
 PRIMARY KEY(deletion_id,step)
);
CREATE TRIGGER media_cleanup_asset_path_insert BEFORE INSERT ON assets
 WHEN EXISTS (SELECT 1 FROM media_cleanup_resources r WHERE r.kind='file'
 AND r.status NOT IN ('retained_shared','retained_external')
 AND (r.path=NEW.path OR r.resolved_path=media_realpath(NEW.path)))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
CREATE TRIGGER media_cleanup_asset_path_update BEFORE UPDATE OF path ON assets
 WHEN NEW.path!=OLD.path AND EXISTS (SELECT 1 FROM media_cleanup_resources r WHERE r.kind='file'
 AND r.status NOT IN ('retained_shared','retained_external')
 AND (r.path=NEW.path OR r.resolved_path=media_realpath(NEW.path)))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;

DROP TRIGGER isolation_asset_delete;
CREATE TRIGGER isolation_asset_delete BEFORE DELETE ON assets
 WHEN OLD.deletion_state!='active' AND NOT (
 media_reconcile_allowed()=1 AND EXISTS (
 SELECT 1 FROM media_cleanups c JOIN media_deletion_members m ON m.deletion_id=c.deletion_id
 JOIN media_deletions d ON d.deletion_id=c.deletion_id
 JOIN media_reconciliations r ON r.deletion_id=c.deletion_id
 JOIN media_tombstones t ON t.kind='asset' AND t.object_id=OLD.id
 WHERE m.asset_id=OLD.id AND m.generation=OLD.deletion_generation AND c.sqlite_done=0
 AND d.status='isolated' AND r.status='reconciled' AND t.deletion_id=d.deletion_id
 AND t.generation=m.generation AND t.scope_id=OLD.scope_id))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;

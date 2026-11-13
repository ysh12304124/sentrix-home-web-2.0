-- 3D lifecycle is additive: legacy media_deletions.status remains compatible.
CREATE TABLE media_deletion_lifecycle (
 deletion_id TEXT PRIMARY KEY REFERENCES media_deletions(deletion_id),
 state TEXT NOT NULL CHECK(state IN ('isolated','cleaning','rebuilding','completed','failed','blocked')),
 generation INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', validation_json TEXT NOT NULL DEFAULT '{}',
 completed_at TEXT, updated_at TEXT NOT NULL
);
INSERT INTO media_deletion_lifecycle(deletion_id,state,updated_at) SELECT deletion_id,CASE WHEN status='failed' THEN 'failed' ELSE 'isolated' END,updated_at FROM media_deletions;
CREATE TRIGGER media_lifecycle_new AFTER INSERT ON media_deletions BEGIN
 INSERT INTO media_deletion_lifecycle(deletion_id,state,updated_at) VALUES(NEW.deletion_id,'isolated',NEW.updated_at);
END;
CREATE TRIGGER media_lifecycle_cleaning AFTER INSERT ON media_cleanups BEGIN
 UPDATE media_deletion_lifecycle SET state='cleaning',updated_at=NEW.updated_at WHERE deletion_id=NEW.deletion_id;
END;
CREATE TABLE media_scope_epochs (
 scope_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 1,
 paused INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
INSERT INTO media_scope_epochs SELECT scope_id,generation,1,cutoff_at FROM media_scope_invalidations;
CREATE TABLE media_rebuild_jobs (
 scope_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued'
 CHECK(status IN ('queued','running','failed','blocked','completed')),
 generation INTEGER NOT NULL DEFAULT 0, fence INTEGER NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 publish_done INTEGER NOT NULL DEFAULT 0, snapshot_hash TEXT NOT NULL DEFAULT '', affected_json TEXT NOT NULL DEFAULT '[]',
 validation_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
);
CREATE TABLE media_rebuild_members (
 deletion_id TEXT PRIMARY KEY REFERENCES media_deletions(deletion_id),
 scope_id TEXT NOT NULL REFERENCES media_rebuild_jobs(scope_id),
 generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE media_rebuild_cache (
 scope_id TEXT NOT NULL, object_table TEXT NOT NULL, object_key TEXT NOT NULL,
 evidence_hash TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(scope_id,object_table,object_key,evidence_hash)
);
CREATE TABLE media_rebuild_dispositions (
 scope_id TEXT NOT NULL, object_table TEXT NOT NULL, object_key TEXT NOT NULL,
 generation INTEGER NOT NULL, action TEXT NOT NULL, evidence_json TEXT NOT NULL,
 PRIMARY KEY(scope_id,object_table,object_key)
);
CREATE TABLE media_rebuild_publications (
 object_table TEXT NOT NULL, object_key TEXT NOT NULL, scope_id TEXT NOT NULL,
 generation INTEGER NOT NULL, PRIMARY KEY(object_table,object_key)
);
CREATE TRIGGER media_rebuild_on_cleanup AFTER UPDATE OF status ON media_cleanups
 WHEN NEW.status IN ('storage_cleaned','storage_cleaned_with_retained') AND (OLD.status!=NEW.status OR NOT EXISTS(SELECT 1 FROM media_rebuild_members WHERE deletion_id=NEW.deletion_id)) BEGIN
 INSERT INTO media_rebuild_jobs(scope_id,updated_at) SELECT scope_id,NEW.updated_at FROM media_deletions WHERE deletion_id=NEW.deletion_id
 ON CONFLICT(scope_id) DO UPDATE SET status='queued',next_attempt=0,updated_at=excluded.updated_at;
 INSERT OR IGNORE INTO media_rebuild_members(deletion_id,scope_id)
 SELECT deletion_id,scope_id FROM media_deletions WHERE deletion_id=NEW.deletion_id;
 UPDATE media_deletion_lifecycle SET state='rebuilding',updated_at=NEW.updated_at WHERE deletion_id=NEW.deletion_id;
END;
INSERT INTO media_rebuild_jobs(scope_id,updated_at)
 SELECT DISTINCT d.scope_id,MAX(c.updated_at) FROM media_cleanups c JOIN media_deletions d ON d.deletion_id=c.deletion_id
 WHERE c.status IN ('storage_cleaned','storage_cleaned_with_retained') GROUP BY d.scope_id;
INSERT INTO media_rebuild_members(deletion_id,scope_id)
 SELECT d.deletion_id,d.scope_id FROM media_deletions d JOIN media_cleanups c ON c.deletion_id=d.deletion_id
 WHERE c.status IN ('storage_cleaned','storage_cleaned_with_retained');
CREATE TRIGGER media_epoch_assets_insert AFTER INSERT ON assets
 WHEN NEW.scope_id IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_assets_update AFTER UPDATE ON assets
 WHEN NEW.scope_id IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_assets_delete AFTER DELETE ON assets
 WHEN OLD.scope_id IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 END;
CREATE TRIGGER media_epoch_observations_insert AFTER INSERT ON observations
 WHEN NEW.scope_id IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_observations_update AFTER UPDATE ON observations
 WHEN NEW.scope_id IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_observations_delete AFTER DELETE ON observations
 WHEN OLD.scope_id IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 END;
CREATE TRIGGER media_epoch_face_instances_insert AFTER INSERT ON face_instances
 WHEN (SELECT scope_id FROM assets WHERE id=NEW.asset_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM assets WHERE id=NEW.asset_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM assets WHERE id=NEW.asset_id);
 END;
CREATE TRIGGER media_epoch_face_instances_update AFTER UPDATE ON face_instances
 WHEN (SELECT scope_id FROM assets WHERE id=NEW.asset_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM assets WHERE id=NEW.asset_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM assets WHERE id=NEW.asset_id);
 END;
CREATE TRIGGER media_epoch_face_instances_delete AFTER DELETE ON face_instances
 WHEN (SELECT scope_id FROM assets WHERE id=OLD.asset_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM assets WHERE id=OLD.asset_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM assets WHERE id=OLD.asset_id);
 END;
CREATE TRIGGER media_epoch_entity_mentions_insert AFTER INSERT ON entity_mentions
 WHEN (SELECT scope_id FROM entities WHERE id=NEW.entity_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_mentions_update AFTER UPDATE ON entity_mentions
 WHEN (SELECT scope_id FROM entities WHERE id=NEW.entity_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_mentions_delete AFTER DELETE ON entity_mentions
 WHEN (SELECT scope_id FROM entities WHERE id=OLD.entity_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=OLD.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=OLD.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_observations_insert AFTER INSERT ON entity_observations
 WHEN (SELECT scope_id FROM entities WHERE id=NEW.entity_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_observations_update AFTER UPDATE ON entity_observations
 WHEN (SELECT scope_id FROM entities WHERE id=NEW.entity_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_observations_delete AFTER DELETE ON entity_observations
 WHEN (SELECT scope_id FROM entities WHERE id=OLD.entity_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=OLD.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=OLD.entity_id);
 END;
CREATE TRIGGER media_epoch_event_observations_insert AFTER INSERT ON event_observations
 WHEN (SELECT scope_id FROM observations WHERE id=NEW.observation_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM observations WHERE id=NEW.observation_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM observations WHERE id=NEW.observation_id);
 END;
CREATE TRIGGER media_epoch_event_observations_update AFTER UPDATE ON event_observations
 WHEN (SELECT scope_id FROM observations WHERE id=NEW.observation_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM observations WHERE id=NEW.observation_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM observations WHERE id=NEW.observation_id);
 END;
CREATE TRIGGER media_epoch_event_observations_delete AFTER DELETE ON event_observations
 WHEN (SELECT scope_id FROM observations WHERE id=OLD.observation_id) IS NOT NULL BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM observations WHERE id=OLD.observation_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM observations WHERE id=OLD.observation_id);
 END;
CREATE TRIGGER media_epoch_entities_insert AFTER INSERT ON entities
 WHEN NEW.scope_id IS NOT NULL AND media_reconcile_allowed()=0 BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_entities_update AFTER UPDATE OF canonical_name,status,family_role,identity_state,role_state,name_state ON entities
 WHEN NEW.scope_id IS NOT NULL AND media_reconcile_allowed()=0 BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_entities_delete AFTER DELETE ON entities
 WHEN OLD.scope_id IS NOT NULL AND media_reconcile_allowed()=0 BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 END;
CREATE TRIGGER media_epoch_entity_properties_insert AFTER INSERT ON entity_properties
 WHEN (SELECT scope_id FROM entities WHERE id=NEW.entity_id) IS NOT NULL AND NEW.source IN ('user','user_override','manual') BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_properties_update AFTER UPDATE ON entity_properties
 WHEN (SELECT scope_id FROM entities WHERE id=NEW.entity_id) IS NOT NULL AND NEW.source IN ('user','user_override','manual') BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id);
 END;
CREATE TRIGGER media_epoch_entity_properties_delete AFTER DELETE ON entity_properties
 WHEN (SELECT scope_id FROM entities WHERE id=OLD.entity_id) IS NOT NULL AND OLD.source IN ('user','user_override','manual') BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=OLD.entity_id);
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=(SELECT scope_id FROM entities WHERE id=OLD.entity_id);
 END;
CREATE TRIGGER media_epoch_relationships_insert AFTER INSERT ON relationships
 WHEN NEW.scope_id IS NOT NULL AND 0 BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_relationships_update AFTER UPDATE ON relationships
 WHEN NEW.scope_id IS NOT NULL AND 0 BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=NEW.scope_id;
 END;
CREATE TRIGGER media_epoch_relationships_delete AFTER DELETE ON relationships
 WHEN OLD.scope_id IS NOT NULL AND 0 BEGIN
 UPDATE media_scope_epochs SET generation=generation+1,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=unixepoch('now')+2,updated_at=datetime('now') WHERE scope_id=OLD.scope_id;
 END;
CREATE TRIGGER media_epoch_new_isolation AFTER INSERT ON media_deletion_members BEGIN
 INSERT INTO media_scope_epochs(scope_id,generation,paused,updated_at)
 SELECT scope_id,1,1,datetime('now') FROM media_deletions WHERE deletion_id=NEW.deletion_id
 ON CONFLICT(scope_id) DO UPDATE SET generation=generation+1,paused=1,updated_at=excluded.updated_at;
 UPDATE media_rebuild_jobs SET status=CASE WHEN status='running' THEN status ELSE 'queued' END,next_attempt=0 WHERE scope_id=(SELECT scope_id FROM media_deletions WHERE deletion_id=NEW.deletion_id);
END;
DROP TRIGGER tombstone_person_portrait_revisions_insert_1;
CREATE TRIGGER tombstone_person_portrait_revisions_insert_1 BEFORE INSERT ON person_portrait_revisions WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_person_portrait_revisions_update_1;
CREATE TRIGGER tombstone_person_portrait_revisions_update_1 BEFORE UPDATE ON person_portrait_revisions WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_semantic_profiles_insert_1;
CREATE TRIGGER tombstone_semantic_profiles_insert_1 BEFORE INSERT ON semantic_profiles WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_semantic_profiles_update_1;
CREATE TRIGGER tombstone_semantic_profiles_update_1 BEFORE UPDATE ON semantic_profiles WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_family_person_portraits_insert_2;
CREATE TRIGGER tombstone_family_person_portraits_insert_2 BEFORE INSERT ON family_person_portraits WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_family_person_portraits_update_2;
CREATE TRIGGER tombstone_family_person_portraits_update_2 BEFORE UPDATE ON family_person_portraits WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_family_analysis_runs_insert_1;
CREATE TRIGGER tombstone_family_analysis_runs_insert_1 BEFORE INSERT ON family_analysis_runs WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_family_analysis_runs_update_1;
CREATE TRIGGER tombstone_family_analysis_runs_update_1 BEFORE UPDATE ON family_analysis_runs WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_memory_graph_nodes_insert_1;
CREATE TRIGGER tombstone_memory_graph_nodes_insert_1 BEFORE INSERT ON memory_graph_nodes WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_memory_graph_nodes_update_1;
CREATE TRIGGER tombstone_memory_graph_nodes_update_1 BEFORE UPDATE ON memory_graph_nodes WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_memory_graph_edges_insert_1;
CREATE TRIGGER tombstone_memory_graph_edges_insert_1 BEFORE INSERT ON memory_graph_edges WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_memory_graph_edges_update_1;
CREATE TRIGGER tombstone_memory_graph_edges_update_1 BEFORE UPDATE ON memory_graph_edges WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_memory_graph_builds_insert_1;
CREATE TRIGGER tombstone_memory_graph_builds_insert_1 BEFORE INSERT ON memory_graph_builds WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER tombstone_memory_graph_builds_update_1;
CREATE TRIGGER tombstone_memory_graph_builds_update_1 BEFORE UPDATE ON memory_graph_builds WHEN ((EXISTS (SELECT 1 FROM media_tombstones t WHERE t.kind='asset' AND t.scope_id=NEW.scope_id))) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_entities_insert;
CREATE TRIGGER media_review_lock_entities_insert BEFORE INSERT ON entities WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='entities' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_entities_update;
CREATE TRIGGER media_review_lock_entities_update BEFORE UPDATE OF summary,confidence ON entities WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='entities' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_entities_delete;
CREATE TRIGGER media_review_lock_entities_delete BEFORE DELETE ON entities WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='entities' AND ms.object_key=json_object('id',OLD.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(OLD.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_face_clusters_insert;
CREATE TRIGGER media_review_lock_face_clusters_insert BEFORE INSERT ON face_clusters WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='face_clusters' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_face_clusters_update;
CREATE TRIGGER media_review_lock_face_clusters_update BEFORE UPDATE OF representative_embedding_json,member_count,confidence ON face_clusters WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='face_clusters' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_face_clusters_delete;
CREATE TRIGGER media_review_lock_face_clusters_delete BEFORE DELETE ON face_clusters WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='face_clusters' AND ms.object_key=json_object('id',OLD.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(OLD.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_events_insert;
CREATE TRIGGER media_review_lock_events_insert BEFORE INSERT ON events WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_events_update;
CREATE TRIGGER media_review_lock_events_update BEFORE UPDATE ON events WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_events_delete;
CREATE TRIGGER media_review_lock_events_delete BEFORE DELETE ON events WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',OLD.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(OLD.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_face_prototypes_insert;
CREATE TRIGGER media_review_lock_face_prototypes_insert BEFORE INSERT ON face_prototypes WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='face_prototypes' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed((SELECT scope_id FROM face_clusters WHERE id=NEW.cluster_id))=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_face_prototypes_update;
CREATE TRIGGER media_review_lock_face_prototypes_update BEFORE UPDATE ON face_prototypes WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='face_prototypes' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed((SELECT scope_id FROM face_clusters WHERE id=NEW.cluster_id))=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_face_prototypes_delete;
CREATE TRIGGER media_review_lock_face_prototypes_delete BEFORE DELETE ON face_prototypes WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='face_prototypes' AND ms.object_key=json_object('id',OLD.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed((SELECT scope_id FROM face_clusters WHERE id=OLD.cluster_id))=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_semantic_profiles_insert;
CREATE TRIGGER media_review_lock_semantic_profiles_insert BEFORE INSERT ON semantic_profiles WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='semantic_profiles' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_semantic_profiles_update;
CREATE TRIGGER media_review_lock_semantic_profiles_update BEFORE UPDATE ON semantic_profiles WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='semantic_profiles' AND ms.object_key=json_object('id',NEW.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_review_lock_semantic_profiles_delete;
CREATE TRIGGER media_review_lock_semantic_profiles_delete BEFORE DELETE ON semantic_profiles WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='semantic_profiles' AND ms.object_key=json_object('id',OLD.id) AND (1=1)) AND media_reconcile_allowed()=0 AND media_generation_allowed(OLD.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_facts_insert;
CREATE TRIGGER media_scope_model_lock_facts_insert BEFORE INSERT ON facts WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (0) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_facts_update;
CREATE TRIGGER media_scope_model_lock_facts_update BEFORE UPDATE ON facts WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (0) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_relationships_insert;
CREATE TRIGGER media_scope_model_lock_relationships_insert BEFORE INSERT ON relationships WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (0) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_relationships_update;
CREATE TRIGGER media_scope_model_lock_relationships_update BEFORE UPDATE ON relationships WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (0) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_semantic_claims_insert;
CREATE TRIGGER media_scope_model_lock_semantic_claims_insert BEFORE INSERT ON semantic_claims WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (NEW.confidence_source IN ('user','manual','user_override','user-confirmed','user_confirmed') AND json_valid(NEW.evidence_ids_json) AND NEW.evidence_ids_json='[]' AND NEW.supporting_event_ids_json='[]') AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_semantic_claims_update;
CREATE TRIGGER media_scope_model_lock_semantic_claims_update BEFORE UPDATE ON semantic_claims WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (NEW.confidence_source IN ('user','manual','user_override','user-confirmed','user_confirmed') AND json_valid(NEW.evidence_ids_json) AND NEW.evidence_ids_json='[]' AND NEW.supporting_event_ids_json='[]') AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_entity_properties_insert;
CREATE TRIGGER media_scope_model_lock_entity_properties_insert BEFORE INSERT ON entity_properties WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id)) AND NOT (NEW.source IN ('user','manual','user_override','user-confirmed','user_confirmed') AND json_valid(NEW.evidence_ids_json) AND NEW.evidence_ids_json='[]') AND media_generation_allowed((SELECT scope_id FROM entities WHERE id=NEW.entity_id))=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_entity_properties_update;
CREATE TRIGGER media_scope_model_lock_entity_properties_update BEFORE UPDATE ON entity_properties WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=(SELECT scope_id FROM entities WHERE id=NEW.entity_id)) AND NOT (NEW.source IN ('user','manual','user_override','user-confirmed','user_confirmed') AND json_valid(NEW.evidence_ids_json) AND NEW.evidence_ids_json='[]') AND media_generation_allowed((SELECT scope_id FROM entities WHERE id=NEW.entity_id))=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_events_insert;
CREATE TRIGGER media_scope_model_lock_events_insert BEFORE INSERT ON events WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (0) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_events_update;
CREATE TRIGGER media_scope_model_lock_events_update BEFORE UPDATE ON events WHEN media_reconcile_allowed()=0 AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND NOT (0) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_entities_insert;
CREATE TRIGGER media_scope_model_lock_entities_insert BEFORE INSERT ON entities WHEN media_reconcile_allowed()=0 AND NEW.summary!='' AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP TRIGGER media_scope_model_lock_entities_update;
CREATE TRIGGER media_scope_model_lock_entities_update BEFORE UPDATE OF summary,confidence ON entities WHEN media_reconcile_allowed()=0 AND NEW.summary!='' AND EXISTS (SELECT 1 FROM media_scope_invalidations si WHERE si.scope_id=NEW.scope_id) AND media_generation_allowed(NEW.scope_id)=0 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
DROP VIEW readable_events;
CREATE VIEW readable_events AS SELECT e.id,e.scope_id,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN '待更新事件' ELSE e.title END AS title,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN '' ELSE e.event_type END AS event_type,e.time_start,e.time_end,e.place,e.admin_district,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN '' ELSE e.activity END AS activity,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN '' ELSE e.summary END AS summary,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN 0 ELSE e.confidence END AS confidence,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN 0 ELSE e.aggregation_score END AS aggregation_score,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='events' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN '{}' ELSE e.aggregation_breakdown_json END AS aggregation_breakdown_json,e.status,e.revision,e.created_at,e.updated_at,e.source_type,e.source_asset_id,e.source_scene_index,e.source_start_sec,e.source_end_sec,e.source_metadata_json FROM events e WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',e.id) AND (validity!='valid')) AND NOT EXISTS (SELECT 1 FROM media_tombstones t WHERE (t.kind='event' AND t.object_id=e.id) OR (t.kind='asset' AND t.object_id=e.source_asset_id)) AND NOT EXISTS (SELECT 1 FROM event_observations eo JOIN media_tombstones t ON t.kind='observation' AND t.object_id=eo.observation_id WHERE eo.event_id=e.id AND NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='event_observations' AND ms.object_key=json_object('event_id',eo.event_id,'observation_id',eo.observation_id) AND (validity='revoked')));
DROP VIEW readable_entities;
CREATE VIEW readable_entities AS SELECT e.id,e.scope_id,e.entity_type,e.canonical_name,e.status,e.family_role,CASE WHEN EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='entities' AND ms.object_key=json_object('id',e.id) AND (summary_valid=0 OR EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='entities' AND mp.object_key=json_object('id',e.id) AND mp.generation!=se.generation))) THEN '' ELSE e.summary END AS summary,e.confidence,e.identity_state,e.role_state,e.name_state,e.created_at,e.updated_at FROM entities e WHERE 1=1;
DROP VIEW readable_semantic_profiles;
CREATE VIEW readable_semantic_profiles AS SELECT x.* FROM semantic_profiles x WHERE NOT EXISTS (
 SELECT 1 FROM json_each(x.evidence_ids_json) j JOIN media_tombstones t ON t.kind=CASE WHEN j.type='object' THEN json_extract(j.value,'$.kind') ELSE 'observation' END AND t.object_id=CASE WHEN j.type='object' THEN json_extract(j.value,'$.id') ELSE j.value END) AND NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='semantic_profiles' AND ms.object_key=json_object('id',x.id) AND (validity!='valid')) AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='semantic_profiles' AND mp.object_key=json_object('id',x.id) AND mp.generation!=se.generation);
DROP VIEW readable_person_portrait_revisions;
CREATE VIEW readable_person_portrait_revisions AS SELECT p.* FROM person_portrait_revisions p WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='person_portrait_revisions' AND ms.object_key=json_object('id',p.id) AND (validity!='valid')) AND NOT EXISTS (SELECT 1 FROM entity_mentions m JOIN observations o ON o.id=m.observation_id JOIN assets a ON a.id=o.asset_id WHERE m.entity_id=p.person_id AND a.deletion_state!='active') AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='person_portrait_revisions' AND mp.object_key=json_object('id',p.id) AND mp.generation!=se.generation);
DROP VIEW readable_family_person_portraits;
CREATE VIEW readable_family_person_portraits AS SELECT r.* FROM family_person_portraits r WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='family_person_portraits' AND ms.object_key=json_object('id',r.id) AND (validity!='valid')) AND NOT EXISTS (SELECT 1 FROM json_each(r.evidence_refs_json) j JOIN media_tombstones t ON t.kind='observation' AND t.object_id=j.value WHERE j.type='text') AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='family_person_portraits' AND mp.object_key=json_object('id',r.id) AND mp.generation!=se.generation);
DROP VIEW readable_memory_graph_nodes;
CREATE VIEW readable_memory_graph_nodes AS SELECT r.* FROM memory_graph_nodes r WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_graph_nodes' AND ms.object_key=json_object('id',r.id) AND (validity!='valid')) AND NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_graph_builds' AND ms.object_key=json_object('scope_id',r.scope_id) AND (validity!='valid')) AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='memory_graph_nodes' AND mp.object_key=json_object('id',r.id) AND mp.generation!=se.generation);
DROP VIEW readable_memory_graph_edges;
CREATE VIEW readable_memory_graph_edges AS SELECT r.* FROM memory_graph_edges r WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_graph_edges' AND ms.object_key=json_object('id',r.id) AND (validity!='valid')) AND NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_graph_builds' AND ms.object_key=json_object('scope_id',r.scope_id) AND (validity!='valid')) AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='memory_graph_edges' AND mp.object_key=json_object('id',r.id) AND mp.generation!=se.generation);
DROP VIEW readable_memory_graph_builds;
CREATE VIEW readable_memory_graph_builds AS SELECT r.* FROM memory_graph_builds r WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_graph_builds' AND ms.object_key=json_object('scope_id',r.scope_id) AND (validity!='valid')) AND NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_graph_builds' AND ms.object_key=json_object('scope_id',r.scope_id) AND (validity!='valid')) AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='memory_graph_builds' AND mp.object_key=json_object('scope_id',r.scope_id) AND mp.generation!=se.generation);
DROP VIEW readable_memory_rotation_sequences;
CREATE VIEW readable_memory_rotation_sequences AS SELECT r.* FROM memory_rotation_sequences r WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='memory_rotation_sequences' AND ms.object_key=json_object('sequence_id',r.sequence_id) AND (validity!='valid')) AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='memory_rotation_sequences' AND mp.object_key=json_object('sequence_id',r.sequence_id) AND mp.generation!=se.generation);

CREATE TRIGGER media_lifecycle_cleanup_failure AFTER UPDATE OF status ON media_cleanups
 WHEN NEW.status IN ('failed','blocked') BEGIN
 UPDATE media_deletion_lifecycle SET state=NEW.status,error=NEW.error,updated_at=NEW.updated_at
 WHERE deletion_id=NEW.deletion_id AND state!='completed';
END;
CREATE TABLE media_rebuild_index_cleanup (
 scope_id TEXT NOT NULL,generation INTEGER NOT NULL,resource_key TEXT NOT NULL,
 locator_json TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(scope_id,generation,resource_key)
);
DROP VIEW readable_text_vectors;
CREATE VIEW readable_text_vectors AS SELECT v.* FROM text_vectors v WHERE NOT EXISTS (
 SELECT 1 FROM media_tombstones t WHERE (t.kind=v.source_type AND t.object_id=v.source_id)
 OR (t.kind='asset' AND t.object_id=json_extract(v.metadata_json,'$.asset_id'))
 OR (t.kind='observation' AND t.object_id=json_extract(v.metadata_json,'$.observation_id')))
 AND NOT (v.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=v.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=v.source_id)) AND NOT (v.source_type='event' AND EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',v.source_id) AND (summary_valid=0 OR validity!='valid')))
AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id
WHERE v.source_type='event' AND mp.object_table='events' AND mp.object_key=json_object('id',v.source_id) AND mp.generation!=se.generation)
AND NOT (v.source_type='observation' AND json_extract(v.metadata_json,'$.source_revision') IS NOT NULL
AND json_extract(v.metadata_json,'$.source_revision')!=(SELECT revision FROM observations WHERE id=v.source_id));
DROP VIEW readable_memory_vectors;
CREATE VIEW readable_memory_vectors AS SELECT v.* FROM memory_vectors v WHERE NOT EXISTS (
 SELECT 1 FROM media_tombstones t WHERE (t.kind=v.source_type AND t.object_id=v.source_id)
 OR (t.kind='asset' AND t.object_id=json_extract(v.metadata_json,'$.asset_id'))
 OR (t.kind='observation' AND t.object_id=json_extract(v.metadata_json,'$.observation_id')))
 AND NOT (v.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=v.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=v.source_id)) AND NOT (v.source_type='event' AND EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='events' AND ms.object_key=json_object('id',v.source_id) AND (summary_valid=0 OR validity!='valid')))
AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id
WHERE v.source_type='event' AND mp.object_table='events' AND mp.object_key=json_object('id',v.source_id) AND mp.generation!=se.generation)
AND NOT (v.source_type='observation' AND json_extract(v.metadata_json,'$.source_revision') IS NOT NULL
AND json_extract(v.metadata_json,'$.source_revision')!=(SELECT revision FROM observations WHERE id=v.source_id));
CREATE TRIGGER media_current_events_summary AFTER UPDATE OF summary ON events
 WHEN media_generation_allowed(NEW.scope_id)=1 AND media_reconcile_allowed()=0 BEGIN
 UPDATE media_rebuild_publications SET generation=(SELECT generation FROM media_scope_epochs WHERE scope_id=NEW.scope_id)
 WHERE object_table='events' AND object_key=json_object('id',NEW.id);
 END;
CREATE TRIGGER media_current_entities_summary AFTER UPDATE OF summary ON entities
 WHEN media_generation_allowed(NEW.scope_id)=1 AND media_reconcile_allowed()=0 BEGIN
 UPDATE media_rebuild_publications SET generation=(SELECT generation FROM media_scope_epochs WHERE scope_id=NEW.scope_id)
 WHERE object_table='entities' AND object_key=json_object('id',NEW.id);
 END;

CREATE TRIGGER media_generation_moments_insert BEFORE INSERT ON person_moments
 WHEN EXISTS(SELECT 1 FROM media_scope_epochs WHERE scope_id=NEW.scope_id) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
CREATE TRIGGER media_generation_moments_update BEFORE UPDATE ON person_moments
 WHEN EXISTS(SELECT 1 FROM media_scope_epochs WHERE scope_id=NEW.scope_id) AND media_reconcile_allowed()=0 AND media_generation_allowed(NEW.scope_id)=0
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;

-- Historical failures and cleaning work retain an accurate additive lifecycle on upgrade.
UPDATE media_deletion_lifecycle SET state=CASE
 WHEN (SELECT status FROM media_cleanups c WHERE c.deletion_id=media_deletion_lifecycle.deletion_id) IN ('failed','blocked')
 THEN (SELECT status FROM media_cleanups c WHERE c.deletion_id=media_deletion_lifecycle.deletion_id)
 WHEN EXISTS(SELECT 1 FROM media_rebuild_members m WHERE m.deletion_id=media_deletion_lifecycle.deletion_id) THEN 'rebuilding'
 WHEN EXISTS(SELECT 1 FROM media_cleanups c WHERE c.deletion_id=media_deletion_lifecycle.deletion_id) THEN 'cleaning'
 WHEN EXISTS(SELECT 1 FROM media_reconciliations r WHERE r.deletion_id=media_deletion_lifecycle.deletion_id AND r.status='failed') THEN 'failed'
 ELSE state END;
DROP VIEW readable_person_moments;
CREATE VIEW readable_person_moments AS SELECT r.* FROM person_moments r JOIN readable_assets a ON a.id=r.asset_id JOIN readable_events e ON e.id=r.event_id WHERE NOT EXISTS (SELECT 1 FROM media_object_states ms WHERE ms.object_table='person_moments' AND ms.object_key=json_object('id',r.id) AND (validity!='valid')) AND NOT EXISTS(SELECT 1 FROM media_rebuild_publications mp JOIN media_scope_epochs se ON se.scope_id=mp.scope_id WHERE mp.object_table='person_moments' AND mp.object_key=json_object('id',r.id) AND mp.generation!=se.generation);
CREATE TRIGGER media_vector_revision_memory_vectors_insert BEFORE INSERT ON memory_vectors
 WHEN NEW.source_type IN ('observation','event') AND json_extract(NEW.metadata_json,'$.source_revision') IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM readable_observations o WHERE o.id=CASE WHEN NEW.source_type='observation' THEN NEW.source_id ELSE json_extract(NEW.metadata_json,'$.observation_id') END AND o.revision=json_extract(NEW.metadata_json,'$.source_revision'))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
 CREATE TRIGGER media_vector_generation_memory_vectors_insert BEFORE INSERT ON memory_vectors
 WHEN NEW.source_type IN ('observation','event') AND EXISTS(SELECT 1 FROM media_scope_epochs WHERE scope_id=NEW.scope_id)
 AND media_reconcile_allowed()=0 AND (CASE WHEN NEW.source_type='observation' THEN media_source_generation_allowed(NEW.scope_id) ELSE media_generation_allowed(NEW.scope_id) END)=0
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
CREATE TRIGGER media_vector_revision_memory_vectors_update BEFORE UPDATE ON memory_vectors
 WHEN NEW.source_type IN ('observation','event') AND json_extract(NEW.metadata_json,'$.source_revision') IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM readable_observations o WHERE o.id=CASE WHEN NEW.source_type='observation' THEN NEW.source_id ELSE json_extract(NEW.metadata_json,'$.observation_id') END AND o.revision=json_extract(NEW.metadata_json,'$.source_revision'))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
 CREATE TRIGGER media_vector_generation_memory_vectors_update BEFORE UPDATE ON memory_vectors
 WHEN NEW.source_type IN ('observation','event') AND EXISTS(SELECT 1 FROM media_scope_epochs WHERE scope_id=NEW.scope_id)
 AND media_reconcile_allowed()=0 AND (CASE WHEN NEW.source_type='observation' THEN media_source_generation_allowed(NEW.scope_id) ELSE media_generation_allowed(NEW.scope_id) END)=0
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
CREATE TRIGGER media_vector_revision_text_vectors_insert BEFORE INSERT ON text_vectors
 WHEN NEW.source_type IN ('observation','event') AND json_extract(NEW.metadata_json,'$.source_revision') IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM readable_observations o WHERE o.id=CASE WHEN NEW.source_type='observation' THEN NEW.source_id ELSE json_extract(NEW.metadata_json,'$.observation_id') END AND o.revision=json_extract(NEW.metadata_json,'$.source_revision'))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
 CREATE TRIGGER media_vector_generation_text_vectors_insert BEFORE INSERT ON text_vectors
 WHEN NEW.source_type IN ('observation','event') AND EXISTS(SELECT 1 FROM media_scope_epochs WHERE scope_id=NEW.scope_id)
 AND media_reconcile_allowed()=0 AND (CASE WHEN NEW.source_type='observation' THEN media_source_generation_allowed(NEW.scope_id) ELSE media_generation_allowed(NEW.scope_id) END)=0
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
CREATE TRIGGER media_vector_revision_text_vectors_update BEFORE UPDATE ON text_vectors
 WHEN NEW.source_type IN ('observation','event') AND json_extract(NEW.metadata_json,'$.source_revision') IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM readable_observations o WHERE o.id=CASE WHEN NEW.source_type='observation' THEN NEW.source_id ELSE json_extract(NEW.metadata_json,'$.observation_id') END AND o.revision=json_extract(NEW.metadata_json,'$.source_revision'))
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;
 CREATE TRIGGER media_vector_generation_text_vectors_update BEFORE UPDATE ON text_vectors
 WHEN NEW.source_type IN ('observation','event') AND EXISTS(SELECT 1 FROM media_scope_epochs WHERE scope_id=NEW.scope_id)
 AND media_reconcile_allowed()=0 AND (CASE WHEN NEW.source_type='observation' THEN media_source_generation_allowed(NEW.scope_id) ELSE media_generation_allowed(NEW.scope_id) END)=0
 BEGIN SELECT RAISE(ABORT,'media_isolated'); END;

-- Isolation only: original rows and files are retained.
ALTER TABLE assets ADD COLUMN deletion_state TEXT NOT NULL DEFAULT 'active' CHECK (deletion_state IN ('active', 'isolated'));
ALTER TABLE assets ADD COLUMN deletion_generation INTEGER NOT NULL DEFAULT 0;
CREATE TABLE media_deletions (
 deletion_id TEXT PRIMARY KEY,
 scope_id TEXT NOT NULL,
 root_asset_id TEXT NOT NULL UNIQUE REFERENCES assets(id),
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
 asset_id TEXT NOT NULL REFERENCES assets(id),
 generation INTEGER NOT NULL,
 PRIMARY KEY(deletion_id, asset_id)
);
CREATE INDEX idx_assets_deletion_state ON assets(deletion_state);
CREATE VIEW readable_assets AS SELECT * FROM assets WHERE deletion_state = 'active';
CREATE VIEW readable_observations AS SELECT o.* FROM observations o JOIN readable_assets a ON a.id=o.asset_id;
CREATE VIEW readable_face_instances AS SELECT f.* FROM face_instances f JOIN readable_assets a ON a.id=f.asset_id;
CREATE VIEW readable_events AS SELECT e.* FROM events e
 WHERE NOT EXISTS (SELECT 1 FROM assets a WHERE a.id=e.source_asset_id AND a.deletion_state!='active')
 AND NOT EXISTS (SELECT 1 FROM event_observations eo JOIN observations o ON o.id=eo.observation_id JOIN assets a ON a.id=o.asset_id WHERE eo.event_id=e.id AND a.deletion_state!='active');
CREATE VIEW readable_entity_mentions AS SELECT m.* FROM entity_mentions m JOIN readable_observations o ON o.id=m.observation_id;
CREATE VIEW readable_entity_observations AS SELECT m.* FROM entity_observations m JOIN readable_observations o ON o.id=m.observation_id;
CREATE VIEW readable_person_moments AS SELECT m.* FROM person_moments m JOIN readable_assets a ON a.id=m.asset_id JOIN readable_events e ON e.id=m.event_id;
CREATE VIEW readable_person_appearance_evidence AS SELECT m.* FROM person_appearance_evidence m JOIN readable_assets a ON a.id=m.asset_id;
CREATE VIEW readable_face_prototypes AS SELECT p.* FROM face_prototypes p JOIN readable_face_instances f ON f.id=p.face_instance_id;
CREATE VIEW readable_memory_vectors AS SELECT v.* FROM memory_vectors v WHERE NOT (EXISTS (SELECT 1 FROM assets a WHERE a.deletion_state!='active' AND (
 (v.source_type='asset' AND v.source_id=a.id) OR
 (v.source_type='observation' AND v.source_id IN (SELECT id FROM observations WHERE asset_id=a.id)) OR
 (v.source_type='face_instance' AND v.source_id IN (SELECT id FROM face_instances WHERE asset_id=a.id)))) OR
 (v.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=v.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=v.source_id)));
CREATE VIEW readable_text_vectors AS SELECT v.* FROM text_vectors v WHERE NOT (EXISTS (SELECT 1 FROM assets a WHERE a.deletion_state!='active' AND (
 (v.source_type='asset' AND v.source_id=a.id) OR
 (v.source_type='observation' AND v.source_id IN (SELECT id FROM observations WHERE asset_id=a.id)) OR
 (v.source_type='face_instance' AND v.source_id IN (SELECT id FROM face_instances WHERE asset_id=a.id)))) OR
 (v.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=v.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=v.source_id)));
CREATE VIEW readable_observation_search_terms AS SELECT v.* FROM observation_search_terms v WHERE NOT (EXISTS (SELECT 1 FROM assets a WHERE a.id=v.asset_id AND a.deletion_state!='active'));
CREATE TRIGGER isolation_assets_insert BEFORE INSERT ON assets WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.parent_asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_assets_update_OF_parent_asset_id BEFORE UPDATE OF parent_asset_id ON assets WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.parent_asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_assets_update_OF_status BEFORE UPDATE OF status, metadata_json, path, captured_at, gps_latitude, gps_longitude, capture_time_json ON assets WHEN (OLD.deletion_state!='active') BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_assets_monotonic BEFORE UPDATE OF deletion_state, deletion_generation ON assets
 WHEN (OLD.deletion_state='isolated' AND (NEW.deletion_state!='isolated' OR NEW.deletion_generation!=OLD.deletion_generation))
 OR (OLD.deletion_state='active' AND (NEW.deletion_state!='isolated' OR NEW.deletion_generation!=OLD.deletion_generation+1))
 BEGIN SELECT RAISE(ABORT, 'invalid_deletion_generation'); END;
CREATE TRIGGER isolation_observations_insert BEFORE INSERT ON observations WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_observations_update BEFORE UPDATE ON observations WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_face_instances_insert BEFORE INSERT ON face_instances WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active') OR EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_face_instances_update BEFORE UPDATE ON face_instances WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active') OR EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_asset_places_insert BEFORE INSERT ON asset_places WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_asset_places_update BEFORE UPDATE ON asset_places WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_entity_mentions_insert BEFORE INSERT ON entity_mentions WHEN (EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_entity_mentions_update BEFORE UPDATE ON entity_mentions WHEN (EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_entity_observations_insert BEFORE INSERT ON entity_observations WHEN (EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_entity_observations_update BEFORE UPDATE ON entity_observations WHEN (EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_person_appearance_evidence_insert BEFORE INSERT ON person_appearance_evidence WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_person_appearance_evidence_update BEFORE UPDATE ON person_appearance_evidence WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_person_moments_insert BEFORE INSERT ON person_moments WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_person_moments_update BEFORE UPDATE ON person_moments WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_event_observations_insert BEFORE INSERT ON event_observations WHEN (EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active') OR (EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.event_id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.event_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_event_observations_update BEFORE UPDATE ON event_observations WHEN (EXISTS (SELECT 1 FROM observations o JOIN assets a ON a.id=o.asset_id WHERE o.id=NEW.observation_id AND a.deletion_state!='active') OR (EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.event_id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.event_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_events_insert BEFORE INSERT ON events WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.source_asset_id AND a.deletion_state!='active') OR (EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_events_update BEFORE UPDATE ON events WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.source_asset_id AND a.deletion_state!='active') OR (EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_face_prototypes_insert BEFORE INSERT ON face_prototypes WHEN (EXISTS (SELECT 1 FROM face_instances f JOIN assets a ON a.id=f.asset_id WHERE f.id=NEW.face_instance_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_face_prototypes_update BEFORE UPDATE ON face_prototypes WHEN (EXISTS (SELECT 1 FROM face_instances f JOIN assets a ON a.id=f.asset_id WHERE f.id=NEW.face_instance_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_event_participants_insert BEFORE INSERT ON event_participants WHEN ((EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.event_id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.event_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_event_participants_update BEFORE UPDATE ON event_participants WHEN ((EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.event_id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.event_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_event_entities_insert BEFORE INSERT ON event_entities WHEN ((EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.event_id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.event_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_event_entities_update BEFORE UPDATE ON event_entities WHEN ((EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.event_id) AND NOT EXISTS (SELECT 1 FROM readable_events e WHERE e.id=NEW.event_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_observation_search_terms_insert BEFORE INSERT ON observation_search_terms WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_observation_search_terms_update BEFORE UPDATE ON observation_search_terms WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_vectors_insert BEFORE INSERT ON memory_vectors WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.deletion_state!='active' AND (
 (NEW.source_type='asset' AND NEW.source_id=a.id) OR
 (NEW.source_type='observation' AND NEW.source_id IN (SELECT id FROM observations WHERE asset_id=a.id)) OR
 (NEW.source_type='face_instance' AND NEW.source_id IN (SELECT id FROM face_instances WHERE asset_id=a.id)))) OR
 (NEW.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=NEW.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=NEW.source_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_vectors_update BEFORE UPDATE ON memory_vectors WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.deletion_state!='active' AND (
 (NEW.source_type='asset' AND NEW.source_id=a.id) OR
 (NEW.source_type='observation' AND NEW.source_id IN (SELECT id FROM observations WHERE asset_id=a.id)) OR
 (NEW.source_type='face_instance' AND NEW.source_id IN (SELECT id FROM face_instances WHERE asset_id=a.id)))) OR
 (NEW.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=NEW.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=NEW.source_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_text_vectors_insert BEFORE INSERT ON text_vectors WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.deletion_state!='active' AND (
 (NEW.source_type='asset' AND NEW.source_id=a.id) OR
 (NEW.source_type='observation' AND NEW.source_id IN (SELECT id FROM observations WHERE asset_id=a.id)) OR
 (NEW.source_type='face_instance' AND NEW.source_id IN (SELECT id FROM face_instances WHERE asset_id=a.id)))) OR
 (NEW.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=NEW.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=NEW.source_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_text_vectors_update BEFORE UPDATE ON text_vectors WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.deletion_state!='active' AND (
 (NEW.source_type='asset' AND NEW.source_id=a.id) OR
 (NEW.source_type='observation' AND NEW.source_id IN (SELECT id FROM observations WHERE asset_id=a.id)) OR
 (NEW.source_type='face_instance' AND NEW.source_id IN (SELECT id FROM face_instances WHERE asset_id=a.id)))) OR
 (NEW.source_type='event' AND EXISTS (SELECT 1 FROM events WHERE id=NEW.source_id) AND NOT EXISTS (SELECT 1 FROM readable_events WHERE id=NEW.source_id))) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_facts_insert BEFORE INSERT ON facts WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_facts_update BEFORE UPDATE ON facts WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_relationships_insert BEFORE INSERT ON relationships WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_relationships_update BEFORE UPDATE ON relationships WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_entity_properties_insert BEFORE INSERT ON entity_properties WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_entity_properties_update BEFORE UPDATE ON entity_properties WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_semantic_claims_insert BEFORE INSERT ON semantic_claims WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_semantic_claims_update BEFORE UPDATE ON semantic_claims WHEN (EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_person_portrait_revisions_insert BEFORE INSERT ON person_portrait_revisions WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_person_portrait_revisions_update BEFORE UPDATE ON person_portrait_revisions WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_semantic_profiles_insert BEFORE INSERT ON semantic_profiles WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_semantic_profiles_update BEFORE UPDATE ON semantic_profiles WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_person_portraits_insert BEFORE INSERT ON family_person_portraits WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_person_portraits_update BEFORE UPDATE ON family_person_portraits WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_analysis_runs_insert BEFORE INSERT ON family_analysis_runs WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_analysis_runs_update BEFORE UPDATE ON family_analysis_runs WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_graph_nodes_insert BEFORE INSERT ON memory_graph_nodes WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_graph_nodes_update BEFORE UPDATE ON memory_graph_nodes WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_graph_edges_insert BEFORE INSERT ON memory_graph_edges WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_graph_edges_update BEFORE UPDATE ON memory_graph_edges WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_graph_builds_insert BEFORE INSERT ON memory_graph_builds WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_memory_graph_builds_update BEFORE UPDATE ON memory_graph_builds WHEN (EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active')) BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE VIEW readable_facts AS SELECT t.* FROM facts t WHERE NOT EXISTS (SELECT 1 FROM json_each(t.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active');
CREATE VIEW readable_relationships AS SELECT t.* FROM relationships t WHERE NOT EXISTS (SELECT 1 FROM json_each(t.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active');
CREATE VIEW readable_entity_properties AS SELECT t.* FROM entity_properties t WHERE NOT EXISTS (SELECT 1 FROM json_each(t.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active');
CREATE VIEW readable_semantic_claims AS SELECT t.* FROM semantic_claims t WHERE NOT EXISTS (SELECT 1 FROM json_each(t.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active');
CREATE VIEW readable_semantic_profiles AS SELECT t.* FROM semantic_profiles t WHERE NOT EXISTS (SELECT 1 FROM json_each(t.evidence_ids_json) j JOIN observations o ON o.id=j.value JOIN assets a ON a.id=o.asset_id WHERE a.deletion_state!='active');
CREATE VIEW readable_person_portrait_revisions AS SELECT p.* FROM person_portrait_revisions p WHERE NOT EXISTS (
 SELECT 1 FROM entity_mentions m JOIN observations o ON o.id=m.observation_id JOIN assets a ON a.id=o.asset_id
 WHERE m.entity_id=p.person_id AND a.deletion_state!='active');
CREATE TRIGGER isolation_family_relationships_insert BEFORE INSERT ON family_relationships WHEN EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active') BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_relationships_update BEFORE UPDATE ON family_relationships WHEN EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active') BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_person_memberships_insert BEFORE INSERT ON family_person_memberships WHEN EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active') BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_family_person_memberships_update BEFORE UPDATE ON family_person_memberships WHEN EXISTS (SELECT 1 FROM assets a WHERE a.scope_id=NEW.scope_id AND a.deletion_state!='active') BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_video_job_claim BEFORE UPDATE OF status ON video_jobs
 WHEN NEW.status='PROCESSING' AND EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_import_job_claim BEFORE UPDATE OF status ON import_jobs
 WHEN NEW.status='running' AND EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_video_job_stale BEFORE UPDATE ON video_jobs
 WHEN NEW.status!='CANCELLED' AND EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_video_job_create BEFORE INSERT ON video_jobs
 WHEN EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_video_stage_stale BEFORE UPDATE ON video_job_stages
 WHEN EXISTS (SELECT 1 FROM video_jobs j JOIN assets a ON a.id=j.asset_id WHERE j.job_id=NEW.job_id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_import_job_stale BEFORE UPDATE ON import_jobs
 WHEN NEW.status!='cancelled' AND EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.asset_id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_asset_identity_insert BEFORE INSERT ON assets
 WHEN NEW.deletion_state!='active' OR NEW.deletion_generation!=0
 OR EXISTS (SELECT 1 FROM assets a WHERE a.id=NEW.id AND a.deletion_state!='active')
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_asset_identity_update BEFORE UPDATE OF parent_asset_id,scope_id,id ON assets
 WHEN OLD.deletion_state!='active'
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;
CREATE TRIGGER isolation_asset_delete BEFORE DELETE ON assets
 WHEN OLD.deletion_state!='active'
 BEGIN SELECT RAISE(ABORT, 'media_isolated'); END;

-- S2 / M3: 脸级描述与身份可信状态。
-- 复用 person_appearance_evidence 承接已归簇描述；未归簇描述走 memory_evidence
-- owner_kind='face_instance' 的暂存，不新建低质量人物。
ALTER TABLE person_appearance_evidence ADD COLUMN description_text TEXT NOT NULL DEFAULT '';
ALTER TABLE person_appearance_evidence ADD COLUMN source_build_id TEXT REFERENCES observation_builds(id);
ALTER TABLE person_appearance_evidence ADD COLUMN input_kind TEXT NOT NULL DEFAULT 'crop';
ALTER TABLE person_appearance_evidence ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE person_appearance_evidence ADD COLUMN description_state TEXT NOT NULL DEFAULT 'effective';
CREATE INDEX IF NOT EXISTS idx_person_appearance_descriptions
    ON person_appearance_evidence(person_id, description_state);

ALTER TABLE face_instances ADD COLUMN identity_state TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE face_instances ADD COLUMN identity_source TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE face_instances ADD COLUMN identity_score REAL NOT NULL DEFAULT 0;
ALTER TABLE face_instances ADD COLUMN identity_margin REAL NOT NULL DEFAULT 0;
ALTER TABLE face_instances ADD COLUMN rejected_entity_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_face_instances_identity_state
    ON face_instances(identity_state);

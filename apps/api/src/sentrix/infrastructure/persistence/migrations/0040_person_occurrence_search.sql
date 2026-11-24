-- S3: 人物出现词法投影、FTS 与重复人物建议决策。
-- 派生表与 observation_search_terms 同形：只索引当前有效描述、稳定标签/ID 与获准
-- 使用的姓名；不写每脸文本向量。MERGE 决策是负向/接受记录，特征或簇版本改变后
-- 才允许重新提示。
CREATE TABLE person_occurrence_search_terms (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    face_instance_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    person_id TEXT NOT NULL DEFAULT '',
    field_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    evidence_id TEXT NOT NULL DEFAULT '',
    source_revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_pot_face ON person_occurrence_search_terms(face_instance_id);
CREATE INDEX idx_pot_scope_field ON person_occurrence_search_terms(scope_id, field_type);
CREATE INDEX idx_pot_asset ON person_occurrence_search_terms(asset_id);
CREATE UNIQUE INDEX idx_pot_unique ON person_occurrence_search_terms(
    face_instance_id, field_type, normalized_value, evidence_id
);

CREATE VIRTUAL TABLE person_occurrence_search_fts USING fts5(
    tokens,
    term_id UNINDEXED,
    scope_id UNINDEXED,
    field_type UNINDEXED,
    face_instance_id UNINDEXED,
    asset_id UNINDEXED,
    person_id UNINDEXED,
    normalized_value UNINDEXED,
    evidence_id UNINDEXED,
    source_revision UNINDEXED
);

CREATE TABLE person_merge_suggestion_decisions (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    left_cluster_id TEXT NOT NULL,
    right_cluster_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    feature_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(scope_id, left_cluster_id, right_cluster_id)
);

-- S1 / M2: 单图精细字段与当前有效构建指针。
-- caption 保持原义；detail_caption 是新观察的详细描述，旧行留空并按 legacy 读取。
ALTER TABLE observations ADD COLUMN short_caption TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN detail_caption TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE observations ADD COLUMN entities_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE observations ADD COLUMN field_states_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE observations ADD COLUMN active_build_id TEXT REFERENCES observation_builds(id);
CREATE INDEX IF NOT EXISTS idx_observations_active_build ON observations(active_build_id);

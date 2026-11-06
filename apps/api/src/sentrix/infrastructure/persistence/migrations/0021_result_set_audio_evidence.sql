-- 冻结检索时实际交付给回答模型的音频锚点与相邻语境。
ALTER TABLE agent_result_sets ADD COLUMN audio_evidence_json TEXT NOT NULL DEFAULT '{}';

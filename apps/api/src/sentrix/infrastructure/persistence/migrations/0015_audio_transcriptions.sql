-- 音频 ASR 的权威证据与检索投影。
-- 原始/可疑转写单独保存，observations.transcript_text 只接收质量门禁放行的文本。
ALTER TABLE observations ADD COLUMN transcript_text TEXT NOT NULL DEFAULT '';

CREATE TABLE audio_transcriptions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    scope_id TEXT NOT NULL,
    status TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    config_hash TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    accepted_text TEXT NOT NULL DEFAULT '',
    quality_score REAL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(asset_id, model_name, model_version, config_hash)
);

CREATE INDEX idx_audio_transcriptions_asset
    ON audio_transcriptions(asset_id, updated_at DESC);
CREATE INDEX idx_audio_transcriptions_scope
    ON audio_transcriptions(scope_id, updated_at DESC);

CREATE TABLE audio_transcript_segments (
    id TEXT PRIMARY KEY,
    transcription_id TEXT NOT NULL REFERENCES audio_transcriptions(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    scope_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    normalized_text TEXT NOT NULL DEFAULT '',
    confidence_raw REAL,
    confidence_calibrated REAL,
    alignment_score REAL,
    quality_status TEXT NOT NULL,
    quality_reasons_json TEXT NOT NULL DEFAULT '[]',
    searchable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(transcription_id, ordinal)
);

CREATE INDEX idx_audio_segments_transcription
    ON audio_transcript_segments(transcription_id, ordinal);
CREATE INDEX idx_audio_segments_asset_time
    ON audio_transcript_segments(asset_id, start_ms, end_ms);
CREATE INDEX idx_audio_segments_searchable
    ON audio_transcript_segments(scope_id, searchable, quality_status);

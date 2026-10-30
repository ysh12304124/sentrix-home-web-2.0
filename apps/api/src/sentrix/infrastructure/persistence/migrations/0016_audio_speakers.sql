-- 单文件匿名说话人划分。标签来自 FunASR + CAM++，只在一次媒体转写内有意义；
-- 不把它冒充成跨媒体稳定身份或已确认人物。
ALTER TABLE audio_transcript_segments
ADD COLUMN local_speaker_label TEXT NOT NULL DEFAULT '';

ALTER TABLE audio_transcript_segments
ADD COLUMN speaker_confidence REAL;

CREATE INDEX idx_audio_segments_local_speaker
    ON audio_transcript_segments(asset_id, local_speaker_label, start_ms);

"""音频转写权威仓储。原始证据与质量判断在同一事务内落库。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.media.transcription import (
    AudioTranscription,
    AudioTranscriptSegment,
    SegmentQualityStatus,
    TranscriptionStatus,
)
from sentrix.infrastructure.persistence.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteAudioTranscriptionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save(
        self,
        transcription: AudioTranscription,
        segments: Sequence[AudioTranscriptSegment],
    ) -> None:
        timestamp = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO audio_transcriptions (id, asset_id, scope_id, status, model_name,"
                " model_version, config_hash, language, raw_text, accepted_text, quality_score,"
                " error_message, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(asset_id, model_name, model_version, config_hash) DO UPDATE SET"
                " status = excluded.status, language = excluded.language, raw_text = excluded.raw_text,"
                " accepted_text = excluded.accepted_text, quality_score = excluded.quality_score,"
                " error_message = excluded.error_message, updated_at = excluded.updated_at",
                (
                    transcription.id,
                    transcription.asset_id,
                    transcription.scope_id,
                    transcription.status.value,
                    transcription.model_name,
                    transcription.model_version,
                    transcription.config_hash,
                    transcription.language,
                    transcription.raw_text,
                    transcription.accepted_text,
                    transcription.quality_score,
                    transcription.error_message,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT id FROM audio_transcriptions WHERE asset_id = ? AND model_name = ?"
                " AND model_version = ? AND config_hash = ?",
                (
                    transcription.asset_id,
                    transcription.model_name,
                    transcription.model_version,
                    transcription.config_hash,
                ),
            ).fetchone()
            stored_id = str(row["id"])
            connection.execute(
                "DELETE FROM audio_transcript_segments WHERE transcription_id = ?", (stored_id,)
            )
            for segment in segments:
                connection.execute(
                    "INSERT INTO audio_transcript_segments (id, transcription_id, asset_id, scope_id,"
                    " ordinal, start_ms, end_ms, raw_text, normalized_text, confidence_raw,"
                    " confidence_calibrated, alignment_score, quality_status, quality_reasons_json,"
                    " searchable, local_speaker_label, speaker_confidence, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        segment.id,
                        stored_id,
                        segment.asset_id,
                        segment.scope_id,
                        segment.ordinal,
                        segment.start_ms,
                        segment.end_ms,
                        segment.raw_text,
                        segment.normalized_text,
                        segment.confidence_raw,
                        segment.confidence_calibrated,
                        segment.alignment_score,
                        segment.quality_status.value,
                        json.dumps(list(segment.quality_reasons), ensure_ascii=False),
                        int(segment.searchable),
                        segment.local_speaker_label,
                        segment.speaker_confidence,
                        timestamp,
                        timestamp,
                    ),
                )

    def latest_for_asset(self, asset_id: str) -> AudioTranscription | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, asset_id, scope_id, status, model_name, model_version, config_hash,"
                " language, raw_text, accepted_text, quality_score, error_message"
                " FROM audio_transcriptions WHERE asset_id = ? ORDER BY updated_at DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        return AudioTranscription(
            id=row["id"],
            asset_id=row["asset_id"],
            scope_id=row["scope_id"],
            status=TranscriptionStatus(row["status"]),
            model_name=row["model_name"],
            model_version=row["model_version"],
            config_hash=row["config_hash"],
            language=row["language"],
            raw_text=row["raw_text"],
            accepted_text=row["accepted_text"],
            quality_score=row["quality_score"],
            error_message=row["error_message"],
        )

    def list_segments(self, transcription_id: str) -> Sequence[AudioTranscriptSegment]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, transcription_id, asset_id, scope_id, ordinal, start_ms, end_ms,"
                " raw_text, normalized_text, confidence_raw, confidence_calibrated, alignment_score,"
                " quality_status, quality_reasons_json, searchable"
                ", local_speaker_label, speaker_confidence"
                " FROM audio_transcript_segments WHERE transcription_id = ? ORDER BY ordinal",
                (transcription_id,),
            ).fetchall()
        return tuple(self._segment(row) for row in rows)

    @staticmethod
    def _segment(row: sqlite3.Row) -> AudioTranscriptSegment:
        try:
            reasons = json.loads(row["quality_reasons_json"] or "[]")
        except json.JSONDecodeError:
            reasons = []
        return AudioTranscriptSegment(
            id=row["id"],
            transcription_id=row["transcription_id"],
            asset_id=row["asset_id"],
            scope_id=row["scope_id"],
            ordinal=row["ordinal"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            raw_text=row["raw_text"],
            normalized_text=row["normalized_text"],
            confidence_raw=row["confidence_raw"],
            confidence_calibrated=row["confidence_calibrated"],
            alignment_score=row["alignment_score"],
            quality_status=SegmentQualityStatus(row["quality_status"]),
            quality_reasons=tuple(str(item) for item in reasons),
            searchable=bool(row["searchable"]),
            local_speaker_label=row["local_speaker_label"] or "",
            speaker_confidence=row["speaker_confidence"],
        )

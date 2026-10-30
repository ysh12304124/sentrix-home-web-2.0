from datetime import UTC, datetime

from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.transcription import (
    AudioTranscription,
    AudioTranscriptSegment,
    SegmentQualityStatus,
    TranscriptionStatus,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.audio_transcriptions import (
    SqliteAudioTranscriptionRepository,
)


def test_raw_and_accepted_transcripts_are_stored_separately(migrations_ready_db) -> None:
    now = datetime.now(UTC)
    asset = Asset(
        id="asset_audio",
        scope_id="album1",
        file_name="memo.m4a",
        media_type=MediaType.AUDIO,
        path="/tmp/memo.m4a",
        mime_type="audio/mp4",
        size_bytes=10,
        status=AssetStatus.PROCESSED,
        content_sha256="abc",
        captured_at=now,
        captured_location=None,
        created_at=now,
        updated_at=now,
    )
    assets = SqliteAssetRepository(migrations_ready_db)
    observations = SqliteObservationRepository(migrations_ready_db)
    assets.add(asset)
    observations.add(
        Observation(
            id="obs_audio",
            scope_id="album1",
            asset_id=asset.id,
            source_type="import",
            captured_at=now,
            confidence=0.0,
            created_at=now,
            updated_at=now,
        )
    )

    repository = SqliteAudioTranscriptionRepository(migrations_ready_db)
    transcription = AudioTranscription(
        id="atr_1",
        asset_id=asset.id,
        scope_id="album1",
        status=TranscriptionStatus.REVIEW,
        model_name="paraformer-zh",
        model_version="1",
        config_hash="cfg",
        language="zh",
        raw_text="可信句子 含糊句子",
        accepted_text="可信句子",
    )
    repository.save(
        transcription,
        (
            AudioTranscriptSegment(
                id="seg_1",
                transcription_id=transcription.id,
                asset_id=asset.id,
                scope_id="album1",
                ordinal=0,
                start_ms=0,
                end_ms=1000,
                raw_text="可信句子",
                normalized_text="可信句子",
                confidence_raw=0.95,
                confidence_calibrated=None,
                alignment_score=None,
                quality_status=SegmentQualityStatus.ACCEPTED,
                quality_reasons=(),
                searchable=True,
                local_speaker_label="0",
                speaker_confidence=0.93,
            ),
            AudioTranscriptSegment(
                id="seg_2",
                transcription_id=transcription.id,
                asset_id=asset.id,
                scope_id="album1",
                ordinal=1,
                start_ms=1000,
                end_ms=2000,
                raw_text="含糊句子",
                normalized_text="含糊句子",
                confidence_raw=0.2,
                confidence_calibrated=None,
                alignment_score=None,
                quality_status=SegmentQualityStatus.UNCERTAIN,
                quality_reasons=("low_confidence",),
                searchable=False,
            ),
        ),
    )

    stored = repository.latest_for_asset(asset.id)
    assert stored is not None
    assert stored.raw_text == "可信句子 含糊句子"
    assert stored.accepted_text == "可信句子"
    segments = repository.list_segments(stored.id)
    assert [item.searchable for item in segments] == [True, False]
    assert segments[1].quality_reasons == ("low_confidence",)
    assert segments[0].local_speaker_label == "0"
    assert segments[0].speaker_confidence == 0.93

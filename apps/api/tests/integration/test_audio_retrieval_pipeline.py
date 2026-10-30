"""不启动 HTTP/前端，直接验证音频文本从门禁到检索的完整后端链路。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.media.transcribe_audio import TranscribeAudio
from sentrix.application.retrieval.index_audio_transcript import IndexAudioTranscriptSegments
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn, TextSearchRequest
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.transcription import AsrResult, AsrSegment, SegmentQualityStatus
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.audio_transcriptions import (
    SqliteAudioTranscriptionRepository,
)
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository


class FixedAsr:
    available = True
    load_error = ""

    def __init__(self, *, text: str, confidence: float) -> None:
        self._text = text
        self._confidence = confidence

    def transcribe(self, path: Path) -> AsrResult:
        return AsrResult(
            model_name="fake-paraformer",
            model_version="1",
            config_hash="pipeline-test",
            language="zh",
            raw_text=self._text,
            segments=(
                AsrSegment(
                    1200,
                    3200,
                    self._text,
                    confidence_raw=self._confidence,
                    local_speaker_label="0",
                    speaker_confidence=0.88,
                ),
            ),
        )


class FixedTextEmbedder:
    model_id = scheme.TEXT_MODEL
    dimension = 3

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0] if text.strip() else []


def _seed_audio(database: Database, *, asset_id: str) -> tuple[Asset, Observation]:
    now = datetime.now(UTC)
    asset = Asset(
        id=asset_id,
        scope_id="album1",
        file_name=f"{asset_id}.m4a",
        media_type=MediaType.AUDIO,
        path=f"/tmp/{asset_id}.m4a",
        mime_type="audio/mp4",
        size_bytes=100,
        status=AssetStatus.PROCESSED,
        content_sha256=f"sha-{asset_id}",
        captured_at=now,
        captured_location=None,
        created_at=now,
        updated_at=now,
    )
    observation = Observation(
        id=f"obs-{asset_id}",
        scope_id="album1",
        asset_id=asset.id,
        source_type="import",
        captured_at=now,
        confidence=0.0,
        created_at=now,
        updated_at=now,
    )
    SqliteAssetRepository(database).add(asset)
    SqliteObservationRepository(database).add(observation)
    return asset, observation


def _run_pipeline(
    database: Database, *, asset_id: str, text: str, confidence: float
) -> tuple[TranscribeAudio, Observation, SqliteLexicalIndex, RetrieveTextAnn]:
    asset, observation = _seed_audio(database, asset_id=asset_id)
    observations = SqliteObservationRepository(database)
    transcriptions = SqliteAudioTranscriptionRepository(database)
    vectors = SqliteVectorRepository(database)
    embedder = FixedTextEmbedder()
    use_case = TranscribeAudio(
        transcriber=FixedAsr(text=text, confidence=confidence),
        transcriptions=transcriptions,
        observations=observations,
        segment_indexer=IndexAudioTranscriptSegments(embedder=embedder, vectors=vectors).index,
    )

    prepared = use_case.prepare(asset)
    enriched = use_case.commit(observation, prepared)
    IndexObservationText(embedder=embedder, vectors=vectors).index(enriched, None)
    lexical = SqliteLexicalIndex(
        database,
        observations,
        assets=SqliteAssetRepository(database),
        places=SqliteAssetPlaceRepository(database),
    )
    lexical.refresh_from_observation(enriched)
    return use_case, enriched, lexical, RetrieveTextAnn(embedder=embedder, vectors=vectors)


def test_accepted_audio_reaches_lexical_and_vector_retrieval_with_timestamp(
    migrations_ready_db: Database,
) -> None:
    use_case, observation, lexical, retrieval = _run_pipeline(
        migrations_ready_db,
        asset_id="audio-ok",
        text="今天去公园散步",
        confidence=0.95,
    )

    transcription, segments = use_case.latest("audio-ok")
    assert transcription is not None
    assert transcription.raw_text == "今天去公园散步"
    assert transcription.accepted_text == "今天去公园散步"
    assert observation.transcript_text == "今天去公园散步"
    assert segments[0].quality_status is SegmentQualityStatus.ACCEPTED
    assert segments[0].local_speaker_label == "0"
    assert segments[0].speaker_confidence == 0.88
    assert [asset_id for asset_id, _hits in lexical.search_fts("公园散步", scope_id="album1", limit=10)] == [
        "audio-ok"
    ]

    result = retrieval.execute(TextSearchRequest(text="公园散步", scope_id="album1"))
    assert [item.asset_id for item in result.hits] == ["audio-ok"]
    assert result.hits[0].start_ms == 1200
    assert result.hits[0].end_ms == 3200


def test_uncertain_audio_is_auditable_but_absent_from_both_indexes(
    migrations_ready_db: Database,
) -> None:
    use_case, observation, lexical, retrieval = _run_pipeline(
        migrations_ready_db,
        asset_id="audio-uncertain",
        text="火星污染测试词",
        confidence=0.1,
    )

    transcription, segments = use_case.latest("audio-uncertain")
    assert transcription is not None
    assert transcription.raw_text == "火星污染测试词"
    assert transcription.accepted_text == ""
    assert observation.transcript_text == ""
    assert segments[0].quality_status is SegmentQualityStatus.UNCERTAIN
    assert segments[0].searchable is False
    assert lexical.search_fts("火星污染测试词", scope_id="album1", limit=10) == []
    assert retrieval.execute(TextSearchRequest(text="火星污染测试词")).hits == ()

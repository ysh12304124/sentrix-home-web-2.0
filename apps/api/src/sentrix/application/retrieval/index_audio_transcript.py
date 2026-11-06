"""把通过门禁的音频分段写入现有文本向量空间。"""

from __future__ import annotations

from collections.abc import Sequence

from sentrix.domain.media.transcription import AudioTranscriptSegment
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import TextEmbedder, VectorRecord, VectorRepository

AUDIO_SEGMENT_SOURCE = "audio_segment"


class IndexAudioTranscriptSegments:
    def __init__(self, *, embedder: TextEmbedder, vectors: VectorRepository) -> None:
        self._embedder = embedder
        self._vectors = vectors

    def index(self, asset_id: str, segments: Sequence[AudioTranscriptSegment]) -> int:
        # 修订切换前先撤掉旧分段；否则换模型后新旧两套向量会同时参与召回。
        self._vectors.delete_for_asset(source_type=AUDIO_SEGMENT_SOURCE, asset_id=asset_id)
        rows = 0
        for segment in segments:
            if not segment.searchable or not segment.normalized_text:
                continue
            vector = self._embedder.embed_query(segment.normalized_text)
            if not vector or len(vector) != self._embedder.dimension:
                continue
            self._vectors.upsert(
                VectorRecord(
                    scope_id=segment.scope_id,
                    space=scheme.SEMANTIC_SPACE,
                    source_type=AUDIO_SEGMENT_SOURCE,
                    source_id=segment.id,
                    model_name=self._embedder.model_id,
                    vector=tuple(float(value) for value in vector),
                    metadata={
                        "asset_id": segment.asset_id,
                        "transcription_id": segment.transcription_id,
                        "start_ms": segment.start_ms,
                        "end_ms": segment.end_ms,
                        "confidence": segment.confidence_calibrated
                        if segment.confidence_calibrated is not None
                        else segment.confidence_raw,
                        "local_speaker_label": segment.local_speaker_label,
                        "speaker_confidence": segment.speaker_confidence,
                    },
                )
            )
            rows += 1
        return rows

from __future__ import annotations

from sentrix.application.retrieval.retrieve_audio_segments import (
    AudioSegmentSearchRequest,
    RetrieveAudioSegments,
)
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorHit


class Embedder:
    model_id = scheme.TEXT_MODEL
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class Vectors:
    def search_exact(self, **_: object) -> list[VectorHit]:
        return [
            VectorHit("obs", "s", "observation", scheme.TEXT_MODEL, 0.99, {"asset_id": "frame"}),
            VectorHit("seg-a", "s", "audio_segment", scheme.TEXT_MODEL, 0.9, {"asset_id": "video", "start_ms": 100, "end_ms": 200}),
            VectorHit("seg-b", "s", "audio_segment", scheme.TEXT_MODEL, 0.8, {"asset_id": "video", "start_ms": 300, "end_ms": 400}),
        ]


def test_keeps_two_segments_for_the_same_asset() -> None:
    result = RetrieveAudioSegments(embedder=Embedder(), vectors=Vectors()).execute(
        AudioSegmentSearchRequest(text="咖啡", limit=2)
    )
    assert [hit.segment_id for hit in result.hits] == ["seg-a", "seg-b"]
    assert [hit.asset_id for hit in result.hits] == ["video", "video"]

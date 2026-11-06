from __future__ import annotations

from dataclasses import replace

from sentrix.application.retrieval.expand_audio_context import ExpandAudioContext
from sentrix.domain.media.transcription import (
    AudioTranscription,
    AudioTranscriptSegment,
    SegmentQualityStatus,
    TranscriptionStatus,
)


class FakeTranscriptions:
    def __init__(self, segments: tuple[AudioTranscriptSegment, ...]) -> None:
        self.segments = segments
        self.transcription = AudioTranscription(
            id="tx_1",
            asset_id="asset_1",
            scope_id="scope_1",
            status=TranscriptionStatus.COMPLETED,
            model_name="whisper",
            model_version="1",
            config_hash="cfg",
            language="zh",
            raw_text="",
            accepted_text="",
        )

    def latest_for_asset(self, asset_id: str) -> AudioTranscription | None:
        return self.transcription if asset_id == "asset_1" else None

    def list_segments(self, transcription_id: str) -> tuple[AudioTranscriptSegment, ...]:
        return self.segments if transcription_id == "tx_1" else ()


def _segment(ordinal: int, start_ms: int, end_ms: int, text: str) -> AudioTranscriptSegment:
    return AudioTranscriptSegment(
        id=f"seg_{ordinal}",
        transcription_id="tx_1",
        asset_id="asset_1",
        scope_id="scope_1",
        ordinal=ordinal,
        start_ms=start_ms,
        end_ms=end_ms,
        raw_text=text,
        normalized_text=text,
        confidence_raw=0.9,
        confidence_calibrated=None,
        alignment_score=None,
        quality_status=SegmentQualityStatus.ACCEPTED,
        quality_reasons=(),
        searchable=True,
        local_speaker_label="speaker_0",
    )


def test_expands_one_neighbor_on_each_side_without_changing_anchor() -> None:
    segments = (
        _segment(0, 0, 2_000, "前一句"),
        _segment(1, 2_500, 5_000, "决定性实体"),
        _segment(2, 5_200, 8_000, "后一句"),
    )

    evidence = ExpandAudioContext(FakeTranscriptions(segments)).execute(
        asset_id="asset_1", anchor_start_ms=2_500, anchor_end_ms=5_000
    )

    assert [item.text for item in evidence] == ["前一句", "决定性实体", "后一句"]
    assert [item.is_anchor for item in evidence] == [False, True, False]
    assert evidence[1].local_speaker_label == "speaker_0"


def test_does_not_bridge_large_gap_or_include_unsearchable_segment() -> None:
    previous = replace(_segment(0, 0, 1_000, "不可靠"), searchable=False)
    segments = (
        previous,
        _segment(1, 7_000, 9_000, "锚点"),
        _segment(2, 15_000, 17_000, "太远"),
    )

    evidence = ExpandAudioContext(FakeTranscriptions(segments)).execute(
        asset_id="asset_1", anchor_start_ms=7_000, anchor_end_ms=9_000
    )

    assert [(item.text, item.is_anchor) for item in evidence] == [("锚点", True)]


def test_requires_the_persisted_timestamp_to_match_or_overlap() -> None:
    evidence = ExpandAudioContext(FakeTranscriptions((_segment(0, 10_000, 12_000, "一句"),))).execute(
        asset_id="asset_1", anchor_start_ms=20_000, anchor_end_ms=21_000
    )

    assert evidence == ()

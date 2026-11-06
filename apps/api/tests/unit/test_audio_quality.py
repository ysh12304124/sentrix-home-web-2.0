from sentrix.domain.media.transcription import (
    AsrSegment,
    AudioQualityGate,
    SegmentQualityStatus,
    normalize_transcript_text,
)


def test_clear_segment_is_accepted() -> None:
    decision = AudioQualityGate().assess(AsrSegment(0, 1800, "今天去公园散步。", confidence_raw=0.92))

    assert decision.status is SegmentQualityStatus.ACCEPTED
    assert decision.searchable is True


def test_low_confidence_segment_is_quarantined() -> None:
    decision = AudioQualityGate().assess(AsrSegment(0, 1800, "今天去公园散步。", confidence_raw=0.20))

    assert decision.status is SegmentQualityStatus.UNCERTAIN
    assert decision.searchable is False
    assert "low_confidence" in decision.reasons


def test_missing_acoustic_scores_are_visible_even_when_structural_gate_accepts() -> None:
    decision = AudioQualityGate().assess(AsrSegment(0, 1800, "今天去公园散步。"))

    assert decision.status is SegmentQualityStatus.ACCEPTED
    assert decision.searchable is True
    assert decision.reasons == ("no_acoustic_score",)


def test_empty_or_control_only_segment_is_rejected() -> None:
    decision = AudioQualityGate().assess(AsrSegment(0, 1000, "<|nospeech|>"))

    assert decision.status is SegmentQualityStatus.REJECTED
    assert decision.searchable is False


def test_normalization_does_not_rewrite_meaning() -> None:
    assert normalize_transcript_text("  <|zh|> 今天\n 去公园  ") == "今天 去公园"

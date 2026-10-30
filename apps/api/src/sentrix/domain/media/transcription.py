"""音频转写的领域模型与质量门禁。纯数据、纯规则、零 IO。

ASR 原始结果是证据，检索文本只是从证据生成的投影。这个模块刻意不出现
FunASR/SenseVoice 等 SDK 类型，换模型时只需要替换 infrastructure 适配器。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class TranscriptionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    REVIEW = "review"
    FAILED = "failed"


class SegmentQualityStatus(StrEnum):
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"
    HUMAN_CORRECTED = "human_corrected"


@dataclass(frozen=True, slots=True)
class AsrSegment:
    """ASR 适配器返回的一段原始识别结果。时间统一为毫秒。"""

    start_ms: int
    end_ms: int
    text: str
    confidence_raw: float | None = None
    alignment_score: float | None = None
    speech_probability: float | None = None
    language_probability: float | None = None
    # FunASR + CAM++ 在单个媒体文件内生成的匿名说话人标签（例如 ``0`` / ``1``）。
    # 它不是跨文件稳定身份，更不是已确认人物 id。
    local_speaker_label: str = ""
    speaker_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class AsrResult:
    """一次模型调用的输出。模型与配置身份必须随结果一起返回。"""

    model_name: str
    model_version: str
    config_hash: str
    language: str
    raw_text: str
    segments: tuple[AsrSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioTranscriptSegment:
    id: str
    transcription_id: str
    asset_id: str
    scope_id: str
    ordinal: int
    start_ms: int
    end_ms: int
    raw_text: str
    normalized_text: str
    confidence_raw: float | None
    confidence_calibrated: float | None
    alignment_score: float | None
    quality_status: SegmentQualityStatus
    quality_reasons: tuple[str, ...]
    searchable: bool
    # 与 AsrSegment 同口径：只表示当前录音内的匿名说话人划分。
    local_speaker_label: str = ""
    speaker_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class AudioEvidenceExcerpt:
    """送入回答模型的一段音频证据。

    ``is_anchor`` 区分真正由向量检索命中的片段与仅用于补足语境的相邻片段；相邻片段
    不参与召回打分，也不能反过来抬高所属资产的排名。
    """

    start_ms: int
    end_ms: int
    text: str
    is_anchor: bool = False
    local_speaker_label: str = ""


@dataclass(frozen=True, slots=True)
class AudioTranscription:
    id: str
    asset_id: str
    scope_id: str
    status: TranscriptionStatus
    model_name: str
    model_version: str
    config_hash: str
    language: str
    raw_text: str
    accepted_text: str
    quality_score: float | None = None
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class SegmentQualityDecision:
    status: SegmentQualityStatus
    normalized_text: str
    reasons: tuple[str, ...] = ()

    @property
    def searchable(self) -> bool:
        return self.status in {
            SegmentQualityStatus.ACCEPTED,
            SegmentQualityStatus.HUMAN_CORRECTED,
        }


@dataclass(frozen=True, slots=True)
class AudioQualityGate:
    """第一版保守门禁。

    confidence/alignment 等分数只有模型真的返回时才参与；缺失分数不会伪装成
    “已校准”。这些初始阈值只负责拦明显异常，后续要用家庭录音验证集重新校准。
    """

    version: str = "audio-gate-v1"
    min_segment_ms: int = 300
    max_chars_per_second: float = 24.0
    max_invalid_ratio: float = 0.20
    max_repeated_ratio: float = 0.60
    min_confidence: float = 0.45
    min_alignment: float = 0.45
    min_speech_probability: float = 0.50
    min_language_probability: float = 0.50

    def assess(self, segment: AsrSegment) -> SegmentQualityDecision:
        text = normalize_transcript_text(segment.text)
        if not text:
            return SegmentQualityDecision(SegmentQualityStatus.REJECTED, text, ("empty_text",))
        if segment.start_ms < 0 or segment.end_ms <= segment.start_ms:
            return SegmentQualityDecision(SegmentQualityStatus.REJECTED, text, ("invalid_timing",))

        duration_ms = segment.end_ms - segment.start_ms
        hard: list[str] = []
        soft: list[str] = []
        if duration_ms < self.min_segment_ms:
            soft.append("segment_too_short")

        content = [char for char in text if not char.isspace() and not _is_punctuation(char)]
        if not content:
            return SegmentQualityDecision(SegmentQualityStatus.REJECTED, text, ("punctuation_only",))
        invalid_ratio = sum(not _is_language_character(char) for char in content) / len(content)
        if invalid_ratio > 0.50:
            hard.append("mostly_invalid_characters")
        elif invalid_ratio > self.max_invalid_ratio:
            soft.append("many_invalid_characters")

        chars_per_second = len(content) / max(duration_ms / 1000.0, 0.001)
        if chars_per_second > self.max_chars_per_second * 2:
            hard.append("impossible_text_rate")
        elif chars_per_second > self.max_chars_per_second:
            soft.append("high_text_rate")

        repeated_ratio = _repeated_bigram_ratio("".join(content))
        if repeated_ratio > 0.85:
            hard.append("severe_repetition")
        elif repeated_ratio > self.max_repeated_ratio:
            soft.append("repeated_text")

        _score_reason(segment.confidence_raw, self.min_confidence, "low_confidence", soft)
        _score_reason(segment.alignment_score, self.min_alignment, "low_alignment", soft)
        _score_reason(
            segment.speech_probability,
            self.min_speech_probability,
            "low_speech_probability",
            soft,
        )
        _score_reason(
            segment.language_probability,
            self.min_language_probability,
            "language_mismatch",
            soft,
        )

        if hard:
            return SegmentQualityDecision(SegmentQualityStatus.REJECTED, text, tuple(hard + soft))
        if soft:
            return SegmentQualityDecision(SegmentQualityStatus.UNCERTAIN, text, tuple(soft))
        if all(
            value is None
            for value in (
                segment.confidence_raw,
                segment.alignment_score,
                segment.speech_probability,
                segment.language_probability,
            )
        ):
            # Paraformer 的常见输出没有稳定可用的置信度。第一版为了可用性仍允许通过
            # 结构门禁的文本进入检索，但把证据不足显式留在段上，不能把它当成高置信。
            return SegmentQualityDecision(
                SegmentQualityStatus.ACCEPTED,
                text,
                ("no_acoustic_score",),
            )
        return SegmentQualityDecision(SegmentQualityStatus.ACCEPTED, text)


_SPACE_RE = re.compile(r"\s+")
_CONTROL_TAG_RE = re.compile(r"<\s*\|[^<>]*?\|\s*>")


def normalize_transcript_text(text: str) -> str:
    """只做确定性清洗，不做 LLM 纠错或语义改写。"""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _CONTROL_TAG_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char).startswith(("P", "S"))


def _is_language_character(char: str) -> bool:
    category = unicodedata.category(char)
    return category.startswith(("L", "N"))


def _repeated_bigram_ratio(text: str) -> float:
    if len(text) < 6:
        return 0.0
    bigrams = [text[index : index + 2] for index in range(len(text) - 1)]
    return 1.0 - (len(set(bigrams)) / len(bigrams))


def _score_reason(value: float | None, threshold: float, reason: str, reasons: list[str]) -> None:
    if value is not None and value < threshold:
        reasons.append(reason)

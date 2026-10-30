"""音频转写：调用 ASR、逐段门禁、保存证据、生成可信文本投影。"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sentrix.domain.media.models import Asset, MediaType, Observation
from sentrix.domain.media.ports import (
    AudioTranscriber,
    AudioTranscriptionRepository,
    ObservationRepository,
)
from sentrix.domain.media.transcription import (
    AsrResult,
    AudioQualityGate,
    AudioTranscription,
    AudioTranscriptSegment,
    SegmentQualityStatus,
    TranscriptionStatus,
)

log = logging.getLogger("sentrix.media.transcribe_audio")


@dataclass(frozen=True, slots=True)
class PreparedAudioTranscription:
    transcription: AudioTranscription
    segments: tuple[AudioTranscriptSegment, ...] = ()


class TranscribeAudio:
    """两段式用例：prepare 只推理，commit 才写权威表和检索投影。"""

    def __init__(
        self,
        *,
        transcriber: AudioTranscriber,
        transcriptions: AudioTranscriptionRepository,
        observations: ObservationRepository,
        segment_indexer: Callable[[str, Sequence[AudioTranscriptSegment]], int] | None = None,
        gate: AudioQualityGate | None = None,
    ) -> None:
        self._transcriber = transcriber
        self._transcriptions = transcriptions
        self._observations = observations
        self._segment_indexer = segment_indexer
        self._gate = gate or AudioQualityGate()

    def prepare(self, asset: Asset) -> PreparedAudioTranscription | None:
        # FunASR sidecar 的标准化入口接收的是「包含音轨的媒体文件」：它会用
        # ``ffmpeg -vn`` 从视频里取默认音轨，再统一成 16 kHz 单声道 WAV。因此视频
        # 不需要先伪装成一条独立音频资产；证据直接绑定源视频 asset_id，检索命中后
        # 才能原样返回视频以及分段时间戳。
        if asset.media_type not in (MediaType.AUDIO, MediaType.VIDEO):
            return None
        try:
            result = self._transcriber.transcribe(Path(asset.path))
        except Exception as error:  # noqa: BLE001 - ASR 是增强，源媒体资产仍应完成导入
            message = f"{type(error).__name__}: {error}"
            log.warning("媒体音轨转写失败 asset=%s: %s", asset.id, message)
            identity = hashlib.sha256(self._gate.version.encode("utf-8")).hexdigest()[:16]
            return PreparedAudioTranscription(
                AudioTranscription(
                    id=_stable_id(asset.id, "funasr-sidecar", "", identity),
                    asset_id=asset.id,
                    scope_id=asset.scope_id,
                    status=TranscriptionStatus.FAILED,
                    model_name="funasr-sidecar",
                    model_version="",
                    config_hash=identity,
                    language="",
                    raw_text="",
                    accepted_text="",
                    error_message=message,
                )
            )
        return self._evaluate(asset, result)

    def commit(
        self,
        observation: Observation,
        prepared: PreparedAudioTranscription | None,
    ) -> Observation:
        if prepared is None:
            return observation
        self._transcriptions.save(prepared.transcription, prepared.segments)
        if prepared.transcription.status is TranscriptionStatus.FAILED:
            return observation

        enriched = self._observations.enrich_transcript(
            observation.id,
            transcript_text=prepared.transcription.accepted_text,
        )
        if self._segment_indexer is not None:
            self._segment_indexer(observation.asset_id, prepared.segments)
        return enriched

    def latest(self, asset_id: str) -> tuple[AudioTranscription | None, Sequence[AudioTranscriptSegment]]:
        """读回最新证据；供诊断/API 使用，不从 Observation 反推原始 ASR。"""
        transcription = self._transcriptions.latest_for_asset(asset_id)
        if transcription is None:
            return None, ()
        return transcription, self._transcriptions.list_segments(transcription.id)

    def _evaluate(self, asset: Asset, result: AsrResult) -> PreparedAudioTranscription:
        config_hash = hashlib.sha256(f"{result.config_hash}:{self._gate.version}".encode()).hexdigest()[:16]
        transcription_id = _stable_id(asset.id, result.model_name, result.model_version, config_hash)
        segments: list[AudioTranscriptSegment] = []
        accepted: list[str] = []
        for ordinal, source in enumerate(result.segments):
            decision = self._gate.assess(source)
            if decision.searchable:
                accepted.append(decision.normalized_text)
            segments.append(
                AudioTranscriptSegment(
                    id=f"aseg_{uuid.uuid5(uuid.NAMESPACE_URL, f'{transcription_id}:{ordinal}').hex[:16]}",
                    transcription_id=transcription_id,
                    asset_id=asset.id,
                    scope_id=asset.scope_id,
                    ordinal=ordinal,
                    start_ms=source.start_ms,
                    end_ms=source.end_ms,
                    raw_text=source.text,
                    normalized_text=decision.normalized_text,
                    confidence_raw=source.confidence_raw,
                    # 没有项目验证集前不冒充“已校准分数”。
                    confidence_calibrated=None,
                    alignment_score=source.alignment_score,
                    quality_status=decision.status,
                    quality_reasons=decision.reasons,
                    searchable=decision.searchable,
                    local_speaker_label=source.local_speaker_label,
                    speaker_confidence=source.speaker_confidence,
                )
            )

        needs_review = bool(not result.segments and result.raw_text.strip()) or any(
            item.quality_status is SegmentQualityStatus.UNCERTAIN for item in segments
        )
        status = TranscriptionStatus.REVIEW if needs_review else TranscriptionStatus.COMPLETED
        transcription = AudioTranscription(
            id=transcription_id,
            asset_id=asset.id,
            scope_id=asset.scope_id,
            status=status,
            model_name=result.model_name,
            model_version=result.model_version,
            config_hash=config_hash,
            language=result.language,
            raw_text=result.raw_text,
            accepted_text=" ".join(accepted).strip(),
            quality_score=None,
        )
        return PreparedAudioTranscription(transcription, tuple(segments))


def _stable_id(asset_id: str, model: str, version: str, config_hash: str) -> str:
    key = f"{asset_id}:{model}:{version}:{config_hash}"
    return f"atr_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:16]}"

"""在音频向量命中之后，为最终锚点补一段相邻转写语境。"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.domain.media.ports import AudioTranscriptionRepository
from sentrix.domain.media.transcription import AudioEvidenceExcerpt, AudioTranscriptSegment


@dataclass(frozen=True, slots=True)
class ExpandAudioContext:
    """只扩展最终交付的锚点，不参与召回、融合或重排。"""

    transcriptions: AudioTranscriptionRepository
    neighbor_count: int = 1
    max_gap_ms: int = 5_000

    def execute(
        self,
        *,
        asset_id: str,
        anchor_start_ms: int,
        anchor_end_ms: int,
    ) -> tuple[AudioEvidenceExcerpt, ...]:
        transcription = self.transcriptions.latest_for_asset(asset_id)
        if transcription is None:
            return ()
        segments = tuple(
            segment
            for segment in self.transcriptions.list_segments(transcription.id)
            if segment.searchable and segment.normalized_text.strip()
        )
        if not segments:
            return ()
        anchor_index = _anchor_index(segments, anchor_start_ms, anchor_end_ms)
        if anchor_index is None:
            return ()

        selected: list[tuple[AudioTranscriptSegment, bool]] = [(segments[anchor_index], True)]
        previous = segments[anchor_index]
        for index in range(anchor_index - 1, max(-1, anchor_index - self.neighbor_count - 1), -1):
            candidate = segments[index]
            if previous.start_ms - candidate.end_ms > self.max_gap_ms:
                break
            selected.insert(0, (candidate, False))
            previous = candidate

        following = segments[anchor_index]
        for index in range(anchor_index + 1, min(len(segments), anchor_index + self.neighbor_count + 1)):
            candidate = segments[index]
            if candidate.start_ms - following.end_ms > self.max_gap_ms:
                break
            selected.append((candidate, False))
            following = candidate

        return tuple(
            AudioEvidenceExcerpt(
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.normalized_text,
                is_anchor=is_anchor,
                local_speaker_label=segment.local_speaker_label,
            )
            for segment, is_anchor in selected
        )


def _anchor_index(segments: tuple[AudioTranscriptSegment, ...], start_ms: int, end_ms: int) -> int | None:
    exact = next(
        (
            index
            for index, segment in enumerate(segments)
            if segment.start_ms == start_ms and segment.end_ms == end_ms
        ),
        None,
    )
    if exact is not None:
        return exact
    overlapping = [
        (index, min(segment.end_ms, end_ms) - max(segment.start_ms, start_ms))
        for index, segment in enumerate(segments)
        if min(segment.end_ms, end_ms) > max(segment.start_ms, start_ms)
    ]
    if overlapping:
        return max(overlapping, key=lambda item: item[1])[0]
    return None

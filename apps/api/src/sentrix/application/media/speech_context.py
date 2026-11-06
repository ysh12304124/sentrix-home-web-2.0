"""Build bounded ASR context for a timestamped video frame.

This sits after retrieval: it never changes ranking and only expands a matched
speech anchor by its immediately adjacent segments when they are continuous.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def expand_frame_speech_context(
    segments: Sequence[Mapping[str, object]],
    *,
    frame_time_sec: float,
    neighbor_count: int = 1,
    max_gap_sec: float = 5.0,
) -> tuple[dict[str, object], ...]:
    """Return chronological ``previous + anchor + next`` evidence.

    The anchor is a segment containing the frame timestamp.  Adjacent segments
    are added only while their gap remains bounded, preventing unrelated
    dialogue from another scene from becoming answer evidence.
    """
    normalized = tuple(_normalize(segment) for segment in segments)
    normalized = tuple(item for item in normalized if item is not None)
    anchors = [
        index
        for index, item in enumerate(normalized)
        if item["start_sec"] <= frame_time_sec <= item["end_sec"]
    ]
    if not anchors:
        return ()

    selected: dict[int, bool] = {index: True for index in anchors}
    for anchor in anchors:
        previous = normalized[anchor]
        for index in range(anchor - 1, max(-1, anchor - neighbor_count - 1), -1):
            candidate = normalized[index]
            if float(previous["start_sec"]) - float(candidate["end_sec"]) > max_gap_sec:
                break
            selected.setdefault(index, False)
            previous = candidate
        following = normalized[anchor]
        for index in range(anchor + 1, min(len(normalized), anchor + neighbor_count + 1)):
            candidate = normalized[index]
            if float(candidate["start_sec"]) - float(following["end_sec"]) > max_gap_sec:
                break
            selected.setdefault(index, False)
            following = candidate

    return tuple({**normalized[index], "is_anchor": selected[index]} for index in sorted(selected))


def _normalize(segment: Mapping[str, object]) -> dict[str, object] | None:
    text = str(segment.get("text") or "").strip()
    if not text:
        return None
    try:
        start_sec = float(segment.get("start_sec", 0.0))
        end_sec = float(segment.get("end_sec", start_sec))
    except (TypeError, ValueError):
        return None
    if end_sec < start_sec:
        return None
    return {
        "start_sec": start_sec,
        "end_sec": end_sec,
        "text": text[:1000],
        "speaker": str(segment.get("speaker") or ""),
    }

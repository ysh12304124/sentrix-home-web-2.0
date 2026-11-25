"""Build one source-video memory from timestamped visual and speech evidence."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from itertools import groupby
from pathlib import Path
from typing import Any

from sentrix.domain.media.models import Asset, VideoMetadata
from sentrix.domain.media.ports import TranscriptionSegment, VideoGroupObserver, VideoRepository

log = logging.getLogger(__name__)
PROCESSING_VERSION = "video-memory-v2"


def build_video_memory(
    video: Asset,
    frames: Sequence[tuple[float, int, Asset]],
    segments: Sequence[TranscriptionSegment],
    metadata: VideoMetadata | None,
    audio_status: str,
    observer: VideoGroupObserver,
    repository: VideoRepository,
) -> dict[str, Any]:
    groups = []
    evidence: list[tuple[float, str, str]] = []
    ordered_frames = sorted(frames, key=lambda frame: frame[0])
    selections = groupby(ordered_frames, key=lambda frame: frame[1])
    for index, (selection_group, members) in enumerate(selections, start=1):
        batch = [(seconds, frame) for seconds, _, frame in members]
        group_id = f"{video.id}:group:{index}"
        records = [
            {"frame_id": frame.id, "video_id": video.id, "frame_path": frame.path, "timestamp_sec": seconds}
            for seconds, frame in batch
        ]
        signature = [[frame.id, frame.content_sha256, seconds] for seconds, frame in batch]
        group = repository.read_video_memory_record(video.id, group_id)
        if (
            not group
            or group.get("signature") != signature
            or group.get("status") != "complete"
            or group.get("selection_group") != selection_group
        ):
            details = observer.observe_group([(Path(frame.path), seconds) for seconds, frame in batch])
            group = {
                "group_id": group_id,
                "group_index": index,
                "selection_group": selection_group,
                "video_id": video.id,
                "frame_ids": [record["frame_id"] for record in records],
                "frames": records,
                "first_frame_sec": batch[0][0],
                "last_frame_sec": batch[-1][0],
                "signature": signature,
                "status": "complete" if details else "failed",
                **(details or {}),
            }
            repository.save_video_memory_record(video.id, group_id, group)
        if group["status"] == "complete":
            evidence.append((batch[0][0], group_id, f"画面：{group['caption']}"))
        else:
            log.warning("video group observation failed video_id=%s group_index=%d", video.id, index)
        groups.append(group)

    audio = None
    speech: list[dict[str, Any]] = []
    audio_offset = (metadata.audio_source_start_sec if metadata else None) or 0.0
    stream_index = metadata.audio_stream_index if metadata else None
    if stream_index is not None or segments:
        audio_id = f"{video.id}:audio:{stream_index if stream_index is not None else 0}"
        audio = {
            "audio_id": audio_id,
            "video_id": video.id,
            "memory_id": video.id,
            "audio_path": video.path,
            "stream_index": stream_index,
            "source_start_sec": audio_offset,
            "duration": metadata.audio_duration_sec if metadata else None,
        }
        for index, segment in enumerate(sorted(segments, key=lambda item: (item.start_sec, item.end_sec)), 1):
            segment_id = f"{audio_id}:segment:{index}"
            video_start = segment.start_sec + audio_offset
            speech.append(
                {
                    "segment_id": segment_id,
                    "audio_id": audio_id,
                    "video_id": video.id,
                    "memory_id": video.id,
                    "start_sec": segment.start_sec,
                    "end_sec": segment.end_sec,
                    "source_start_sec": audio_offset,
                    "video_start_sec": video_start,
                    "video_end_sec": segment.end_sec + audio_offset,
                    "text": segment.text,
                    "speaker_label": segment.speaker or None,
                }
            )
            speaker = f"{segment.speaker}：" if segment.speaker else ""
            evidence.append((video_start, segment_id, f"语音转写：{speaker}{segment.text}"))
    evidence.sort(key=lambda item: item[0])
    complete_groups = [group for group in groups if group["status"] == "complete"]
    memory = {
        "id": video.id,
        "type": "video",
        "source_file": video.file_name,
        "timestamp": video.captured_at.isoformat() if video.captured_at else None,
        "duration": metadata.duration_sec if metadata else None,
        "processing_version": PROCESSING_VERSION,
        "audio_status": audio_status,
        "status": "complete" if len(complete_groups) == len(groups) else "partial",
        "groups": groups,
        "audio_track": audio,
        "segments": speech,
        "source_group_ids": [group["group_id"] for group in complete_groups],
        "source_segment_ids": [segment["segment_id"] for segment in speech],
        "evidence_refs": [
            {"video_id": video.id, "record_id": record_id, "timestamp_sec": seconds, "text": content}
            for seconds, record_id, content in evidence
        ],
        "short_caption": next(
            (group["short_caption"] for group in complete_groups if group["short_caption"]), ""
        ),
        "caption": "\n".join(f"[{seconds:.3f}s] {content}" for seconds, _, content in evidence),
        "transcript": "\n".join(segment["text"] for segment in speech),
        "ocr_text": "\n".join(
            dict.fromkeys(group["ocr_text"] for group in complete_groups if group["ocr_text"])
        ),
        "tags": list(dict.fromkeys(tag for group in complete_groups for tag in group["tags"])),
        "entities": list(dict.fromkeys(entity for group in complete_groups for entity in group["entities"])),
    }
    repository.save_video_memory_record(video.id, video.id, memory)
    return memory

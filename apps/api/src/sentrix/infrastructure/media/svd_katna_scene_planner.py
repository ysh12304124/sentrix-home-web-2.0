"""10fps SVD rank-energy and similarity deduplication followed by Katna.

The planner is deliberately self-contained: it decodes the source video once,
keeps the exact first and last decoded frames, folds adjacent near-duplicates
using low-rank descriptors, and passes the surviving image arrays directly to
Katna's image selector. The returned timestamps become normal Web2 scene
windows, so the existing asset, event, and memory pipelines remain unchanged.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import NotRequired, TypedDict

import cv2
import numpy as np
from Katna.image_selector import ImageSelector

from sentrix.domain.media.scenes import SceneWindow


class FrameCandidate(TypedDict):
    frame_index: int
    timestamp_sec: float
    image: np.ndarray
    candidate_index: NotRequired[int]
    endpoint_anchor: NotRequired[bool]


SAMPLE_FPS = 10.0
SVD_ENERGY_THRESHOLD = 0.50
SIMILARITY_THRESHOLD = 0.90
KATNA_FRAME_BUDGET = 4
MAX_FRAME_SIDE = 640
FEATURE_SIZE = (32, 18)


def _feature(image: np.ndarray) -> np.ndarray:
    """Match the compact grayscale-plus-color descriptor used in the experiment."""
    small = cv2.resize(image, (160, 90), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    compact = cv2.resize(gray, FEATURE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    color = small.reshape(-1, 3).mean(axis=0).astype(np.float32) / 255.0
    return np.concatenate([compact.reshape(-1), color])


def _thumbnail(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


def _sample_video(
    source: Path, duration_sec: float
) -> tuple[list[tuple[float, np.ndarray]], dict[str, object], float]:
    started = time.perf_counter()
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {source}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if source_fps <= 0 or source_width <= 0 or source_height <= 0:
        capture.release()
        raise RuntimeError(f"video has invalid stream metadata: {source}")

    scale = min(1.0, MAX_FRAME_SIDE / max(source_width, source_height))
    width = max(2, int(round(source_width * scale / 2) * 2))
    height = max(2, int(round(source_height * scale / 2) * 2))
    duration = float(duration_sec)
    frames: list[tuple[float, np.ndarray]] = []
    sampled_indices: list[int] = []
    last_frame: np.ndarray | None = None
    last_frame_index = -1
    next_tick = 0
    index = 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        last_frame = image.copy()
        last_frame_index = index
        source_time = index / source_fps
        target_time = next_tick / SAMPLE_FPS
        if source_time + (0.5 / max(source_fps, 1.0)) >= target_time and target_time < duration:
            if (image.shape[1], image.shape[0]) != (width, height):
                image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
            frames.append((source_time, image))
            sampled_indices.append(index)
            next_tick += 1
        index += 1
    capture.release()
    if not frames or last_frame is None:
        raise RuntimeError(f"video contains no decodable frames: {source}")

    # The first 10fps sample is the exact first decoded frame. Add the exact
    # final decoded frame when the last regular tick did not land on it.
    tail_added = sampled_indices[-1] != last_frame_index
    actual_last_timestamp = last_frame_index / source_fps
    if tail_added:
        if (last_frame.shape[1], last_frame.shape[0]) != (width, height):
            last_frame = cv2.resize(last_frame, (width, height), interpolation=cv2.INTER_AREA)
        frames.append((actual_last_timestamp, last_frame))
    return (
        frames,
        {
            "source_fps": source_fps,
            "duration_sec": duration,
            "decoded_frame_count": index,
            "sampled_frame_count": len(frames) - int(tail_added),
            "tail_anchor_added": tail_added,
            "source_last_frame_timestamp_sec": actual_last_timestamp,
            "source_resolution": [source_width, source_height],
            "algorithm_resolution": [width, height],
        },
        time.perf_counter() - started,
    )


def _deduplicate(
    frames: list[tuple[float, np.ndarray]],
) -> tuple[list[FrameCandidate], dict[str, object]]:
    features = np.vstack([_feature(image) for _, image in frames]).astype(np.float64)
    centered = features - features.mean(axis=0, keepdims=True)
    left, singular_values, right = np.linalg.svd(centered, full_matrices=False)
    energy = np.square(singular_values)
    cumulative = np.cumsum(energy) / max(float(energy.sum()), 1e-12)
    rank = min(int(np.searchsorted(cumulative, SVD_ENERGY_THRESHOLD) + 1), len(singular_values))
    low_rank = (left[:, :rank] * singular_values[:rank]) @ right[:rank, :]
    norms = np.linalg.norm(low_rank, axis=1, keepdims=True)
    descriptors = low_rank / np.maximum(norms, 1e-10)
    sharpness = [float(cv2.Laplacian(image, cv2.CV_64F).var()) for _, image in frames]

    kept = [0]
    representative = 0
    for index in range(1, max(1, len(frames) - 1)):
        similarity = float(np.dot(descriptors[index], descriptors[representative]))
        if similarity >= SIMILARITY_THRESHOLD:
            # The source first frame is a protected anchor and cannot be
            # replaced by a sharper frame from its duplicate run.
            if representative != 0 and sharpness[index] > sharpness[representative]:
                kept[-1] = index
                representative = index
        else:
            kept.append(index)
            representative = index
    if len(frames) > 1:
        kept.append(len(frames) - 1)

    candidates: list[FrameCandidate] = [
        {"frame_index": index, "timestamp_sec": frames[index][0], "image": frames[index][1]} for index in kept
    ]
    return candidates, {
        "svd_rank": rank,
        "svd_achieved_energy": float(cumulative[rank - 1]),
        "candidate_count_before_dedup": len(frames),
        "candidate_count_after_dedup": len(candidates),
        "dedup_reduction_ratio": 1.0 - len(candidates) / len(frames),
    }


def _katna_select(candidates: list[FrameCandidate]) -> list[FrameCandidate]:
    if len(candidates) <= 1:
        return list(candidates)
    selected_images = ImageSelector(n_processes=1).select_best_frames(
        [item["image"] for item in candidates], min(KATNA_FRAME_BUDGET, len(candidates))
    )
    selected: list[FrameCandidate] = []
    thumbs = [_thumbnail(item["image"]) for item in candidates]
    for image in selected_images:
        target = _thumbnail(image)
        index = min(range(len(candidates)), key=lambda i: float(np.mean((thumbs[i] - target) ** 2)))
        if all(abs(index - int(item["candidate_index"])) > 1 for item in selected):
            selected.append({**candidates[index], "candidate_index": index})
    return selected


def _scene_windows(selected: list[FrameCandidate], duration_sec: float) -> tuple[SceneWindow, ...]:
    ordered = sorted(selected, key=lambda item: float(item["timestamp_sec"]))
    unique: list[FrameCandidate] = []
    for item in ordered:
        if unique and abs(float(item["timestamp_sec"]) - float(unique[-1]["timestamp_sec"])) < 1e-6:
            continue
        unique.append(item)
    timestamps = [float(item["timestamp_sec"]) for item in unique]
    windows: list[SceneWindow] = []
    for index, _item in enumerate(unique):
        start = 0.0 if index == 0 else (timestamps[index - 1] + timestamps[index]) / 2
        end = (
            float(duration_sec)
            if index == len(unique) - 1
            else (timestamps[index] + timestamps[index + 1]) / 2
        )
        windows.append(
            SceneWindow(
                index=index,
                start_sec=start,
                end_sec=max(start, end),
                selected_timestamp_sec=timestamps[index],
            )
        )
    return tuple(windows)


class SVDKatnaScenePlanner:
    """Build memory scene windows from the selected 10fps SVD50/S90→Katna algorithm."""

    def __init__(self, output_root: Path) -> None:
        self._output_root = Path(output_root).expanduser().resolve()

    def __call__(self, video_path: Path, video_id: str, duration_sec: float) -> tuple[SceneWindow, ...]:
        started = time.perf_counter()
        frames, sampling, decode_seconds = _sample_video(Path(video_path), duration_sec)
        svd_started = time.perf_counter()
        candidates, svd_metrics = _deduplicate(frames)
        svd_dedup_seconds = time.perf_counter() - svd_started
        katna_started = time.perf_counter()
        selected = _katna_select(candidates)
        katna_seconds = time.perf_counter() - katna_started
        katna_selected_count = len(selected)
        scene_window_started = time.perf_counter()

        # Katna ranks the SVD survivors. Source endpoints remain mandatory even
        # when Katna did not choose them.
        for endpoint_index in sorted({0, len(frames) - 1}):
            timestamp, image = frames[endpoint_index]
            if not any(abs(float(item["timestamp_sec"]) - timestamp) < 1e-6 for item in selected):
                selected.append(
                    {
                        "frame_index": endpoint_index,
                        "timestamp_sec": timestamp,
                        "image": image,
                        "endpoint_anchor": True,
                    }
                )

        windows = _scene_windows(selected, duration_sec)
        scene_window_seconds = time.perf_counter() - scene_window_started
        elapsed = time.perf_counter() - started
        output = self._output_root / video_id
        output.mkdir(parents=True, exist_ok=True)
        (output / "analysis.json").write_text(
            json.dumps(
                {
                    "algorithm": "svd_energy50_similarity90_katna_direct_images",
                    "parameters": {
                        "fps": SAMPLE_FPS,
                        "svd_energy_threshold": SVD_ENERGY_THRESHOLD,
                        "similarity_threshold": SIMILARITY_THRESHOLD,
                        "katna_frame_budget": KATNA_FRAME_BUDGET,
                        "max_frame_side": MAX_FRAME_SIDE,
                        "first_and_last_frame_preserved": True,
                    },
                    "sampling": sampling,
                    "svd": svd_metrics,
                    "katna": {
                        "input_count": len(candidates),
                        "selected_count": katna_selected_count,
                        "seconds": katna_seconds,
                    },
                    "timings": {
                        "decode_seconds": round(decode_seconds, 3),
                        "svd_dedup_seconds": round(svd_dedup_seconds, 3),
                        "katna_seconds": round(katna_seconds, 3),
                        "scene_window_seconds": round(scene_window_seconds, 3),
                    },
                    "final_selected_count_including_endpoints": len(selected),
                    "selected_timestamps_sec": [window.keyframe_timestamp_sec for window in windows],
                    "runtime_seconds": elapsed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not windows:
            raise RuntimeError(f"SVD/Katna selected no keyframes for {video_path}")
        return windows

    def stage_timings(self, video_id: str) -> dict[str, float]:
        """Return measured planner stages for the video just planned."""
        path = self._output_root / video_id / "analysis.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            timings = payload.get("timings", {}) if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(timings, dict):
            return {}
        return {str(key): float(value) for key, value in timings.items() if isinstance(value, (int, float))}

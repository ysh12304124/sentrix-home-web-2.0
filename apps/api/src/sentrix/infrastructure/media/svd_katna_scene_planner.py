"""Shot-boundary detection followed by adaptive per-shot Katna selection.

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


def _svd_representation(
    frames: list[tuple[float, np.ndarray]],
) -> dict[str, object]:
    """Return the compact low-rank representation shared by dedup and planning."""
    features = np.vstack([_feature(image) for _, image in frames]).astype(np.float64)
    centered = features - features.mean(axis=0, keepdims=True)
    left, singular_values, right = np.linalg.svd(centered, full_matrices=False)
    energy = np.square(singular_values)
    cumulative = np.cumsum(energy) / max(float(energy.sum()), 1e-12)
    rank = min(int(np.searchsorted(cumulative, SVD_ENERGY_THRESHOLD) + 1), len(singular_values))
    low_rank = (left[:, :rank] * singular_values[:rank]) @ right[:rank, :]
    norms = np.linalg.norm(low_rank, axis=1, keepdims=True)
    descriptors = low_rank / np.maximum(norms, 1e-10)
    return {
        "features": features,
        "centered": centered,
        "singular_values": singular_values,
        "energy": energy,
        "cumulative_energy": cumulative,
        "rank": rank,
        "low_rank": low_rank,
        "descriptors": descriptors,
    }


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
    representation: dict[str, object] | None = None,
) -> tuple[list[FrameCandidate], dict[str, object]]:
    representation = representation or _svd_representation(frames)
    rank = int(representation["rank"])
    cumulative = np.asarray(representation["cumulative_energy"])
    descriptors = np.asarray(representation["descriptors"])
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


def _katna_select(candidates: list[FrameCandidate], requested_budget: int) -> list[FrameCandidate]:
    if not candidates:
        return []
    budget = min(max(1, int(requested_budget)), len(candidates))
    selected_images = ImageSelector(n_processes=1).select_best_frames(
        [item["image"] for item in candidates], budget
    )
    selected: list[FrameCandidate] = []
    thumbs = [_thumbnail(item["image"]) for item in candidates]
    for image in selected_images:
        target = _thumbnail(image)
        index = min(range(len(candidates)), key=lambda i: float(np.mean((thumbs[i] - target) ** 2)))
        # Samples are 10fps. Keep at least half a second between frames picked
        # from the same segment so Katna cannot spend its budget on near twins.
        if all(abs(index - int(item["candidate_index"])) > 4 for item in selected):
            selected.append({**candidates[index], "candidate_index": index})
    return selected


def _shot_groups(
    frames: list[tuple[float, np.ndarray]],
    candidates: list[FrameCandidate],
    representation: dict[str, object],
) -> tuple[list[list[FrameCandidate]], list[dict[str, float | int | bool]], dict[str, float]]:
    """Detect shot cuts from adjacent-frame appearance discontinuities.

    This follows the public behavior described for Jianying/CapCut scene split:
    identify visual scene changes, then treat each shot independently. It does
    not impose an arbitrary maximum shot duration or total keyframe count.
    """
    if len(frames) <= 2:
        return [candidates], [], {}

    low_rank = np.asarray(representation["low_rank"])
    descriptors = np.asarray(representation["descriptors"])
    transitions: list[dict[str, float | int | bool]] = []
    for left_index in range(len(frames) - 1):
        right_index = left_index + 1
        left_time, left_image = frames[left_index]
        right_time, right_image = frames[right_index]
        similarity = float(np.clip(np.dot(descriptors[left_index], descriptors[right_index]), -1.0, 1.0))
        energy = float(
            np.linalg.norm(low_rank[right_index] - low_rank[left_index]) / np.sqrt(max(1, low_rank.shape[1]))
        )
        small_left = cv2.resize(left_image, (96, 54), interpolation=cv2.INTER_AREA)
        small_right = cv2.resize(right_image, (96, 54), interpolation=cv2.INTER_AREA)
        gray_left = cv2.cvtColor(small_left, cv2.COLOR_BGR2GRAY)
        gray_right = cv2.cvtColor(small_right, cv2.COLOR_BGR2GRAY)
        pixel_difference = float(cv2.absdiff(gray_left, gray_right).mean() / 255.0)
        hsv_left = cv2.cvtColor(small_left, cv2.COLOR_BGR2HSV)
        hsv_right = cv2.cvtColor(small_right, cv2.COLOR_BGR2HSV)
        hist_left = cv2.calcHist([hsv_left], [0, 1], None, [16, 8], [0, 180, 0, 256])
        hist_right = cv2.calcHist([hsv_right], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist_left, hist_left)
        cv2.normalize(hist_right, hist_right)
        histogram_distance = float(cv2.compareHist(hist_left, hist_right, cv2.HISTCMP_BHATTACHARYYA))
        transitions.append(
            {
                "left_index": left_index,
                "right_index": left_index + 1,
                "timestamp_sec": float((left_time + right_time) / 2),
                "similarity": similarity,
                "novelty": max(0.0, 1.0 - similarity),
                "energy": energy,
                "pixel_difference": pixel_difference,
                "histogram_distance": histogram_distance,
            }
        )

    positive_energy = [float(item["energy"]) for item in transitions if float(item["energy"]) > 0]
    energy_scale = float(np.quantile(positive_energy, 0.90)) if positive_energy else 1.0
    for item in transitions:
        energy_norm = min(1.0, float(item["energy"]) / max(energy_scale, 1e-9))
        novelty_norm = min(1.0, float(item["novelty"]) / 0.25)
        item["energy_norm"] = energy_norm
        item["change_score"] = 0.5 * novelty_norm + 0.5 * energy_norm
    # A cut is a large appearance discontinuity. Pixel and color evidence are
    # both retained so the decision can be inspected and tuned per video.
    # The low thresholds catch hard cuts; histogram-only cuts catch similar
    # geometry with a major palette change. Nearby detections are coalesced.
    raw_cuts = [
        item
        for item in transitions
        if (
            (float(item["histogram_distance"]) >= 0.22 and float(item["pixel_difference"]) >= 0.045)
            or float(item["histogram_distance"]) >= 0.30
            or (float(item["pixel_difference"]) >= 0.15 and float(item["novelty"]) >= 0.4)
        )
    ]
    cuts: list[dict[str, float | int | bool]] = []
    for item in raw_cuts:
        if cuts and float(item["timestamp_sec"]) - float(cuts[-1]["timestamp_sec"]) < 0.6:
            previous = cuts[-1]
            item_strength = float(item["histogram_distance"]) + float(item["pixel_difference"])
            previous_strength = float(previous["histogram_distance"]) + float(previous["pixel_difference"])
            if item_strength > previous_strength:
                cuts[-1] = item
        else:
            cuts.append(item)
    for item in transitions:
        item["shot_cut"] = any(item is cut for cut in cuts)

    boundaries = [0] + [int(item["right_index"]) for item in cuts] + [len(frames)]
    groups: list[list[FrameCandidate]] = []
    candidate_by_index = {int(item["frame_index"]): item for item in candidates}
    for begin, end in zip(boundaries, boundaries[1:], strict=False):
        group = [candidate_by_index[i] for i in range(begin, end) if i in candidate_by_index]
        if group:
            groups.append(group)
    thresholds = {
        "histogram_distance_min": 0.22,
        "pixel_difference_min": 0.045,
        "histogram_distance_strong": 0.30,
        "pixel_difference_strong": 0.15,
        "strong_pixel_novelty_min": 0.4,
        "merge_distance_sec": 0.6,
    }
    return groups, transitions, thresholds


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
    """Build memory scenes with adaptive SVD change groups and Katna per group."""

    def __init__(self, output_root: Path) -> None:
        self._output_root = Path(output_root).expanduser().resolve()

    def __call__(self, video_path: Path, video_id: str, duration_sec: float) -> tuple[SceneWindow, ...]:
        started = time.perf_counter()
        frames, sampling, decode_seconds = _sample_video(Path(video_path), duration_sec)
        svd_started = time.perf_counter()
        representation = _svd_representation(frames)
        candidates, svd_metrics = _deduplicate(frames, representation)
        svd_dedup_seconds = time.perf_counter() - svd_started
        katna_started = time.perf_counter()
        first: FrameCandidate = {
            "frame_index": 0,
            "timestamp_sec": float(frames[0][0]),
            "image": frames[0][1],
            "endpoint_anchor": True,
        }
        last_index = len(frames) - 1
        last: FrameCandidate = {
            "frame_index": last_index,
            "timestamp_sec": float(frames[last_index][0]),
            "image": frames[last_index][1],
            "endpoint_anchor": True,
        }

        # Katna itself selects the exact source endpoints through singleton
        # groups. Interior groups receive the full 10fps stream so short-lived
        # transitions are not erased by global deduplication before planning.
        raw_selected = _katna_select([first], 1)
        if last_index > 0:
            raw_selected.extend(_katna_select([last], 1))
        interior_candidates = [item for item in candidates if 0 < int(item["frame_index"]) < last_index]
        groups, transitions, cut_thresholds = _shot_groups(frames, interior_candidates, representation)
        shot_rows: list[dict[str, object]] = []
        for shot_number, group in enumerate(groups, start=1):
            start_index = int(group[0]["frame_index"])
            end_index = int(group[-1]["frame_index"])
            internal_transitions = [
                item for item in transitions if start_index <= int(item["left_index"]) < end_index
            ]
            mean_similarity = (
                float(np.mean([float(item["similarity"]) for item in internal_transitions]))
                if internal_transitions
                else 1.0
            )
            mean_energy = (
                float(np.mean([float(item["energy_norm"]) for item in internal_transitions]))
                if internal_transitions
                else 0.0
            )
            change_score = (
                float(np.mean([float(item["change_score"]) for item in internal_transitions]))
                if internal_transitions
                else 0.0
            )
            # Candidate density and within-shot visual change set the budget.
            # There is no global keyframe ceiling; static shots get one frame.
            budget = min(
                len(group),
                max(1, int(round(1.0 + np.sqrt(len(group)) * change_score * 1.4))),
            )
            selected_group = _katna_select(group, budget)
            raw_selected.extend(selected_group)
            shot_rows.append(
                {
                    "shot": shot_number,
                    "start_sec": float(group[0]["timestamp_sec"]),
                    "end_sec": float(group[-1]["timestamp_sec"]),
                    "deduplicated_candidate_count": len(group),
                    "mean_similarity": mean_similarity,
                    "mean_normalized_energy": mean_energy,
                    "change_score": change_score,
                    "requested_katna_budget": budget,
                    "katna_selected_count": len(selected_group),
                    "selected_timestamps_sec": [float(item["timestamp_sec"]) for item in selected_group],
                }
            )

        # Remove selections repeated across segment boundaries. Keep endpoints
        # locked and retain the sharper Katna-selected image in each conflict.
        sharpness = [float(cv2.Laplacian(image, cv2.CV_64F).var()) for _, image in frames]
        locked = [item for item in raw_selected if item.get("endpoint_anchor")]
        interior_selected = [item for item in raw_selected if not item.get("endpoint_anchor")]
        selected = list(locked)
        dedup_decisions: list[dict[str, object]] = []
        for item in sorted(
            interior_selected,
            key=lambda candidate: sharpness[int(candidate["frame_index"])],
            reverse=True,
        ):
            frame_index = int(item["frame_index"])
            conflict = next(
                (
                    int(previous["frame_index"])
                    for previous in selected
                    if abs(frame_index - int(previous["frame_index"])) <= 4
                ),
                None,
            )
            if conflict is None:
                selected.append(item)
                decision = "keep"
                reason = "Katna selection passed cross-group deduplication"
            else:
                decision = "suppress"
                reason = f"within 0.4 seconds of retained sample {conflict}"
            dedup_decisions.append(
                {
                    "sample_index": frame_index,
                    "timestamp_sec": float(item["timestamp_sec"]),
                    "decision": decision,
                    "reason": reason,
                    "sharpness": sharpness[frame_index],
                }
            )
        selected.sort(key=lambda item: float(item["timestamp_sec"]))
        katna_seconds = time.perf_counter() - katna_started
        katna_selected_count = len(raw_selected)
        scene_window_started = time.perf_counter()
        windows = _scene_windows(selected, duration_sec)
        scene_window_seconds = time.perf_counter() - scene_window_started
        elapsed = time.perf_counter() - started
        output = self._output_root / video_id
        output.mkdir(parents=True, exist_ok=True)
        (output / "analysis.json").write_text(
            json.dumps(
                {
                    "algorithm": "svd_similarity90_shot_boundary_adaptive_katna",
                    "parameters": {
                        "fps": SAMPLE_FPS,
                        "svd_energy_threshold": SVD_ENERGY_THRESHOLD,
                        "similarity_threshold": SIMILARITY_THRESHOLD,
                        "shot_detection": (
                            "adjacent grayscale pixel difference + HSV histogram Bhattacharyya distance"
                        ),
                        "shot_cut_thresholds": cut_thresholds,
                        "per_shot_budget": (
                            "round(1 + sqrt(deduplicated_candidates) * mean_change_score * 1.4), "
                            "bounded by candidates in that shot"
                        ),
                        "katna_frame_budget_min": 1,
                        "katna_frame_budget_max": None,
                        "minimum_selected_spacing_sec": 0.5,
                        "max_frame_side": MAX_FRAME_SIDE,
                        "first_and_last_selected_by_katna": True,
                    },
                    "sampling": sampling,
                    "svd": svd_metrics,
                    "shots": shot_rows,
                    "change_transitions": transitions,
                    "dedup_decisions": dedup_decisions,
                    "katna": {
                        "svd_candidate_count": len(candidates),
                        "selected_before_cross_group_dedup": katna_selected_count,
                        "selected_after_cross_group_dedup": len(selected),
                        "seconds": katna_seconds,
                    },
                    "timings": {
                        "decode_seconds": round(decode_seconds, 3),
                        "svd_dedup_seconds": round(svd_dedup_seconds, 3),
                        "katna_seconds": round(katna_seconds, 3),
                        "scene_window_seconds": round(scene_window_seconds, 3),
                    },
                    "final_selected_count": len(selected),
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

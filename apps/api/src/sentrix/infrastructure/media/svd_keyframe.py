"""CPU-only low-rank keyframe extraction, adapted from the original project.

The extractor deliberately has no dependency on the semantic WorldMM route. It
works on a compact temporal feature matrix, detects changes between adjacent
local subspaces, and writes ordinary JPEG evidence frames plus an analysis
manifest.  The caller can therefore import the result through the existing
video scene/keyframe contract without changing the database schema.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .svd_contracts import WorldMMKeyframe, WorldMMResult, WorldMMScene

DEFAULT_CONFIG: dict[str, Any] = {
    "sample_fps": 2.0,
    "resize": [160, 90],
    "window_seconds": 8.0,
    "stride_seconds": 2.0,
    "energy_threshold": 0.90,
    "min_rank": 1,
    "max_rank": 16,
    "min_event_seconds": 2.0,
    "score_weights": {"reconstruction": 0.40, "subspace": 0.30, "spectrum": 0.20, "coverage": 0.10},
    "boundary_quantile": 0.78,
    "dedup_similarity": 0.985,
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_svd_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load YAML when PyYAML is present, with a safe built-in fallback."""
    candidate = Path(path) if path else Path(__file__).with_name("svd_config.yaml")
    if not candidate.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        return _merge(DEFAULT_CONFIG, payload if isinstance(payload, dict) else {})
    except Exception:
        # The checked-in config is intentionally simple, so this parser is
        # sufficient when an installation does not include PyYAML.
        values: dict[str, Any] = {}
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-") or ":" not in line:
                continue
            key, raw = [part.strip() for part in line.split(":", 1)]
            try:
                values[key] = float(raw) if "." in raw else int(raw)
            except ValueError:
                values[key] = raw.strip("\"'")
        return _merge(DEFAULT_CONFIG, values)


def _normalise(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32, copy=False)
    low, high = float(values.min()), float(values.max())
    if high - low > 1e-6:
        values = (values - low) / (high - low)
    return values


class SVDLowRankKeyframeExtractor:
    """Extract representative frames using local low-rank temporal structure."""

    def __init__(self, config: dict[str, Any] | None = None, config_path: str | Path | None = None):
        self.config = _merge(load_svd_config(config_path), config or {})

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        width, height = [int(v) for v in self.config.get("resize", [160, 90])]
        small = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        # A 32x18 luminance grid is compact enough for long videos while
        # retaining motion/state changes that a single global histogram loses.
        compact = cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        colour = small.reshape(-1, 3).mean(axis=0).astype(np.float32) / 255.0
        return np.concatenate([compact.reshape(-1), colour])

    def sample_video(self, video_path: str | Path):
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video: {video_path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        sample_fps = max(0.1, float(self.config.get("sample_fps", 2.0)))
        step = max(1, int(round(fps / sample_fps))) if fps > 0 else 1
        samples: list[tuple[float, int, np.ndarray]] = []
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % step == 0:
                timestamp = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
                samples.append((timestamp, index, frame))
            index += 1
        cap.release()
        if not samples:
            raise RuntimeError("video contains no decodable frames")
        return samples, {
            "fps": fps,
            "frame_count": frame_count or index,
            "duration_sec": duration or samples[-1][0],
        }

    def build_feature_matrix(self, samples) -> np.ndarray:
        return np.vstack([self.preprocess(frame) for _, _, frame in samples]).astype(np.float32)

    def estimate_rank(self, singular_values: np.ndarray) -> tuple[int, float]:
        if singular_values.size == 0:
            return 1, 1.0
        energy = np.square(singular_values.astype(np.float64))
        ratio = np.cumsum(energy) / max(float(energy.sum()), 1e-12)
        threshold = float(self.config.get("energy_threshold", 0.90))
        rank = int(np.searchsorted(ratio, threshold) + 1)
        rank = max(
            int(self.config.get("min_rank", 1)),
            min(rank, int(self.config.get("max_rank", 16)), len(singular_values)),
        )
        return rank, float(ratio[rank - 1])

    def low_rank_decomposition(self, matrix: np.ndarray):
        if matrix.size == 0:
            return np.empty((0, 0)), np.empty(0), np.empty((0, 0)), 1, 1.0
        u, singular, vt = np.linalg.svd(matrix, full_matrices=False)
        rank, energy = self.estimate_rank(singular)
        return u[:, :rank], singular, vt[:rank, :], rank, energy

    @staticmethod
    def _js_divergence(a: np.ndarray, b: np.ndarray) -> float:
        size = max(len(a), len(b))
        pa = np.pad(a, (0, size - len(a))) + 1e-12
        pb = np.pad(b, (0, size - len(b))) + 1e-12
        pa, pb = pa / pa.sum(), pb / pb.sum()
        mid = (pa + pb) / 2.0
        return float(0.5 * np.sum(pa * np.log(pa / mid)) + 0.5 * np.sum(pb * np.log(pb / mid)))

    def reconstruction_novelty(self, matrix: np.ndarray, u: np.ndarray, vt: np.ndarray) -> np.ndarray:
        if not len(matrix):
            return np.empty(0)
        reconstructed = (u @ (u.T @ matrix)) if u.size else np.zeros_like(matrix)
        return np.mean(np.square(matrix - reconstructed), axis=0)

    def subspace_change(self, previous_u: np.ndarray | None, current_u: np.ndarray) -> float:
        if previous_u is None or not previous_u.size or not current_u.size:
            return 0.0
        rank = min(previous_u.shape[1], current_u.shape[1])
        a, b = previous_u[:, :rank], current_u[:, :rank]
        return float(np.linalg.norm(a @ a.T - b @ b.T, ord="fro") / math.sqrt(max(1, a.shape[0])))

    def spectrum_change(self, previous_s: np.ndarray | None, current_s: np.ndarray) -> float:
        if previous_s is None or not len(current_s):
            return 0.0
        return self._js_divergence(np.square(previous_s), np.square(current_s))

    def detect_event_boundaries(self, scores: np.ndarray, timestamps: np.ndarray) -> list[int]:
        if len(scores) <= 1:
            return [0]
        finite = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
        threshold = float(np.quantile(finite, float(self.config.get("boundary_quantile", 0.78))))
        min_gap = max(float(self.config.get("min_event_seconds", 2.0)), 0.1)
        candidates = [
            i
            for i in range(1, len(finite) - 1)
            if finite[i] >= threshold and finite[i] >= finite[i - 1] and finite[i] >= finite[i + 1]
        ]
        selected: list[int] = []
        for index in sorted(candidates, key=lambda i: finite[i], reverse=True):
            if all(abs(float(timestamps[index] - timestamps[other])) >= min_gap for other in selected):
                selected.append(index)
        return [0] + sorted(selected)

    def select_event_keyframes(
        self, start: int, end: int, scores: np.ndarray, features: np.ndarray, max_count: int
    ) -> list[int]:
        if end <= start:
            return [start]
        count = max(1, min(max_count, end - start))
        ranked = sorted(range(start, end), key=lambda i: float(scores[i]), reverse=True)
        selected: list[int] = []
        for index in ranked:
            if all(
                float(
                    np.dot(features[index], features[other])
                    / (np.linalg.norm(features[index]) * np.linalg.norm(features[other]) + 1e-8)
                )
                < float(self.config.get("dedup_similarity", 0.985))
                for other in selected
            ):
                selected.append(index)
            if len(selected) >= count:
                break
        return sorted(selected or [start])

    def deduplicate(self, indices: list[int], features: np.ndarray) -> list[int]:
        result: list[int] = []
        for index in indices:
            if not result:
                result.append(index)
                continue
            similarity = float(
                np.dot(features[index], features[result[-1]])
                / (np.linalg.norm(features[index]) * np.linalg.norm(features[result[-1]]) + 1e-8)
            )
            if similarity < float(self.config.get("dedup_similarity", 0.985)):
                result.append(index)
        return result

    def extract(self, video_path: str | Path, video_id: str, output_dir: str | Path) -> WorldMMResult:
        started = time.perf_counter()
        output = Path(output_dir).resolve()
        frames_dir = output / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        samples, video = self.sample_video(video_path)
        features = _normalise(self.build_feature_matrix(samples))
        timestamps = np.asarray([item[0] for item in samples], dtype=np.float64)
        scores = np.zeros(len(samples), dtype=np.float64)
        reconstruction = np.zeros(len(samples), dtype=np.float64)
        subspace = np.zeros(len(samples), dtype=np.float64)
        spectrum = np.zeros(len(samples), dtype=np.float64)
        ranks = np.ones(len(samples), dtype=np.int32)
        energies = np.ones(len(samples), dtype=np.float64)
        window = max(
            2,
            int(
                round(
                    float(self.config.get("window_seconds", 8.0)) * float(self.config.get("sample_fps", 2.0))
                )
            ),
        )
        stride = max(
            1,
            int(
                round(
                    float(self.config.get("stride_seconds", 2.0)) * float(self.config.get("sample_fps", 2.0))
                )
            ),
        )
        previous_u = previous_s = None
        for left in range(0, len(samples), stride):
            right = min(len(samples), left + window)
            matrix = features[left:right].T
            u, singular, vt, rank, energy = self.low_rank_decomposition(matrix)
            local_rec = self.reconstruction_novelty(matrix, u, vt)
            centre = left + max(0, (right - left - 1) // 2)
            reconstruction[left:right] = np.maximum(reconstruction[left:right], local_rec)
            change = self.subspace_change(previous_u, u)
            spec = self.spectrum_change(previous_s, singular)
            subspace[centre] = max(subspace[centre], change)
            spectrum[centre] = max(spectrum[centre], spec)
            ranks[left:right] = rank
            energies[left:right] = energy
            previous_u, previous_s = u, singular
        for values in (reconstruction, subspace, spectrum):
            peak = float(np.percentile(values, 95))
            if peak > 1e-9:
                values /= peak
        weights = self.config.get("score_weights", {})
        coverage = np.linspace(0.0, 1.0, len(samples), dtype=np.float64)
        scores[:] = (
            float(weights.get("reconstruction", 0.40)) * reconstruction
            + float(weights.get("subspace", 0.30)) * subspace
            + float(weights.get("spectrum", 0.20)) * spectrum
            + float(weights.get("coverage", 0.10)) * coverage
        )
        boundaries = self.detect_event_boundaries(scores, timestamps)
        boundaries.append(len(samples))
        scenes: list[WorldMMScene] = []
        all_selected: list[int] = []
        for scene_index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
            if end <= start:
                continue
            duration = float(timestamps[min(end - 1, len(timestamps) - 1)] - timestamps[start])
            max_count = 1 if duration < 5 else 2 if duration <= 15 else 3
            picks = self.select_event_keyframes(start, end, scores, features, max_count)
            picks = self.deduplicate(picks, features)
            keyframes: list[WorldMMKeyframe] = []
            for ordinal, index in enumerate(picks, 1):
                timestamp, frame_index, frame = samples[index]
                target = frames_dir / f"scene_{scene_index:04d}_kf_{ordinal:04d}.jpg"
                cv2.imwrite(str(target), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                raw = {
                    "reconstruction_error": float(reconstruction[index]),
                    "subspace_change": float(subspace[index]),
                    "spectrum_change": float(spectrum[index]),
                    "svd_rank": int(ranks[index]),
                    "energy_ratio": float(energies[index]),
                }
                keyframes.append(
                    WorldMMKeyframe(
                        code=f"{video_id}_svd_{scene_index:04d}_{ordinal:04d}",
                        path=str(target),
                        timestamp_sec=float(timestamp),
                        frame_index=int(frame_index),
                        score=float(scores[index]),
                        selection_reason="svd_lowrank_score",
                        raw=raw,
                    )
                )
                all_selected.append(index)
            scenes.append(
                WorldMMScene(
                    scene_id=f"{video_id}_svd_scene_{scene_index:04d}",
                    index=scene_index,
                    start_sec=float(timestamps[start]),
                    end_sec=float(timestamps[min(end - 1, len(timestamps) - 1)]),
                    keyframes=keyframes,
                    semantic_labels=["svd_lowrank"],
                    raw={"boundary_score": float(scores[start])},
                )
            )
        metrics = {
            "timestamps": timestamps.tolist(),
            "score": scores.tolist(),
            "reconstruction_error": reconstruction.tolist(),
            "subspace_change": subspace.tolist(),
            "spectrum_change": spectrum.tolist(),
            "svd_rank": ranks.tolist(),
            "energy_ratio": energies.tolist(),
            "runtime_seconds": round(time.perf_counter() - started, 4),
            "config": self.config,
        }
        (output / "analysis.json").write_text(
            json.dumps(
                {
                    "video": video,
                    "algorithm": "svd_lowrank",
                    "metrics": metrics,
                    "selected_indices": all_selected,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return WorldMMResult(
            video=video,
            scenes=scenes,
            output_dir=str(output),
            manifest={"method": "svd_lowrank", "algorithm": "svd_lowrank", "metrics": metrics},
            full_keyframe_count=len(samples),
            summary_keyframe_count=len(all_selected),
            selected_keyframe_count=len(all_selected),
        )

    run = extract

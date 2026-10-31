"""Bidirectional attention between scene-frame and timestamped speech embeddings.

Both modalities must be encoded by the same pretrained multimodal embedder so their
vectors share a semantic space. Speech text here is produced by timestamped ASR; this
module does not claim access to the ASR model's hidden acoustic states.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AttentionResult:
    visual_to_audio: np.ndarray
    audio_to_visual: np.ndarray
    fused_visual: np.ndarray
    fused_audio: np.ndarray


def _unit_rows(values: Sequence[Sequence[float]]) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2 or not array.shape[0] or not array.shape[1]:
        return np.empty((0, 0), dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return array / np.maximum(norms, 1e-12)


def _softmax(values: np.ndarray, axis: int) -> np.ndarray:
    shifted = values - np.max(values, axis=axis, keepdims=True)
    exp = np.exp(np.clip(shifted, -80.0, 0.0))
    return exp / np.maximum(exp.sum(axis=axis, keepdims=True), 1e-12)


def bidirectional_cross_modal_attention(
    visual_features: Sequence[Sequence[float]],
    audio_features: Sequence[Sequence[float]],
    visual_times_sec: Sequence[float],
    audio_times_sec: Sequence[float],
    *,
    temperature: float = 0.07,
    temporal_scale_sec: float = 45.0,
) -> AttentionResult:
    """Compute visual→speech and speech→visual attention with a temporal prior.

    Inputs are pooled pretrained embeddings in one shared multimodal space. Logits use
    scaled cross-modal cosine similarity plus a smooth timestamp prior, not hard scene
    overlap. Thus a continuous utterance may attend across a visual cut.
    """
    visual = _unit_rows(visual_features)
    audio = _unit_rows(audio_features)
    if visual.size == 0 or audio.size == 0 or visual.shape[1] != audio.shape[1]:
        return AttentionResult(np.empty((0, 0)), np.empty((0, 0)), visual, audio)
    if len(visual_times_sec) != len(visual) or len(audio_times_sec) != len(audio):
        raise ValueError("feature and timestamp counts must match")

    v_times = np.asarray(visual_times_sec, dtype=np.float32)[:, None]
    a_times = np.asarray(audio_times_sec, dtype=np.float32)[None, :]
    logits = (visual @ audio.T) / max(float(temperature), 1e-4)
    logits -= np.abs(v_times - a_times) / max(float(temporal_scale_sec), 1e-3)
    v_to_a = _softmax(logits, axis=1)
    a_to_v = _softmax(logits, axis=0).T
    fused_visual = visual + v_to_a @ audio
    fused_audio = audio + a_to_v @ visual
    fused_visual /= np.maximum(np.linalg.norm(fused_visual, axis=1, keepdims=True), 1e-12)
    fused_audio /= np.maximum(np.linalg.norm(fused_audio, axis=1, keepdims=True), 1e-12)
    return AttentionResult(v_to_a, a_to_v, fused_visual, fused_audio)


def attended_speech_indices(result: AttentionResult, *, per_frame: int = 3) -> list[list[tuple[int, float]]]:
    """Return each frame's strongest mutually supported ASR segments and weights."""
    if result.visual_to_audio.size == 0:
        return []
    mutual = np.sqrt(np.maximum(result.visual_to_audio * result.audio_to_visual.T, 0.0))
    totals = mutual.sum(axis=1, keepdims=True)
    mutual = mutual / np.maximum(totals, 1e-12)
    rows: list[list[tuple[int, float]]] = []
    for row in mutual:
        order = np.argsort(row)[::-1][: max(1, int(per_frame))]
        rows.append([(int(index), float(row[index])) for index in order if row[index] > 0])
    return rows

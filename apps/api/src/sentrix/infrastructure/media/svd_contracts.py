"""Minimal result objects used by the original CPU SVD keyframe extractor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorldMMKeyframe:
    code: str
    path: str
    timestamp_sec: float
    frame_index: int
    score: float = 0.0
    selection_reason: str = ""
    objects: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    expressions: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldMMScene:
    scene_id: str
    index: int
    start_sec: float
    end_sec: float
    keyframes: list[WorldMMKeyframe] = field(default_factory=list)
    semantic_labels: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldMMResult:
    video: dict[str, Any]
    scenes: list[WorldMMScene]
    output_dir: str
    manifest: dict[str, Any] = field(default_factory=dict)
    full_keyframe_count: int = 0
    summary_keyframe_count: int = 0
    selected_keyframe_count: int = 0

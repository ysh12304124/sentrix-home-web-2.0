"""把原始图片投影为可遍历的图片—图片关系边。"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from sentrix.domain.media.models import Asset, MediaType, Observation
from sentrix.domain.retrieval.ports import VectorRepository
from sentrix.domain.retrieval.scheme import VISUAL_SPACE

log = logging.getLogger("sentrix.memories.image_graph")

# 普通图片不一定属于同一个视频，因此时间边按拍摄时间把相邻图片串起来；
# 只连接同一日内的相邻记录，避免把相隔数月的图片误连成一条时间线。
IMAGE_TEMPORAL_MAX_SECONDS = 24 * 60 * 60
IMAGE_PAIR_WINDOW = 8
IMAGE_OBJECTS_PER_IMAGE = 8
VISUAL_TOP_K = 3
VISUAL_MIN_SCORE = 0.80
VISUAL_CHUNK_SIZE = 256

NON_INFORMATIVE_OBJECTS = frozenset(
    {
        "person",
        "people",
        "human",
        "hand",
        "body",
        "camera",
        "floor",
        "wall",
        "ceiling",
        "sky",
        "ground",
        "pavement",
        "road",
        "floor-wood",
        "wall-tile",
        "ceiling-tile",
        "door",
        "window",
    }
)


@dataclass(frozen=True, slots=True)
class ImageGraphStats:
    image_count: int = 0
    temporal_edges: int = 0
    object_edges: int = 0
    visual_edges: int = 0
    visual_status: str = "no_images"


@dataclass(frozen=True, slots=True)
class _Image:
    asset: Asset
    observation: Observation
    objects: tuple[str, ...]

    @property
    def node_id(self) -> str:
        return f"asset:{self.asset.id}"

    @property
    def captured_at(self) -> datetime | None:
        return self.asset.captured_at or self.observation.captured_at


def _labels(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip().casefold() for value in values if value.strip()))


def _timestamp(image: _Image) -> float | None:
    value = image.captured_at
    if value is None:
        return None
    try:
        return value.timestamp()
    except (AttributeError, OSError, OverflowError, ValueError):
        return None


def _visual_neighbors(
    images: Sequence[_Image], vectors: VectorRepository
) -> tuple[dict[tuple[str, str], float], str]:
    """按模型和维度分组，取 mutual top-k 的图片视觉近邻。"""
    by_model: dict[tuple[str, int], list[tuple[str, tuple[float, ...]]]] = defaultdict(list)
    missing = 0
    for image in images:
        record = vectors.get(space=VISUAL_SPACE, source_type="asset", source_id=image.asset.id)
        if record is None or not record.vector:
            missing += 1
            continue
        by_model[(record.model_name, record.dimension)].append((image.asset.id, record.vector))

    edges: dict[tuple[str, str], float] = {}
    for rows in by_model.values():
        if len(rows) < 2:
            continue
        ids = [item[0] for item in rows]
        matrix = np.asarray([item[1] for item in rows], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms
        neighbors: list[set[int]] = [set() for _ in ids]
        top_k = min(VISUAL_TOP_K, len(ids) - 1)
        for start in range(0, len(ids), VISUAL_CHUNK_SIZE):
            stop = min(start + VISUAL_CHUNK_SIZE, len(ids))
            similarities = matrix[start:stop] @ matrix.T
            local_rows = np.arange(stop - start)
            similarities[local_rows, np.arange(start, stop)] = -np.inf
            top_indices = np.argpartition(-similarities, kth=top_k - 1, axis=1)[:, :top_k]
            for local_index, candidates in enumerate(top_indices):
                source = start + local_index
                for target in candidates:
                    if similarities[local_index, target] >= VISUAL_MIN_SCORE:
                        neighbors[source].add(int(target))
        for source, targets in enumerate(neighbors):
            for target in targets:
                if source not in neighbors[target]:
                    continue
                pair = (min(ids[source], ids[target]), max(ids[source], ids[target]))
                edges[pair] = float(np.dot(matrix[source], matrix[target]))
    if not by_model:
        return edges, "vectors_unavailable"
    return edges, "partial_vectors" if missing else "ok"


def project_image_graph(
    *,
    assets: Sequence[Asset],
    latest_observations: Mapping[str, Observation],
    vectors: VectorRepository | None,
    add_edge: Callable[[str, str, str, float, Mapping[str, object], str], None],
) -> ImageGraphStats:
    """为原始图片建立共享对象、时间和视觉相似的直接边。

    视频关键帧由 ``keyframe_graph`` 单独处理；这里明确排除派生图片，避免把
    一张视频关键帧同时当成普通照片再次计入图片关系。
    """
    images = [
        _Image(
            asset=asset,
            observation=latest_observations[asset.id],
            objects=tuple(
                value
                for value in _labels(latest_observations[asset.id].objects)
                if value not in NON_INFORMATIVE_OBJECTS
            )[:IMAGE_OBJECTS_PER_IMAGE],
        )
        for asset in assets
        if asset.media_type == MediaType.IMAGE
        and asset.derived_kind is None
        and asset.id in latest_observations
    ]
    if not images:
        return ImageGraphStats()

    temporal_edges = object_edges = visual_edges = 0

    timed = [(timestamp, image) for image in images if (timestamp := _timestamp(image)) is not None]
    timed.sort(key=lambda item: (item[0], item[1].asset.id))
    for (left_time, left), (right_time, right) in zip(timed, timed[1:], strict=False):
        gap = right_time - left_time
        if 0 <= gap <= IMAGE_TEMPORAL_MAX_SECONDS:
            add_edge(
                left.node_id,
                right.node_id,
                "image_temporal_next",
                max(0.1, 1.0 - gap / IMAGE_TEMPORAL_MAX_SECONDS),
                {
                    "sub_type": "TIME_PRECEDES",
                    "gap_seconds": gap,
                    "evidence_tier": "confirmed",
                },
                "",
            )
            temporal_edges += 1

    by_object: dict[str, list[_Image]] = defaultdict(list)
    for image in images:
        for label in image.objects:
            by_object[label].append(image)
    for label, matches in sorted(by_object.items()):
        matches.sort(
            key=lambda image: (
                image.asset.captured_at is None,
                _timestamp(image) or 0.0,
                image.asset.id,
            )
        )
        for index, left in enumerate(matches):
            for right in matches[index + 1 : index + 1 + IMAGE_PAIR_WINDOW]:
                add_edge(
                    left.node_id,
                    right.node_id,
                    "image_shares_object",
                    0.9,
                    {
                        "sub_type": "SAME_OBJECT",
                        "label": label,
                        "evidence_tier": "supported",
                    },
                    label,
                )
                object_edges += 1

    visual_status = "not_configured"
    if vectors is not None:
        try:
            visual_neighbors, visual_status = _visual_neighbors(images, vectors)
            for (left_id, right_id), score in sorted(visual_neighbors.items()):
                add_edge(
                    f"asset:{left_id}",
                    f"asset:{right_id}",
                    "image_visual_similar",
                    score,
                    {
                        "sub_type": "VISUAL_SIMILAR",
                        "similarity": score,
                        "evidence_tier": "inferred",
                    },
                    "",
                )
                visual_edges += 1
        except Exception as error:  # 派生关系不能阻断主图构建
            visual_status = "degraded"
            log.warning("普通图片视觉近邻降级: %s", error)

    return ImageGraphStats(
        image_count=len(images),
        temporal_edges=temporal_edges,
        object_edges=object_edges,
        visual_edges=visual_edges,
        visual_status=visual_status,
    )

"""把已有的观察文本向量投影为可控的观察—观察语义边。"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from sentrix.domain.media.models import Observation
from sentrix.domain.retrieval.ports import VectorRepository
from sentrix.domain.retrieval.scheme import SEMANTIC_SPACE

log = logging.getLogger("sentrix.memories.semantic_graph")

SEMANTIC_TOP_K = 3
SEMANTIC_MIN_SCORE = 0.82
SEMANTIC_EDGE_RELATION = "observation_semantic_similar"


@dataclass(frozen=True, slots=True)
class SemanticGraphStats:
    observation_count: int = 0
    semantic_edges: int = 0
    status: str = "no_observations"


def project_semantic_graph(
    *,
    observations: Sequence[Observation],
    vectors: VectorRepository | None,
    add_edge: Callable[[str, str, str, float, Mapping[str, object], str], None],
) -> SemanticGraphStats:
    """按模型/维度分组，使用 mutual top-k + 阈值避免语义边泛滥。"""
    if not observations:
        return SemanticGraphStats()
    if vectors is None:
        return SemanticGraphStats(observation_count=len(observations), status="not_configured")

    rows: list[tuple[str, str, tuple[float, ...]]] = []
    missing = 0
    lookup_failed = False
    for observation in observations:
        try:
            record = vectors.get(
                space=SEMANTIC_SPACE,
                source_type="observation",
                source_id=observation.id,
            )
        except Exception as error:  # noqa: BLE001 - 新向量空间不能阻断图构建
            if not lookup_failed:
                log.warning("读取观察语义向量失败，跳过语义边投影: %s", error)
            lookup_failed = True
            missing += 1
            continue
        if record is None or not record.vector:
            missing += 1
            continue
        rows.append((record.model_name, observation.id, record.vector))

    by_model: dict[tuple[str, int], list[tuple[str, tuple[float, ...]]]] = defaultdict(list)
    for model_name, observation_id, vector in rows:
        by_model[(model_name, len(vector))].append((observation_id, vector))

    edges: dict[tuple[str, str], float] = {}
    for grouped in by_model.values():
        if len(grouped) < 2:
            continue
        ids = [row[0] for row in grouped]
        matrix = np.asarray([row[1] for row in grouped], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms
        top_k = min(SEMANTIC_TOP_K, len(ids) - 1)
        neighbors: list[set[int]] = [set() for _ in ids]
        similarities = matrix @ matrix.T
        np.fill_diagonal(similarities, -np.inf)
        top_indices = np.argpartition(-similarities, kth=top_k - 1, axis=1)[:, :top_k]
        for source, candidates in enumerate(top_indices):
            for target in candidates:
                if similarities[source, target] >= SEMANTIC_MIN_SCORE:
                    neighbors[source].add(int(target))
        for source, targets in enumerate(neighbors):
            for target in targets:
                if source not in neighbors[target]:
                    continue
                pair = (min(ids[source], ids[target]), max(ids[source], ids[target]))
                edges[pair] = float(np.dot(matrix[source], matrix[target]))

    for (left, right), score in sorted(edges.items()):
        add_edge(
            f"observation:{left}",
            f"observation:{right}",
            SEMANTIC_EDGE_RELATION,
            score,
            {
                "sub_type": "SEMANTIC_SIMILAR",
                "similarity": score,
                "threshold": SEMANTIC_MIN_SCORE,
                "evidence_tier": "inferred",
            },
            "",
        )
    if not by_model:
        status = "vectors_unavailable"
    elif missing or lookup_failed:
        status = "partial_vectors"
    else:
        status = "ok"
    return SemanticGraphStats(
        observation_count=len(observations),
        semantic_edges=len(edges),
        status=status,
    )

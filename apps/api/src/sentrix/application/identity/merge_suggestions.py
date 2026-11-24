"""相似人物簇的人工合并建议。

只做候选排序与负向决策，不合并、不发布「同人」判定。门槛复用现有人脸匹配的
余弦阈值；真正合并仍走既有 merge 用例。每个簇最多 5 条，一页 20 条。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentrix.application.identity.enroll_face import MATCH_THRESHOLD
from sentrix.domain.identity.faces import cosine
from sentrix.domain.identity.models import (
    MergeCandidateCluster,
    MergeSuggestion,
    merge_pair_key,
)
from sentrix.domain.identity.ports import PersonMergeSuggestionRepository

MAX_PER_CLUSTER = 5


def _feature_version(left: MergeCandidateCluster, right: MergeCandidateCluster) -> str:
    return (
        f"{left.model_name}:{left.model_version}:{left.revision}"
        f":{right.model_name}:{right.model_version}:{right.revision}"
    )


def _unit(values: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values) if norm > 0 else ()


def _similarity(left: MergeCandidateCluster, right: MergeCandidateCluster) -> float:
    # 仓储里的代表向量可能来自簇平均，未必已归一化；比较前各自归一，门槛仍是余弦。
    return cosine(_unit(left.embedding), _unit(right.embedding))


def _comparable(left: MergeCandidateCluster, right: MergeCandidateCluster) -> bool:
    if not left.embedding or not right.embedding:
        return False
    if left.person_id and left.person_id == right.person_id:
        return False
    for name in ("model_name", "model_version"):
        left_value = getattr(left, name)
        right_value = getattr(right, name)
        if left_value != "unknown" and right_value != "unknown" and left_value != right_value:
            return False
    return True


@dataclass(frozen=True, slots=True)
class MergeSuggestionList:
    scope_id: str
    suggestions: tuple[MergeSuggestion, ...]


class ReadMergeSuggestions:
    def __init__(self, *, suggestions: PersonMergeSuggestionRepository) -> None:
        self._suggestions = suggestions

    def execute(
        self,
        *,
        scope_id: str,
        limit: int = 20,
        threshold: float = MATCH_THRESHOLD,
    ) -> MergeSuggestionList:
        if not scope_id.strip():
            raise ValueError("scope_id is required")
        candidates = self._suggestions.features(scope_id)
        decisions = self._suggestions.decisions(scope_id)
        pairs: list[MergeSuggestion] = []
        for index, left in enumerate(candidates):
            for right in candidates[index + 1 :]:
                if not _comparable(left, right):
                    continue
                score = _similarity(left, right)
                if score < threshold:
                    continue
                version = _feature_version(left, right)
                decision = decisions.get(merge_pair_key(left.cluster_id, right.cluster_id))
                if decision is not None and decision[1] == version:
                    continue
                pairs.append(
                    MergeSuggestion(
                        left=left,
                        right=right,
                        score=score,
                        feature_version=version,
                    )
                )
        pairs.sort(key=lambda item: (-item.score, item.left.cluster_id, item.right.cluster_id))
        counts: dict[str, int] = {}
        selected: list[MergeSuggestion] = []
        for pair in pairs:
            left_count = counts.get(pair.left.cluster_id, 0)
            right_count = counts.get(pair.right.cluster_id, 0)
            if left_count >= MAX_PER_CLUSTER or right_count >= MAX_PER_CLUSTER:
                continue
            counts[pair.left.cluster_id] = left_count + 1
            counts[pair.right.cluster_id] = right_count + 1
            selected.append(pair)
            if len(selected) >= max(1, int(limit)):
                break
        return MergeSuggestionList(scope_id=scope_id, suggestions=tuple(selected))


class RejectMergeSuggestion:
    def __init__(self, *, suggestions: PersonMergeSuggestionRepository) -> None:
        self._suggestions = suggestions

    def execute(
        self,
        *,
        scope_id: str,
        left_cluster_id: str,
        right_cluster_id: str,
        feature_version: str,
    ) -> None:
        if not all(item.strip() for item in (scope_id, left_cluster_id, right_cluster_id, feature_version)):
            raise ValueError("scope_id, cluster ids and feature_version are required")
        self._suggestions.record_decision(
            scope_id=scope_id,
            left_cluster_id=left_cluster_id,
            right_cluster_id=right_cluster_id,
            decision="rejected",
            feature_version=feature_version,
        )

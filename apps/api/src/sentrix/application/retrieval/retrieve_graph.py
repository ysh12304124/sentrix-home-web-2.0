"""图结构记忆的有界拓展检索。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.domain.identity.family_graph import RELATION_INVERSES
from sentrix.domain.memories.graph import GraphExpansionHit
from sentrix.domain.memories.ports import MemoryGraphRepository
from sentrix.domain.retrieval.models import CandidateHit

GRAPH_CHANNEL = "graph"
GRAPH_MAX_HOPS = 4


def graph_intent(question: str) -> str:
    """按 hpq 的 object/action/temporal/scene/multi-hop 自适应路径选择。"""
    value = question.casefold()
    if re.search(r"\d+\s*(秒|分钟|小时|sec|seconds|minutes|hours)", value) or any(
        token in value
        for token in ("什么时候", "何时", "几点", "多久", "时间线", "when", "what time", "how long")
    ):
        return "temporal"
    if any(token in value for token in ("为什么", "因果", "导致", "原因", "because", "cause", "why")):
        return "causal"
    if any(
        token in value
        for token in (
            "如何",
            "怎么",
            "然后",
            "之后",
            "之前",
            "先后",
            "过程",
            "跨视频",
            "多段",
            "before",
            "after",
            "sequence",
            "steps",
            "across videos",
            "both videos",
        )
    ):
        return "multi_hop"
    if any(
        token in value for token in ("做什么", "动作", "拿起", "打开", "放下", "action", "doing", "holding")
    ):
        return "action"
    if any(token in value for token in ("场景", "环境", "画面", "scene", "setting")):
        return "scene"
    if any(token in value for token in ("什么物体", "哪个物体", "东西", "物品", "object", "item")):
        return "object"
    return "general"


def graph_direction(question: str) -> str:
    """时序/因果边的问句方向；默认正向，不把「B 之后」误答成「B 之前」。"""
    value = question.casefold()
    if any(token in value for token in ("之前", "以前", "先前", "before", "earlier", "previous")):
        return "backward"
    if any(token in value for token in ("之后", "然后", "接着", "后来", "after", "then", "next")):
        return "forward"
    if any(token in value for token in ("为什么", "原因", "what caused", "why")):
        return "backward"
    return ""


@dataclass(frozen=True, slots=True)
class GraphSearchResult:
    hits: Sequence[CandidateHit] = ()
    paths: Sequence[GraphExpansionHit] = ()
    anchored: bool = False
    backend: str = "sqlite_graph"
    degraded: bool = False
    reason: str = ""


class RetrieveGraph:
    """先解析显式人物/事件路径，未命中时再从普通检索头部有界拓展。"""

    def __init__(self, *, graph: MemoryGraphRepository) -> None:
        self._graph = graph

    def execute(
        self,
        *,
        seed_asset_ids: Sequence[str],
        scope_id: str | None,
        limit: int,
        question: str = "",
    ) -> GraphSearchResult:
        # 跨相册检索没有一个可安全混用的 scope 图。此时保留原有五路结果，
        # 同时把限制写入 trace，避免把「图没有跑」伪装成「图跑了但没召回」。
        if not scope_id:
            return GraphSearchResult(degraded=True, reason="graph_requires_scope")
        status = self._graph.status(scope_id)
        if not status.available:
            return GraphSearchResult(degraded=True, reason=status.reason or "graph_not_built")
        anchored_paths = self._graph.query_paths(question, scope_id=scope_id, limit=max(1, limit)) or ()
        requires_path = any(predicate in question for predicate in RELATION_INVERSES) or any(
            token in question for token in ("一起", "同时", "合影", "合照", "共同出现", "together")
        )
        if requires_path and not anchored_paths:
            return GraphSearchResult(reason="no_matching_relation_or_cooccurrence_path")
        intent = graph_intent(question)
        if intent in {"causal", "temporal", "multi_hop"} and anchored_paths:
            # Finding the named person's pictures is only an anchor, not a
            # causal/temporal answer. Continue along the requested edge family.
            seed_asset_ids = tuple(
                dict.fromkeys(
                    (
                        *(item.asset_id for item in anchored_paths),
                        *seed_asset_ids,
                    )
                )
            )
            anchored_paths = ()
        expanded = anchored_paths or self._graph.expand_assets(
            seed_asset_ids,
            scope_id=scope_id,
            max_hops=GRAPH_MAX_HOPS,
            limit=max(1, limit),
            intent=intent,
            direction=graph_direction(question),
        )
        context = tuple(
            hit
            for hit in expanded
            if any(
                relation in {"text_refers_to_event", "text_time_candidate"} for relation in hit.relations
            )
        )
        context_query = any(
            token in question.casefold() for token in ("photo", "picture", "video", "照片", "视频")
        )
        if context and context_query:
            expanded = context
        return GraphSearchResult(
            paths=tuple(expanded),
            anchored=bool(anchored_paths) or bool(context and context_query),
            hits=tuple(
                CandidateHit(
                    asset_id=item.asset_id,
                    channel=GRAPH_CHANNEL,
                    rank=rank,
                    raw_score=item.score,
                    score_kind="graph_path",
                    higher_is_better=True,
                )
                for rank, item in enumerate(expanded, 1)
            ),
        )

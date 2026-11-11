"""实体召回通道：按「已确认的人」找照片。

逐行对应 1.0 ``backend/retrieval/entity.py:1-71`` 的 ``EntityRetriever``。

**人条件是硬条件，不是加分。** 这条通道不参与「像不像」的打分：它只回答「哪些 asset
里出现过这个名字」。所以它的 ``raw_score`` 恒为 1.0、``score_kind`` 是 ``discrete``——
融合只消费 ``rank``，而这一路的名次由 ``asset_id`` 升序给出（1.0 的 ``sorted(candidates)``），
不是按相关性排的。

**检索器从不宣布身份。** 命中一个人名不等于「这张照片里的人就是他」，那是条件判定
（1.0 的 ``HardFilterContext`` / 本轮之前的 S6）的事。这一层只把候选交出去，原文就写在
1.0 ``entity.py:1-8`` 的模块 docstring 里。

**两条取值路径，先投影后观察。** 1.0 的第一条路读 ``observation_search_terms`` 里
``field_type='person_bridge'`` 的行；查不到就退回**直接扫观察的 ``people`` 快照**。
兜底不是给「1.0 的投影写坏了」补的（虽然它确实是坏的，见
``domain/retrieval/lexical.py`` 的字段表注释）——投影是派生数据，它可能还没建起来
（老库、投影重建中途），而观察上的快照是**权威**的那一份。
"""

from __future__ import annotations

from collections.abc import Sequence

from sentrix.domain.media.ports import ObservationRepository
from sentrix.domain.retrieval.models import CandidateHit
from sentrix.domain.retrieval.ports import LexicalSearch

CHANNEL = "entity"

# 1.0 ``entity.py:52`` 的 ``list_observations(limit=100_000)``。兜底那一路要扫整个
# scope，这个上限是 1.0 写死的；写出来而不是留一个「看起来无穷大」的默认值，是因为
# 超过它的照片会**静默地**召回不到。
FALLBACK_SCAN_LIMIT = 100_000


class RetrieveEntity:
    def __init__(self, *, index: LexicalSearch, observations: ObservationRepository) -> None:
        self._index = index
        self._observations = observations

    def execute(
        self,
        *,
        people: Sequence[str],
        scope_id: str | None,
        limit: int,
    ) -> list[CandidateHit]:
        """这些人出现过的 asset，按 id 升序取前 ``limit`` 个。

        ``limit <= 0`` 时返回空表而不是「取前 0 个」——1.0 的循环先 append 再判
        ``len(hits) >= limit``，``limit=0`` 会给出**一个**候选。那不是一个有意义的
        语义，这里按「要 0 个就是不要」处理。
        """
        names = list(dict.fromkeys(item.strip() for item in people if item.strip()))
        if not names or limit <= 0:
            return []
        candidates: set[str] = set()
        for name in names:
            candidates |= self._assets_for_person(name, scope_id)
        return [
            CandidateHit(
                asset_id=asset_id,
                channel=CHANNEL,
                rank=index + 1,
                raw_score=1.0,
                score_kind="discrete",
            )
            for index, asset_id in enumerate(sorted(candidates)[:limit])
        ]

    def _assets_for_person(self, name: str, scope_id: str | None) -> set[str]:
        projected = self._index.assets_for_person(name, scope_id=scope_id)
        if projected:
            return set(projected)
        return self._from_observations(name, scope_id)

    def _from_observations(self, name: str, scope_id: str | None) -> set[str]:
        """兜底：直接读观察上的 ``people`` 快照（1.0 ``entity.py:50-59``）。

        **只认 ``=`` 不认子串**：与投影那条路同一个口径，两条路给出不同的答案比两条路
        都慢更糟——同一个查询在一个库上命中、在另一个库上不命中，而差别只在「投影建没建」。

        ``scope_id`` 为空（跨空间查询）时这条兜底**不跑**：1.0 的 ``list_observations``
        能不带 scope 扫全表，2.0 的 ``ObservationRepository.list_scope`` 要求一个 scope。
        跨空间的人名查询在 2.0 还没有调用方（``EvidenceRequest`` 总有 scope），所以
        这里是「不支持」而不是「随便挑一个空间」。真到需要的那天，缺的是仓储上一条
        跨 scope 的扫描方法，不是这里的一个分支。
        """
        if scope_id is None:
            return set()
        found: set[str] = set()
        for observation in self._observations.list_scope(scope_id, limit=FALLBACK_SCAN_LIMIT):
            if name in observation.people:
                found.add(observation.asset_id)
        return found

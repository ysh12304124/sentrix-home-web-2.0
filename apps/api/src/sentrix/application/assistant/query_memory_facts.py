"""``query_memory_facts`` 的执行侧：查人名、分派、组装答复。

纯规则那一半在 ``domain/retrieval/fact_query.py``（参数校验与 spec 派生），
SQL 在 ``infrastructure/.../structured.py``。这里只剩需要 IO 的三件事：

    人名 → entity_id          ``EntityRepository.find_confirmed_person_by_name``
    按操作分派到仓储           ``StructuredQueryRepository`` 的四个方法
    组装 value / unit / coverage

## 两份投影，方向与检索工具相反

答复拆成 ``FactAnswer``（内部）与 ``visible()``（给模型），对应 ``ToolOutcome`` 的
observation / model_visible。**这一处的切分与检索工具正好相反**：

- 检索工具的 ``total`` 是**候选池大小**，给模型看会让它把「检索到 12 条」当成
  「一共有 12 张」报出去（``application/assistant/tools.py`` 模块注释里那段 1.0 的事故）；
- 这个工具的 ``total`` **就是答案**——用户问「多少张」，它必须被看见。

所以这里不套用「total 一律不给模型」那条规则，而是按字段的含义切：``asset_id``
不给（模型拿到 id 会往提示词里抄），``captured_at`` / ``media_type`` / ``caption``
三样给。1.0 的 ``_fact_samples`` 切的是同一刀。

## 被拒是**正常返回**，不是异常

1.0 用 ``_structured_fact_error`` 返回一个带 ``status`` 的结果而不抛。2.0 沿用
``FactRejection``：模型看到 ``reason`` 才知道下一步怎么改参数，调用方（账本、遥测）
靠 ``status`` 归类。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sentrix.domain.identity.ports import EntityRepository
from sentrix.domain.retrieval.fact_query import (
    FactOperation,
    FactRejection,
    FactRequest,
    FactSubject,
    fact_query_spec,
    parse_fact_arguments,
)
from sentrix.domain.retrieval.ports import StructuredQueryRepository
from sentrix.domain.retrieval.structured_query import StructuredFilter, derive_filter

# 附几条代表观察。1.0 ``_fact_samples`` 里的 ``limit=3``。
SAMPLE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class FactAnswer:
    """一次成功的结构化统计。字段与 1.0 工具返回的 dict 一一对应。"""

    fact_type: str
    subject: FactSubject
    value: Any
    total: int
    unit: str
    rows: tuple[Mapping[str, Any], ...] = ()
    samples: tuple[Mapping[str, Any], ...] = ()
    filters_applied: Mapping[str, Any] = field(default_factory=dict)

    @property
    def coverage(self) -> Mapping[str, Any]:
        """这次统计覆盖了什么。1.0 的 ``coverage``。

        ``identity_scope`` 只在 ``person_appearance`` 下有值：那时统计的单位是
        「已命名人物出现过的照片」，而不是「所有照片」——两个口径下的数字不可比，
        所以必须写在结果里，不能只在调用方知道。
        """
        return {
            "complete": True,
            "scope": "current_album",
            "asset_kind": "original_only",
            "identity_scope": (
                "named_face_entity" if self.subject is FactSubject.PERSON_APPEARANCE else None
            ),
        }

    def visible(self) -> Mapping[str, Any]:
        """发给模型的那一份。``samples`` 里只有三个键——切分在 ``_sample_rows``。"""
        return {
            "status": "ok",
            "fact_type": self.fact_type,
            "subject": self.subject.value,
            "value": self.value,
            "total": self.total,
            "unit": self.unit,
            "rows": [dict(row) for row in self.rows],
            "filters_applied": dict(self.filters_applied),
            "samples": [dict(sample) for sample in self.samples],
            "coverage": dict(self.coverage),
        }


def _unit(request: FactRequest) -> str:
    """结果的计数单位。1.0 先按媒体类型定，再让 first/last/group 覆盖掉。"""
    if request.operation in (FactOperation.FIRST, FactOperation.LAST):
        return "captured_at"
    if request.operation is FactOperation.GROUP:
        return "original_asset_breakdown"
    if request.media == "image":
        return "original_image"
    if request.media == "video":
        return "original_video"
    return "original_asset"


class QueryMemoryFacts:
    def __init__(self, *, structured: StructuredQueryRepository, entities: EntityRepository) -> None:
        self._structured = structured
        self._entities = entities

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        scope_id: str,
        viewer_id: str,
        conversation_id: str,
        now: datetime,
    ) -> FactAnswer | FactRejection:
        """跑一次统计。**失败也是一种正常返回**（``FactRejection``），不抛异常。"""
        parsed = parse_fact_arguments(arguments)
        if isinstance(parsed, FactRejection):
            return parsed
        entity_id: str | None = None
        if parsed.person:
            entity_id = self._resolve_person(parsed.person, scope_id)
            if entity_id is None:
                # 「这个名字在本相册里没被确认过」。**与「这个人不存在」不同**——
                # 所以是 unresolved_person 而不是一个 0 条的统计结果：返回 0 会让
                # 模型理直气壮地说「没有他/她的照片」。
                return FactRejection("unresolved_person", "person_not_named_in_current_album")

        person = parsed.person
        spec = fact_query_spec(
            parsed,
            scope_id=scope_id,
            viewer_id=viewer_id,
            conversation_id=conversation_id,
            # 1.0 用的是 ``tool_{毫秒时间戳}``。``now`` 是注入的，所以这个 id 可复现——
            # 从 ``time.time()`` 取会让同一次调用在用例里每次都不同，而对一个只用于
            # 日志关联的 id 来说，可复现比「更准」有用。
            query_id=f"tool_{int(now.timestamp() * 1000)}",
            now=now,
            entity_resolver=lambda name: entity_id if name == person else None,
        )
        # 传 ``spec.scope_id`` 而不是入参 ``scope_id``：``scope_mode`` 为
        # all_authorized 时它是 None（跨全部相册），那时候「传进来的那个相册」是错的。
        return self._dispatch(parsed, derive_filter(spec, scope_id=spec.scope_id))

    def _resolve_person(self, name: str, scope_id: str) -> str | None:
        match = self._entities.find_confirmed_person_by_name(scope_id, name)
        return match.entity_id if match else None

    def _dispatch(self, request: FactRequest, structured_filter: StructuredFilter) -> FactAnswer:
        rows: tuple[Mapping[str, Any], ...] = ()
        samples: tuple[Mapping[str, Any], ...] = ()

        if request.operation is FactOperation.COUNT:
            total = self._structured.count(structured_filter)
            value: Any = total
        elif request.operation is FactOperation.EXISTS:
            total = self._structured.count(structured_filter)
            value = total > 0
        elif request.operation is FactOperation.FIRST:
            total = self._structured.count(structured_filter)
            value = self._structured.captured_bound(structured_filter, latest=False)
        elif request.operation is FactOperation.LAST:
            total = self._structured.count(structured_filter)
            value = self._structured.captured_bound(structured_filter, latest=True)
        else:
            counts = self._structured.group_counts(structured_filter, request.group_by)
            rows = tuple({"group": item.group, "count": item.count} for item in counts)
            value = [dict(row) for row in rows]
            total = sum(item.count for item in counts)

        if request.operation is not FactOperation.GROUP:
            # 分组结果已经把内容摊开了，再附三条样本是重复的——1.0 也是只在
            # 非分组时才附。
            samples = self._sample_rows(structured_filter)

        applied = dict(structured_filter.applied)
        # 人物过滤在仓储层落的是实体 id；filters_applied 是回给模型/证据绑定看的，
        # 需求限定的是人名。留着 id 会让 evidence_binding 把「明明」与
        # 「entity_xxx」判成 scope 失配，正确答案也落 partial。这里换回请求里
        # 的人名，内部 id 不进入模型可见结果。
        if applied.get("person") and request.person:
            applied["person"] = request.person
        return FactAnswer(
            fact_type=f"{request.subject.value}_{request.operation.value}",
            subject=request.subject,
            value=value,
            total=total,
            unit=_unit(request),
            rows=rows,
            samples=samples,
            filters_applied={
                **applied,
                "scope_id": structured_filter.scope_id,
                "asset_kind": "original_only",
            },
        )

    def _sample_rows(self, structured_filter: StructuredFilter) -> tuple[Mapping[str, Any], ...]:
        """代表观察 → 给模型的那三个键。

        **这一刀就是模型与资产 id 之间的边界**（1.0 ``_fact_samples`` 的键白名单）。
        写成「先全给、再让调用方删掉 asset_id」的话，总有一天会有人忘了删。
        """
        return tuple(
            {
                "captured_at": sample.captured_at,
                "media_type": sample.media_type,
                "caption": sample.caption,
            }
            for sample in self._structured.sample_observations(structured_filter, limit=SAMPLE_LIMIT)
        )


__all__ = ["SAMPLE_LIMIT", "FactAnswer", "QueryMemoryFacts"]

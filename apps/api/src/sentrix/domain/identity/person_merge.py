"""人物合并的纯规则：谁留下、叫什么、算不算已确认、哪些名字变成别名。

用户在名录里说「这几个其实是同一个人」时，后端要回答四个问题。答案全在这里算，
仓储只负责照着 ``PersonMergePlan`` 搬数据，用例只负责编排——三处各管一件，规则
才不会在 SQL 和路由里各抄一份。

1. **谁留下**：用户指定的 ``target``。合并方向是人的决定，这里不做「哪个更像」的推断。
2. **叫什么**：用户显式给的名字优先；没给就用目标自己的名字；目标没名字时取第一个
   有名字的来源。都没有名字就保持未命名（仍是待确认）。
3. **算不算已确认**：参与者里**任意一个**已确认，合并后就是已确认——已确认是用户
   做过的判断，合并不能把它抹掉。
4. **别名**：所有参与者的旧名字与旧别名都留下，去掉最终名字本身和内部簇名。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.domain.identity.models import (
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    ENTITY_TYPE_PERSON,
    PENDING_CLUSTER_NAME,
    EntityRecord,
    entity_display_name,
    is_pending_cluster_name,
)

# 一次最多并几个来源。名录多选不会到这个数；这条上限挡的是误操作或脚本循环，
# 一次事务里改写几十个人的全部派生行，出错时连查都难查。
MAX_MERGE_SOURCES = 20

# 审计行的 ``field_name`` / ``source``。目标与来源各写一行，两边都能追到对方。
REVISION_PERSON_MERGED_INTO = "merged_into"
REVISION_PERSON_ABSORBED = "absorbed_person"
SOURCE_USER_PERSON_MERGE = "user_person_merge"


@dataclass(frozen=True, slots=True)
class PersonMergePlan:
    """仓储照做的那份计划。所有决定都已做完，仓储里不再有判断。"""

    scope_id: str
    target_id: str
    source_ids: tuple[str, ...]
    canonical_name: str
    name_confirmed: bool
    status: str
    family_role: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PersonMergeOutcome:
    """仓储搬完之后的事实，用例据此做后续的重算。"""

    target_cluster_id: str | None
    moved_face_count: int
    moved_relationship_count: int
    dropped_relationship_count: int
    observation_ids: tuple[str, ...]


class PersonMergeRuleError(ValueError):
    """计划本身不成立（参数层面的错，不是数据状态的冲突）。"""


class PersonMergeConflict(ValueError):
    """数据状态不允许合并：跨空间、已被合并过、不是人物。"""


def display_name_of(entity: EntityRecord) -> str | None:
    """有可展示的真名字就返回它，否则 ``None``。占位串与内部簇名都不算名字。"""
    name = entity_display_name(entity)
    return None if name == PENDING_CLUSTER_NAME else name


def plan_person_merge(
    target: EntityRecord,
    sources: Sequence[EntityRecord],
    *,
    requested_name: str | None,
    aliases_by_id: dict[str, Sequence[str]],
) -> PersonMergePlan:
    """按模块 docstring 的四条规则算出合并计划。

    调用方负责把「已经并进目标」的来源先剔掉（幂等重放），这里只看真正要并的人。
    """
    if not sources:
        raise PersonMergeRuleError("at least one source person is required")
    if len(sources) > MAX_MERGE_SOURCES:
        raise PersonMergeRuleError(f"at most {MAX_MERGE_SOURCES} people can be merged at once")
    participants = (target, *sources)
    seen: set[str] = set()
    for entity in participants:
        if entity.id in seen:
            raise PersonMergeRuleError("a person cannot be merged with itself")
        seen.add(entity.id)
        if entity.entity_type != ENTITY_TYPE_PERSON:
            raise PersonMergeConflict("only people can be merged")
        if entity.status not in (ENTITY_STATUS_PENDING, ENTITY_STATUS_CONFIRMED):
            raise PersonMergeConflict(f"person {entity.id} is {entity.status} and cannot be merged")
    scope_id = target.scope_id
    if any(entity.scope_id != scope_id for entity in sources):
        raise PersonMergeConflict("people must belong to the same memory space")

    explicit = (requested_name or "").strip()
    if explicit and is_pending_cluster_name(explicit):
        raise PersonMergeRuleError("person name is required")
    named = [name for name in (display_name_of(entity) for entity in participants) if name]
    final_name = explicit or (named[0] if named else "")
    name_confirmed = bool(final_name)
    canonical_name = final_name or target.canonical_name

    status = (
        ENTITY_STATUS_CONFIRMED
        if any(entity.status == ENTITY_STATUS_CONFIRMED for entity in participants)
        else ENTITY_STATUS_PENDING
    )
    family_role = next((entity.family_role for entity in participants if entity.family_role), None)

    alias_candidates: list[str] = []
    for entity in participants:
        own = display_name_of(entity)
        if own:
            alias_candidates.append(own)
        alias_candidates.extend(aliases_by_id.get(entity.id, ()))
    aliases = tuple(
        dict.fromkeys(
            value
            for value in (str(item or "").strip() for item in alias_candidates)
            if value and value != final_name and not is_pending_cluster_name(value)
        )
    )
    return PersonMergePlan(
        scope_id=scope_id,
        target_id=target.id,
        source_ids=tuple(entity.id for entity in sources),
        canonical_name=canonical_name,
        name_confirmed=name_confirmed,
        status=status,
        family_role=family_role,
        aliases=aliases,
    )

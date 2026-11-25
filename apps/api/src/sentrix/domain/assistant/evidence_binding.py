"""证据绑定：requirement（用户要什么）↔ fact（工具真的产出了什么）。

这一层是纯代码、无模型调用。它回答的不是「工具调用过没有」，而是：

- 工具产出的**值类型**是不是用户要的那一种（检索到 95 张照片 ≠ 婚宴花了多少钱）；
- 工具的 **scope** 是不是覆盖了问题限定的时间/地点/人物/事件；
- 证据的 **coverage** 是完整还是只覆盖了一页候选。

三种判定分开，是因为修法不同：
``unsupported`` 是工具没这个能力（如实说不知道，不提醒模型硬编）；
``scope_mismatch`` 是过滤条件没生效（可以定向重试一次）；
``partial`` 是覆盖不全（允许带缺口回答）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from sentrix.domain.assistant.evidence_ledger import (
    EvidenceRequirement,
    EvidenceType,
    RequirementValueType,
    clean_person_name,
    split_person_scope,
)
from sentrix.domain.assistant.task_state import TaskState, ToolCallRecord
from sentrix.domain.assistant.tools import FINAL_ANSWER_TOOL

_CONTROL_KEYS = frozenset({"event", "time", "place", "person"})


class BindingVerdict(StrEnum):
    SATISFIES = "satisfies"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    SCOPE_MISMATCH = "scope_mismatch"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Fact:
    """工具真正产出的一条事实。``provenance`` 是产生它的 tool call id。"""

    fact_type: str
    value_type: RequirementValueType
    value: Any
    scope: Mapping[str, str] = field(default_factory=dict)
    filters_applied: Mapping[str, str] = field(default_factory=dict)
    #: 模型传了、但工具没有真正应用的过滤条件。它让「静默丢弃」可判定。
    filters_not_applied: tuple[str, ...] = ()
    provenance: str = ""
    confidence: float = 1.0
    coverage: float = 1.0
    person_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RequirementBinding:
    requirement_id: str
    verdict: BindingVerdict
    reason: str = ""
    fact_ids: tuple[str, ...] = ()
    value_type: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _scope_from_filters(filters: Mapping[str, Any]) -> dict[str, str]:
    """只保留 scope 四键。工具可能应用了 media 等过滤，那不是 scope。"""
    scope: dict[str, str] = {}
    for key in _CONTROL_KEYS:
        value = _text(filters.get(key))
        if value:
            scope[key] = value
    return scope


def _retrieval_facts(record: ToolCallRecord) -> list[Fact]:
    observation = record.observation
    visible = record.model_visible
    facts: list[Fact] = []
    asset_ids = observation.get("asset_ids")
    total = int(observation.get("total") or 0)
    raw_people = observation.get("people_by_asset")
    people_by_asset = raw_people if isinstance(raw_people, Mapping) else {}
    raw_returned_ids = observation.get("returned_asset_ids")
    returned_ids = raw_returned_ids if isinstance(raw_returned_ids, (list, tuple)) else ()
    filters = observation.get("filters_applied")
    filter_scope = _scope_from_filters(filters) if isinstance(filters, Mapping) else {}
    if isinstance(asset_ids, (list, tuple)):
        common_people: set[str] = set(people_by_asset.get(asset_ids[0], ())) if asset_ids else set()
        for asset_id in asset_ids[1:]:
            common_people.intersection_update(people_by_asset.get(asset_id, ()))
        facts.append(
            Fact(
                fact_type="retrieval_media_set",
                value_type=RequirementValueType.MEDIA_SET,
                value=tuple(str(item) for item in asset_ids),
                scope=filter_scope,
                provenance=record.call_id,
                coverage=1.0 if total == 0 else min(1.0, len(asset_ids) / total),
                person_names=tuple(sorted(common_people)),
            )
        )
    preview = visible.get("preview")
    if not isinstance(preview, (list, tuple)):
        return facts
    for index, row in enumerate(preview):
        if not isinstance(row, Mapping):
            continue
        handle = _text(row.get("handle"))
        asset_id = returned_ids[index] if index < len(returned_ids) else ""
        confirmed_names = tuple(people_by_asset.get(asset_id, ()))
        scope: dict[str, str] = {}
        for key in ("place", "time", "person"):
            value = _text(row.get(key))
            if value:
                scope[key] = value
        captured_at = _text(row.get("captured_at"))
        if captured_at:
            scope["time"] = filter_scope.get("time", captured_at)
        place = _text(row.get("place"))
        if place:
            scope["place"] = place
        people = row.get("people")
        if isinstance(people, (list, tuple)):
            for person in people:
                name = _text(person)
                if name:
                    facts.append(
                        Fact(
                            fact_type="preview_person",
                            value_type=RequirementValueType.PERSON,
                            value=name,
                            scope={**scope, "person": name},
                            provenance=f"{record.call_id}:{handle}",
                        )
                    )
        if captured_at:
            facts.append(
                Fact(
                    fact_type="preview_time",
                    value_type=RequirementValueType.TIME,
                    value=captured_at,
                    scope=scope,
                    person_names=confirmed_names,
                    provenance=f"{record.call_id}:{handle}",
                )
            )
        if place:
            facts.append(
                Fact(
                    fact_type="preview_place",
                    value_type=RequirementValueType.PLACE,
                    value=place,
                    scope=scope,
                    person_names=confirmed_names,
                    provenance=f"{record.call_id}:{handle}",
                )
            )
        description = _text(row.get("evidence_summary")) or _text(row.get("scene_hint"))
        if description:
            facts.append(
                Fact(
                    fact_type="preview_description",
                    value_type=RequirementValueType.DESCRIPTION,
                    value=description,
                    scope=scope,
                    person_names=confirmed_names,
                    provenance=f"{record.call_id}:{handle}",
                    confidence=1.0 if row.get("evidence_summary") else 0.6,
                )
            )
        speech = row.get("speech_evidence")
        if isinstance(speech, (list, tuple)):
            speech_text = "；".join(
                _text(segment.get("text"))
                for segment in speech
                if isinstance(segment, Mapping) and _text(segment.get("text"))
            )
            if speech_text:
                facts.append(
                    Fact(
                        fact_type="speech_transcript",
                        value_type=RequirementValueType.DESCRIPTION,
                        value=speech_text,
                        # GoalPlanner treats the whole recording as the video-time
                        # scope (rather than inventing a wall-clock capture date).
                        scope={"event": "视频", "time": "视频时间"},
                        provenance=f"{record.call_id}:{handle}",
                    )
                )
    return facts


def fact_value_type(fact_type: str) -> RequirementValueType | None:
    """``query_memory_facts`` 的 fact_type → 它真正产出的值类型。

    公开给 application 层写进工具观察，绑定器与轨迹读同一张表。
    """
    return _fact_type_value_type(fact_type)


def _fact_type_value_type(fact_type: str) -> RequirementValueType | None:
    if fact_type.endswith("_count"):
        return RequirementValueType.COUNT
    if fact_type.endswith("_exists"):
        return RequirementValueType.BOOLEAN
    if fact_type.endswith(("_first", "_last")):
        return RequirementValueType.TIME
    if fact_type.endswith("_group"):
        return RequirementValueType.DESCRIPTION
    return None


def _structured_fact(record: ToolCallRecord) -> list[Fact]:
    observation = record.observation
    fact_type = _text(observation.get("fact_type"))
    value_type = _fact_type_value_type(fact_type)
    if value_type is None:
        return []
    filters = observation.get("filters_applied")
    scope = _scope_from_filters(filters) if isinstance(filters, Mapping) else {}
    dropped = observation.get("filters_not_applied")
    names = observation.get("person_names")
    person_names = tuple(_text(name) for name in names) if isinstance(names, (list, tuple)) else ()
    facts = [
        Fact(
            fact_type=fact_type,
            value_type=value_type,
            value=observation.get("value"),
            scope=scope,
            filters_applied=scope,
            filters_not_applied=tuple(str(item) for item in dropped)
            if isinstance(dropped, (list, tuple))
            else (),
            provenance=record.call_id,
            coverage=1.0,
            person_names=person_names,
        )
    ]
    # 人物维度统计同时是「这个人的事实」。只把它当 count 会让「谁是明明」这类
    # 问题在 person 需求上永远 unsupported。
    person = scope.get("person")
    if person:
        facts.append(
            Fact(
                fact_type=f"{fact_type}_person",
                value_type=RequirementValueType.PERSON,
                value=person,
                scope={"person": person},
                provenance=record.call_id,
                person_names=person_names,
            )
        )
    return facts


def _inspection_facts(record: ToolCallRecord) -> list[Fact]:
    visible = record.model_visible
    facts: list[Fact] = []
    description = _text(visible.get("observation"))
    if description:
        facts.append(
            Fact(
                fact_type="visual_observation",
                value_type=RequirementValueType.DESCRIPTION,
                value=description,
                provenance=record.call_id,
                confidence=1.0 if _text(visible.get("certainty")) == "supported" else 0.6,
            )
        )
    people = visible.get("photo_identities")
    if isinstance(people, (list, tuple)):
        for row in people:
            if not isinstance(row, Mapping):
                continue
            name = _text(row.get("person_name"))
            if name:
                facts.append(
                    Fact(
                        fact_type="photo_identity",
                        value_type=RequirementValueType.PERSON,
                        value=name,
                        scope={"person": name},
                        provenance=record.call_id,
                    )
                )
    return facts


def _text_facts(record: ToolCallRecord) -> list[Fact]:
    source_document = record.tool == "read_source_text"
    full_text = _text(record.model_visible.get("text" if source_document else "full_text"))
    return (
        [
            Fact(
                fact_type="source_text" if source_document else "visible_text",
                value_type=RequirementValueType.DESCRIPTION,
                value=full_text,
                provenance=record.call_id,
            )
        ]
        if full_text
        else []
    )


def _people_facts(record: ToolCallRecord) -> list[Fact]:
    people = record.model_visible.get("people")
    facts: list[Fact] = []
    if isinstance(people, (list, tuple)):
        for row in people:
            if isinstance(row, Mapping):
                name = _text(row.get("person_name"))
                if name:
                    facts.append(
                        Fact(
                            fact_type="photo_people",
                            value_type=RequirementValueType.PERSON,
                            value=name,
                            scope={"person": name},
                            provenance=record.call_id,
                        )
                    )
    return facts


def _profile_facts(record: ToolCallRecord) -> list[Fact]:
    if record.observation.get("found") is not True:
        return []
    name = _text(record.observation.get("person"))
    if not name:
        return []
    raw_aliases = record.observation.get("aliases")
    aliases = raw_aliases if isinstance(raw_aliases, (list, tuple)) else ()
    names = (name, *(_text(alias) for alias in aliases if _text(alias)))
    facts = [
        Fact(
            fact_type="person_profile",
            value_type=RequirementValueType.PERSON,
            value=name,
            scope={"person": name},
            provenance=record.call_id,
            person_names=names,
        )
    ]
    relations = record.observation.get("relationships")
    for relation in relations if isinstance(relations, (list, tuple)) else ():
        if not isinstance(relation, Mapping):
            continue
        other = _text(relation.get("other_name"))
        predicate = _text(relation.get("predicate"))
        if other and predicate:
            raw = relation.get("aliases")
            other_aliases = tuple(_text(alias) for alias in raw) if isinstance(raw, (list, tuple)) else ()
            facts.append(
                Fact(
                    fact_type="person_relationship",
                    value_type=RequirementValueType.PERSON,
                    value=f"{name}是{other}的{predicate}",
                    scope={"person": name},
                    provenance=record.call_id,
                    person_names=(*names, other, *other_aliases),
                )
            )
    description = _text(record.model_visible.get("photo_portrait") or record.model_visible.get("summary"))
    if description:
        facts.append(
            Fact(
                fact_type="person_profile",
                value_type=RequirementValueType.DESCRIPTION,
                value=description,
                scope={"person": name},
                provenance=record.call_id,
                person_names=names,
            )
        )
    return facts


def facts_from_state(state: TaskState) -> tuple[Fact, ...]:
    """把成功的工具记录投影成 facts。失败/被拒的记录不产事实。

    这是**读时投影**而不是另一份状态：``TaskState.records`` 已经是唯一真相，
    再存一份 facts 迟早与它不一致。
    """
    facts: list[Fact] = []
    for record in state.records:
        if record.status != "ok":
            continue
        if record.tool in {"search_memories", "get_result_page"}:
            facts.extend(_retrieval_facts(record))
        elif record.tool == "query_memory_facts":
            facts.extend(_structured_fact(record))
        elif record.tool == "inspect_photo":
            facts.extend(_inspection_facts(record))
        elif record.tool in {"read_photo_text", "read_source_text"}:
            facts.extend(_text_facts(record))
        elif record.tool == "query_photo_people":
            facts.extend(_people_facts(record))
        elif record.tool == "get_person_profile":
            facts.extend(_profile_facts(record))
        elif record.tool == FINAL_ANSWER_TOOL:
            ids = record.observation.get("delivered_asset_ids")
            if isinstance(ids, (list, tuple)) and ids:
                delivered = tuple(str(item) for item in ids)
                source = next(
                    (
                        fact
                        for fact in reversed(facts)
                        if fact.fact_type == "retrieval_media_set" and set(delivered).issubset(fact.value)
                    ),
                    Fact("retrieval_media_set", RequirementValueType.MEDIA_SET, delivered),
                )
                facts.append(
                    replace(
                        source,
                        fact_type="delivered_media_set",
                        value=delivered,
                        provenance=record.call_id,
                        coverage=1.0,
                    )
                )
    return tuple(facts)


def _same_scope_value(expected: str, actual: str) -> bool:
    left = expected.replace(" ", "").lower()
    right = actual.replace(" ", "").lower()
    return bool(left and right) and (left in right or right in left)


def _has_conflicting_values(facts: Sequence[Fact]) -> bool:
    """同一 fact_type、同一 scope 下出现两个不同的值才算冲突。

    不同过滤条件得到两个不同的 count 是正常的（「2017 年 3 张」「2018 年 5 张」），
    把它们当冲突会让所有跨时间统计被判失败。
    """
    grouped: dict[tuple[str, tuple[tuple[str, str], ...]], set[str]] = {}
    for fact in facts:
        if fact.value is None:
            continue
        key = (fact.fact_type, tuple(sorted(fact.scope.items())))
        grouped.setdefault(key, set()).add(str(fact.value))
    return any(len(values) > 1 for values in grouped.values())


def bind_requirement(requirement: EvidenceRequirement, facts: Sequence[Fact]) -> RequirementBinding:
    """单条 requirement 的绑定判定。"""
    if requirement.value_type is None:
        return RequirementBinding(
            requirement.id,
            BindingVerdict.UNKNOWN,
            "requirement 没有声明 value_type",
        )
    if requirement.evidence_type is EvidenceType.SOURCE_TEXT:
        source_calls = tuple(fact.provenance for fact in facts if fact.fact_type == "source_text")
        return RequirementBinding(
            requirement.id,
            BindingVerdict.UNKNOWN,
            "正文已读取，具体答案需结合原文核验" if source_calls else "尚未读取正文",
            source_calls,
            requirement.value_type.value,
        )
    typed = [
        fact
        for fact in facts
        if fact.value_type is requirement.value_type and fact.fact_type != "source_text"
    ]
    if requirement.evidence_type is EvidenceType.PERSON_PROFILE and requirement.value_type in {
        RequirementValueType.PERSON,
        RequirementValueType.DESCRIPTION,
    }:
        typed = [fact for fact in typed if fact.fact_type in {"person_profile", "person_relationship"}]
    if requirement.value_type is RequirementValueType.MEDIA_SET:
        if requirement.evidence_type is EvidenceType.DELIVERED_PHOTO:
            typed = [fact for fact in typed if fact.fact_type == "delivered_media_set"]
        elif requirement.evidence_type is EvidenceType.PHOTO_SET:
            typed = [fact for fact in typed if fact.fact_type == "retrieval_media_set"]
        typed = [fact for fact in typed if isinstance(fact.value, (list, tuple, set)) and len(fact.value) > 0]
    if not typed:
        return RequirementBinding(
            requirement.id,
            BindingVerdict.UNSUPPORTED,
            f"没有工具产出 {requirement.value_type.value} 类型的证据",
            value_type=requirement.value_type.value,
        )
    if requirement.value_type in {
        RequirementValueType.COUNT,
        RequirementValueType.AMOUNT,
        RequirementValueType.DURATION,
        RequirementValueType.BOOLEAN,
    } and _has_conflicting_values(typed):
        return RequirementBinding(
            requirement.id,
            BindingVerdict.CONFLICT,
            "同一值类型出现了互相矛盾的事实",
            tuple(fact.provenance for fact in typed),
            requirement.value_type.value,
        )

    compatible: list[Fact] = []
    saw_scope_conflict = False
    for fact in typed:
        matches = True
        for key, expected in requirement.scope:
            if key in fact.filters_not_applied:
                # 参数里有、工具没应用：这是明确的 scope 失配，不是「信息缺失」。
                matches = False
                saw_scope_conflict = True
                continue
            if key == "person" and fact.person_names:
                if clean_person_name(expected) not in fact.person_names and not all(
                    name in fact.person_names for name in split_person_scope(expected)
                ):
                    matches = False
                    saw_scope_conflict = True
                continue
            actual = fact.scope.get(key, "")
            if not actual:
                # 事实没有这个维度的信息：不能据此说满足，也不能说冲突。
                matches = False
                continue
            if not _same_scope_value(expected, actual):
                matches = False
                saw_scope_conflict = True
        if matches:
            compatible.append(fact)
    if not compatible:
        if saw_scope_conflict:
            return RequirementBinding(
                requirement.id,
                BindingVerdict.SCOPE_MISMATCH,
                "工具事实的 scope 与需求限定不一致",
                tuple(fact.provenance for fact in typed),
                requirement.value_type.value,
            )
        return RequirementBinding(
            requirement.id,
            BindingVerdict.PARTIAL,
            "有该值类型的证据，但缺少需求限定的 scope 信息",
            tuple(fact.provenance for fact in typed),
            requirement.value_type.value,
        )
    if any(fact.coverage < 1.0 for fact in compatible) and all(fact.coverage < 1.0 for fact in compatible):
        return RequirementBinding(
            requirement.id,
            BindingVerdict.PARTIAL,
            "证据只覆盖了部分候选",
            tuple(fact.provenance for fact in compatible),
            requirement.value_type.value,
        )
    return RequirementBinding(
        requirement.id,
        BindingVerdict.SATISFIES,
        "",
        tuple(fact.provenance for fact in compatible),
        requirement.value_type.value,
    )


def bind_requirements(
    requirements: Sequence[EvidenceRequirement], facts: Sequence[Fact]
) -> tuple[RequirementBinding, ...]:
    return tuple(bind_requirement(requirement, facts) for requirement in requirements)


def binding_payload(bindings: Sequence[RequirementBinding]) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": binding.requirement_id,
            "verdict": binding.verdict.value,
            "value_type": binding.value_type,
            "reason": binding.reason,
            "fact_ids": list(binding.fact_ids),
        }
        for binding in bindings
    ]

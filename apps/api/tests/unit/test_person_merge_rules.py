"""``plan_person_merge`` 的四条规则：谁留下、叫什么、算不算已确认、哪些名字变别名。"""

from __future__ import annotations

import pytest

from sentrix.domain.identity.models import EntityRecord
from sentrix.domain.identity.person_merge import (
    MAX_MERGE_SOURCES,
    PersonMergeConflict,
    PersonMergeRuleError,
    plan_person_merge,
)


def entity(
    entity_id: str,
    name: str,
    *,
    status: str = "confirmed",
    scope_id: str = "album1",
    family_role: str | None = None,
    name_state: str = "confirmed",
    entity_type: str = "person",
) -> EntityRecord:
    return EntityRecord(
        id=entity_id,
        scope_id=scope_id,
        entity_type=entity_type,
        canonical_name=name,
        status=status,
        family_role=family_role,
        summary="",
        confidence=0.5,
        identity_state="clustered",
        role_state="unknown",
        name_state=name_state,
        created_at=None,
        updated_at=None,
    )


UNNAMED = {"status": "pending", "name_state": "anonymous"}


def test_the_target_keeps_its_name_and_the_others_become_aliases() -> None:
    plan = plan_person_merge(
        entity("a", "妈妈"),
        [entity("b", "母亲")],
        requested_name=None,
        aliases_by_id={"a": ["阿妈"], "b": ["小芳", "妈妈"]},
    )

    assert plan.canonical_name == "妈妈"
    assert plan.name_confirmed is True
    assert plan.aliases == ("阿妈", "母亲", "小芳")
    assert plan.source_ids == ("b",)


def test_an_unnamed_target_borrows_the_first_named_source() -> None:
    plan = plan_person_merge(
        entity("a", "待确认人物簇 · c1", **UNNAMED),
        [entity("b", "待确认人物簇 · c2", **UNNAMED), entity("c", "王王")],
        requested_name=None,
        aliases_by_id={},
    )

    assert plan.canonical_name == "王王"
    assert plan.status == "confirmed"
    assert plan.aliases == ()


def test_a_named_but_unconfirmed_person_counts_as_named_but_not_confirmed() -> None:
    """「王王」那种状态：改过名、没确认。名字要留下，状态仍是待确认。"""
    plan = plan_person_merge(
        entity("a", "待确认人物簇 · c1", **UNNAMED),
        [entity("b", "王王", status="pending", name_state="confirmed")],
        requested_name=None,
        aliases_by_id={},
    )

    assert plan.canonical_name == "王王"
    assert plan.status == "pending"


def test_nobody_named_keeps_the_internal_name_and_stays_unnamed() -> None:
    plan = plan_person_merge(
        entity("a", "待确认人物簇 · c1", **UNNAMED),
        [entity("b", "待确认人物簇 · c2", **UNNAMED)],
        requested_name=None,
        aliases_by_id={},
    )

    assert plan.canonical_name == "待确认人物簇 · c1"
    assert plan.name_confirmed is False


def test_explicit_name_and_first_family_role_win() -> None:
    plan = plan_person_merge(
        entity("a", "阿明"),
        [entity("b", "明明", family_role="孩子")],
        requested_name="  小明 ",
        aliases_by_id={},
    )

    assert plan.canonical_name == "小明"
    assert plan.family_role == "孩子"
    assert plan.aliases == ("阿明", "明明")


@pytest.mark.parametrize(
    ("sources", "error"),
    [
        ([], PersonMergeRuleError),
        ([entity("a", "自己")], PersonMergeRuleError),
        ([entity("b", "别处的人", scope_id="album2")], PersonMergeConflict),
        ([entity("b", "旧人", status="superseded")], PersonMergeConflict),
        ([entity("b", "西湖", entity_type="place")], PersonMergeConflict),
        ([entity(f"s{index}", f"人{index}") for index in range(MAX_MERGE_SOURCES + 1)], PersonMergeRuleError),
    ],
)
def test_plans_that_cannot_stand(sources: list[EntityRecord], error: type[Exception]) -> None:
    with pytest.raises(error):
        plan_person_merge(entity("a", "妈妈"), sources, requested_name=None, aliases_by_id={})


def test_an_internal_cluster_name_is_not_an_acceptable_explicit_name() -> None:
    with pytest.raises(PersonMergeRuleError):
        plan_person_merge(
            entity("a", "妈妈"),
            [entity("b", "母亲")],
            requested_name="待确认人物簇 · c9",
            aliases_by_id={},
        )

"""人物合并：用户说「这几个其实是同一个人」。

与簇审阅里的 ``merge`` 不是一回事。那条是**簇级**的：只挪人脸实例，并且两个已确认
的人不许合并（「合并等于丢掉一个名字，必须由人来决定」）。这里就是「由人来决定」的
那条路——名录里同一个人因为年龄、光照或算法差异被拆成了几个人物，用户把他们选在一起，
指定留谁、叫什么。

编排分三段，顺序是硬约束：

1. **算**（``plan_person_merge``，纯规则）：名字、状态、家庭角色、别名；
2. **写**（``PersonMergeRepository.merge_people``，一个事务）：全部数据改挂到目标；
3. **重算**：别名属性、目标簇原型、新确认时补提及、外观取样、事件参与者与重分段。
   这一段读的正是第 2 段写下的提及与簇，排在前面会算出一份没有人的结果。

**幂等**：已经并进同一个目标的来源会被跳过；全部跳过时什么都不写，直接返回目标。
重放一次「刚成功的合并」得到的是同一个人，而不是 409。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.application.identity.observe_appearance import ObserveAppearance
from sentrix.application.identity.review_cluster import (
    ClusterNotFoundError,
    InvalidReviewRequestError,
    ReviewConflictError,
)
from sentrix.application.memories.assign_events import ConfirmedPersonEvents
from sentrix.domain.identity.models import (
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_SUPERSEDED,
    EntityRecord,
)
from sentrix.domain.identity.person_merge import (
    PersonMergeConflict,
    PersonMergeRuleError,
    plan_person_merge,
)
from sentrix.domain.identity.ports import EntityRepository, FaceRepository, PersonMergeRepository


@dataclass(frozen=True, slots=True)
class MergePeopleResult:
    entity: EntityRecord
    merged_person_ids: tuple[str, ...]
    moved_face_count: int = 0
    moved_relationship_count: int = 0
    dropped_relationship_count: int = 0


class MergePeople:
    def __init__(
        self,
        *,
        entities: EntityRepository,
        faces: FaceRepository,
        merges: PersonMergeRepository,
        events: ConfirmedPersonEvents,
        appearance: ObserveAppearance,
    ) -> None:
        self._entities = entities
        self._faces = faces
        self._merges = merges
        # 与簇确认同一对后续，**不是可选回调**：合并后不刷事件参与者，按人找照片仍会
        # 把来源那一半事件算到一个已经不存在的人头上。
        self._events = events
        self._appearance = appearance

    def __call__(
        self,
        *,
        target_person_id: str,
        source_person_ids: Sequence[str],
        name: str | None = None,
    ) -> MergePeopleResult:
        target_id = str(target_person_id or "").strip()
        requested = [str(item or "").strip() for item in source_person_ids]
        if not target_id or not requested or any(not item for item in requested):
            raise InvalidReviewRequestError("target_person_id and source_person_ids are required")
        source_ids = list(dict.fromkeys(requested))
        if target_id in source_ids:
            raise InvalidReviewRequestError("a person cannot be merged with itself")

        target = self._require_person(target_id)
        if target.status == ENTITY_STATUS_SUPERSEDED:
            raise ReviewConflictError("target person has already been merged into someone else")

        pending_sources: list[EntityRecord] = []
        replayed: list[str] = []
        for source_id in source_ids:
            source = self._require_person(source_id)
            if source.status == ENTITY_STATUS_SUPERSEDED:
                if self._merges.merged_into(source_id) == target_id:
                    replayed.append(source_id)
                    continue
                raise ReviewConflictError(f"person {source_id} has already been merged into someone else")
            pending_sources.append(source)
        if not pending_sources:
            return MergePeopleResult(entity=target, merged_person_ids=tuple(replayed))

        aliases_by_id = {
            entity.id: self._entities.person_aliases(entity.id) for entity in (target, *pending_sources)
        }
        try:
            plan = plan_person_merge(
                target, pending_sources, requested_name=name, aliases_by_id=aliases_by_id
            )
        except PersonMergeRuleError as error:
            raise InvalidReviewRequestError(str(error)) from error
        except PersonMergeConflict as error:
            raise ReviewConflictError(str(error)) from error

        if plan.status == ENTITY_STATUS_CONFIRMED and plan.name_confirmed:
            # 合并后的名字撞上另一个已确认的人：再造一个同名的已确认人物就是 1.0 同名合并
            # 要防的那件事。让用户把他一起选进来，而不是在这里替用户再并一个。
            match = self._entities.find_confirmed_person_by_name(plan.scope_id, plan.canonical_name)
            participants = {target_id, *plan.source_ids}
            if match is not None and not match.via_alias and match.entity_id not in participants:
                raise ReviewConflictError(
                    "another confirmed person already has this name; select them too to merge"
                )

        outcome = self._merges.merge_people(plan)

        self._entities.set_person_aliases(target_id, plan.aliases)
        if outcome.target_cluster_id is not None:
            self._faces.refresh_prototypes(outcome.target_cluster_id)
            if plan.status == ENTITY_STATUS_CONFIRMED:
                # 来源或目标原先是待确认时，它的脸没有提及；确认后的人必须每张脸都有提及，
                # 这是「按人找照片」唯一的桥。``link_confirmed_mention`` 是 INSERT OR IGNORE，
                # 已有的提及不受影响。
                for instance in self._faces.list_instances(outcome.target_cluster_id):
                    self._entities.link_confirmed_mention(
                        target_id,
                        instance.observation_id,
                        face_instance_id=instance.id,
                        confidence=instance.detection_confidence,
                    )
        if plan.status == ENTITY_STATUS_CONFIRMED:
            self._appearance(target_id)
            self._events.refresh_participants(outcome.observation_ids)
            self._events.resegment(target_id)

        merged = self._entities.get(target_id)
        if merged is None:  # pragma: no cover — 事务刚写过它，读不到就是库被并发删了
            raise ClusterNotFoundError("person not found")
        return MergePeopleResult(
            entity=merged,
            merged_person_ids=(*replayed, *plan.source_ids),
            moved_face_count=outcome.moved_face_count,
            moved_relationship_count=outcome.moved_relationship_count,
            dropped_relationship_count=outcome.dropped_relationship_count,
        )

    def _require_person(self, person_id: str) -> EntityRecord:
        entity = self._entities.get(person_id)
        if entity is None:
            raise ClusterNotFoundError(f"person not found: {person_id}")
        return entity

"""簇审阅：确认 / 拒绝 / 合并 / 拆分。

逐分支移植 1.0 ``backend/db.py`` 的 ``confirm_face_cluster`` :5152、
``_merge_cluster_into_person`` :5194、``reject_face_cluster`` :5579、
``merge_face_clusters`` :4776、``split_face_instance`` :4797、``delete_person_candidate``
:5290。四件事回答的是同一个问题的四个答案：这一簇脸是不是一个人？
是（confirm）／不是（reject）／和另一簇是同一个（merge）／里面混进了别人（split）。

**确认之后要跟着改什么，这一层不自己算。** 1.0 的 ``confirm_face_cluster``
在同一个函数里既写身份（entities / mentions）又刷事件参与者、重分段、重建人物画像。
2.0 把那几件事拆了出去：事件侧是 ``ConfirmedPersonEvents``（Task 81），外观证据是
``ObserveAppearance``（Task 85d），人物画像是 Task 86（S5c）。它们读的正是这里写的
提及，直接内联就是一个循环。``confirm`` 与 ``_merge_into_person`` 在写完提及之后各调
一次前两个，**依赖是构造参数、不是可选回调**——S4 栽过「零调用的纯规则」，一个现在
没人传的钩子和它同类。

**顺序在这里是硬约束**：提及先写、投影后刷。反过来外部键（``entity_mentions``）是空的，
参与者投影刷出来的就是一份没有人的名单——不报错，只是事件摘要里永远没有名字。

**四件里写审计的是 merge 与 split**（外加 confirm 走同名合并那一路，写的也是
``face_cluster_merge``）。reject 写不了：它删的正是那个实体的 ``entity_revisions`` 行。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sentrix.application.identity.observe_appearance import RequestAppearance
from sentrix.application.memories.assign_events import ConfirmedPersonEvents
from sentrix.domain.errors import DomainError
from sentrix.domain.identity.models import (
    CLUSTER_STATUS_REJECTED,
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    PENDING_CLUSTER_SUMMARY,
    EntityRecord,
    FaceClusterRecord,
    pending_cluster_name,
)
from sentrix.domain.identity.ports import EntityRepository, FaceRepository
from sentrix.domain.memories.facts import Fact, FactWriteAction, decide_fact_write
from sentrix.domain.memories.ports import FactRepository

# 1.0 ``db.py:5169`` 的字面量。它是**事实的谓词**，会落进 facts 表被检索命中，
# 不是一个展示用的标签。
FAMILY_ROLE_PREDICATE = "家庭角色"
# 1.0 ``db.py:5170`` 的 ``confidence=1.0``：用户确认出来的角色是确定的。
FAMILY_ROLE_CONFIDENCE = 1.0
# 1.0 ``db.py:5167`` 的 f-string，一字不差。
CONFIRMED_SUMMARY = "已确认人物，出现在 {} 条观察中"

# 1.0 的 ``_record_entity_revision`` 调用点里的三个字面量。合并那一路有两个来源：
# 用户主动合并（``user_merge``）与同名并入已有的人（``user_name_merge``）——
# 界面上这两件事的用户意图不同，审计行必须区分得开。
REVISION_FACE_CLUSTER_MERGE = "face_cluster_merge"
REVISION_FACE_CLUSTER_SPLIT = "face_cluster_split"
SOURCE_USER_MERGE = "user_merge"
SOURCE_USER_SPLIT = "user_split"
SOURCE_USER_NAME_MERGE = "user_name_merge"


class ClusterReviewError(DomainError):
    """簇审阅的领域错误。

    ``code`` 与 ``status_code`` 都是**产品契约**的一部分（400 / 404 / 409），Task 75 的
    路由只把它们放行、由 ``api/errors.py`` 那一个处理器映射 HTTP。定在这里而不是路由里：
    它们描述的是产品行为（「已确认的人不能被拒绝」是冲突，不是参数错），定在路由里就意味
    着只有起了 HTTP 才验得到。

    基类是 ``domain/errors.py`` 的 ``DomainError``——统一口径见那里；这里再把 ``ValueError``
    / ``LookupError`` 混进来，是为了不打断既有的 ``pytest.raises(ValueError)`` 用法。
    """

    code = "invalid_request"
    status_code = 400


class InvalidReviewRequestError(ClusterReviewError, ValueError):
    code = "invalid_request"
    status_code = 400


class ClusterNotFoundError(ClusterReviewError, LookupError):
    code = "not_found"
    status_code = 404


class ReviewConflictError(ClusterReviewError, ValueError):
    code = "conflict"
    status_code = 409


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    """确认的结论。``merged_into`` 非空表示这次确认把簇并进了一个已存在的人。

    1.0 在这种情况下把整个实体详情拼回来再塞一个 ``merged_into`` 字段；2.0 只回
    「是谁」——详情是读侧的事（Task 75），用例不该顺手做一个投影。
    """

    entity: EntityRecord
    merged_into: str | None = None


def _new_fact_id() -> str:
    """1.0 ``make_id("fact")``。与 ``application/memories/maintain_facts.py`` 同一个格式
    ——两条写事实的路径必须给出同一种 id，否则库里会有两种长相的事实。"""
    return f"fact_{uuid.uuid4().hex[:12]}"


def _dedupe(values: list[str]) -> list[str]:
    """1.0 到处在用的 ``list(dict.fromkeys(...))``：保序去重。"""
    return list(dict.fromkeys(value for value in values if value))


class ReviewCluster:
    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        facts: FactRepository,
        events: ConfirmedPersonEvents,
        appearance: RequestAppearance,
    ) -> None:
        self._faces = faces
        self._entities = entities
        self._facts = facts
        # 确认之后的外观取样（Task 85d）。与 ``events`` 同理，**不是可选回调**——
        # 1.0 的 ``_refresh_confirmed_person`` 无条件调它。生产装配的是「入队」：
        # 每个事件一次视觉模型调用（约 10 秒），不能让确认请求等它（2026-11-20）。
        self._appearance = appearance
        # 确认之后的事件侧（参与者投影 + 重分段）。**不是可选参数**：一次确认如果不刷
        # 投影，事件里就查不到这个人，而那是「按人找照片」唯一的桥——少接一处不会报错。
        self._events = events

    # ---- 确认 --------------------------------------------------------------------

    def confirm(
        self,
        cluster_id: str,
        *,
        name: str,
        family_role: str | None = None,
        merge_same_name: bool = True,
    ) -> ConfirmResult:
        """确认一簇脸是某个人。

        ``merge_same_name=False``：用户已经在界面上答过「不是同一个人，只是同名」，
        这时不再按名字并进那个已确认的人，而是照常确认成一个独立的人（2026-11-12）。
        默认 ``True`` 保持 1.0 的同名并入。

        名字先判（400 早于 404）：1.0 在路由里就是这个顺序，两处都错时报的是名字错。
        ``family_role`` 会 ``strip`` 后把空串折成 ``None``——那是 1.0 路由做的事
        （``app.py:1978``），2.0 收进用例：它是一个关于值的规则，不是传输格式。
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise InvalidReviewRequestError("entity name is required")
        role = str(family_role or "").strip() or None
        cluster = self._require_cluster(cluster_id)
        scope_id = cluster.scope_id or DEFAULT_SCOPE_ID

        existing = self._entities.find_confirmed_person_by_name(scope_id, clean_name)
        # ``existing.entity_id != cluster.entity_id`` 这一半是**修 1.0 的缺陷**（见
        # s5-deviations.md）：对一个已经确认过的簇用**它自己的名字**再确认一次时，
        # 名字查询命中的就是它自己的实体，走合并分支于是把簇置成 rejected、
        # member_count 归零——**自己把自己删了**。1.0 的 ``confirm_face_cluster``
        # （``db.py:5157``）漏了这条守卫，而同一作者在 ``confirm_person_entity``
        # （``db.py:5251``）里写了它；这不是新行为，是把 1.0 自己那条判据补齐。
        if merge_same_name and existing is not None and existing.entity_id != cluster.entity_id:
            return self._merge_into_person(cluster, existing.entity_id)

        instances = self._faces.list_instances(cluster.id)
        observation_ids = _dedupe([item.observation_id for item in instances])
        summary = CONFIRMED_SUMMARY.format(len(set(observation_ids)))

        entity = self._entities.get(cluster.entity_id) if cluster.entity_id else None
        if entity is None:
            entity = self._entities.create_person(
                clean_name,
                status=ENTITY_STATUS_CONFIRMED,
                family_role=role,
                confidence=1.0,
                summary=summary,
                scope_id=scope_id,
            )
        else:
            entity = self._entities.confirm_person(
                entity.id, name=clean_name, family_role=role, summary=summary
            )
        self._faces.confirm_cluster(cluster.id, entity.id)
        for instance in instances:
            self._entities.link_confirmed_mention(
                entity.id,
                instance.observation_id,
                face_instance_id=instance.id,
                confidence=instance.detection_confidence,
            )
        if role:
            self._write_family_role(scope_id, clean_name, role, observation_ids)
        # 1.0 ``db.py:5179-5180`` 的两句。**必须排在上面那些提及之后**：投影读的就是
        # ``entity_mentions``，提及还没写出来时刷出来的是一份空名单。
        # 1.0 ``_refresh_confirmed_person`` 的第一件事就是它，排在事件侧之前。
        # **必须排在提及之后**：取样那条 SQL 与仓储的第三重守卫读的都是
        # ``entity_mentions``，提及还没写出来时它一张脸都挑不到——不报错，只是这个
        # 人永远没有外观证据。与下面那两句是同一个约束。
        self._appearance(entity.id)
        self._events.refresh_participants(observation_ids)
        self._events.resegment(entity.id)
        return ConfirmResult(entity=entity, merged_into=None)

    def _merge_into_person(self, cluster: FaceClusterRecord, target_entity_id: str) -> ConfirmResult:
        """同名并入一个已确认的人（1.0 ``_merge_cluster_into_person``）。

        人名已经被人认过，再建第二个同名的就是错的。实例改挂到对方的已确认簇上，
        提及写给对方，源簇退休但**归属改成对方**（``reject`` 那条路径不这么干）。
        """
        target = self._entities.get(target_entity_id)
        if target is None:
            raise ClusterNotFoundError("confirmed person not found")
        if (cluster.scope_id or DEFAULT_SCOPE_ID) != (target.scope_id or DEFAULT_SCOPE_ID):
            raise ReviewConflictError("face cluster and person must belong to the same memory space")

        instances = self._faces.list_instances(cluster.id)
        target_cluster = self._faces.confirmed_cluster_for_entity(target.id)
        for instance in instances:
            if target_cluster is not None:
                self._faces.move_instance(instance.id, target_cluster.id)
            self._entities.link_confirmed_mention(
                target.id,
                instance.observation_id,
                face_instance_id=instance.id,
                confidence=instance.detection_confidence,
            )
        self._faces.retire_cluster(cluster.id, entity_id=target.id)
        if target_cluster is not None:
            self._faces.refresh_prototypes(target_cluster.id)
        self._entities.record_revision(
            target.id,
            REVISION_FACE_CLUSTER_MERGE,
            cluster.id,
            target_cluster.id if target_cluster is not None else None,
            source=SOURCE_USER_NAME_MERGE,
        )
        # 同名合并那一路在 1.0 里也刷投影与重分段（``db.py:5233-5234``），目标换成了
        # 被并入的那个人——实例挂到他名下了，他的事件才是要改的那些。
        self._appearance(target.id)
        self._events.refresh_participants([instance.observation_id for instance in instances])
        self._events.resegment(target.id)
        return ConfirmResult(entity=target, merged_into=target.id)

    def _write_family_role(self, scope_id: str, name: str, role: str, observation_ids: list[str]) -> None:
        """写一条「(名字, 家庭角色, 角色)」的事实。

        走的是与 ``MaintainFacts`` 同一套纯规则：同值只合证据抬置信度，异值落
        ``pending`` 等人确认——改口要人认，不能悄悄改掉。
        """
        existing = self._facts.active_for(scope_id=scope_id, subject=name, predicate=FAMILY_ROLE_PREDICATE)
        write = decide_fact_write(
            existing=existing,
            object_value=role,
            evidence_ids=tuple(observation_ids),
            confidence=FAMILY_ROLE_CONFIDENCE,
        )
        if write.action is FactWriteAction.REINFORCE:
            assert existing is not None  # decide_fact_write 只在 existing 非空时给 REINFORCE
            self._facts.reinforce(existing.id, evidence_ids=write.evidence_ids, confidence=write.confidence)
            return
        self._facts.insert(
            Fact(
                id=_new_fact_id(),
                scope_id=scope_id,
                subject=name,
                predicate=FAMILY_ROLE_PREDICATE,
                object=role,
                status=write.status,
                confidence=write.confidence,
                evidence_ids=write.evidence_ids,
                revision=write.revision,
                supersedes_fact_id=write.supersedes_fact_id,
            )
        )

    # ---- 拒绝 --------------------------------------------------------------------

    def reject(self, cluster_id: str) -> str | None:
        """这簇脸不是人：删掉候选实体与它的派生行，人脸实例留着。

        返回被删掉的实体 id；簇没有实体时返回 ``None`` 且**什么都不做**——1.0 就是这个
        行为（``reject_face_cluster`` 的最后一行是个条件表达式），那时簇还留在库里。
        """
        cluster = self._require_cluster(cluster_id)
        if cluster.entity_id is None:
            return None
        entity = self._entities.get(cluster.entity_id)
        if entity is not None and entity.status == ENTITY_STATUS_CONFIRMED:
            raise ReviewConflictError("confirmed person clusters cannot be rejected")
        self._entities.delete_candidate(cluster.entity_id)
        return cluster.entity_id

    # ---- 合并 / 拆分 -------------------------------------------------------------

    def merge(
        self,
        *,
        target_cluster_id: str,
        source_cluster_id: str,
        source: str = SOURCE_USER_MERGE,
    ) -> FaceClusterRecord:
        """两个簇其实是同一个人：把源簇的实例整体挪到目标簇。

        只改 ``face_instances.cluster_id`` 一列——实体、提及、原型都跟着走，没有第二份
        需要同步的副本。源簇转 rejected（**归属不变**，与同名合并那条路不同），
        目标簇的原型重算。
        """
        if target_cluster_id == source_cluster_id:
            # 自己合自己不是错误，是一个空操作。返回当前状态，不写审计。
            return self._require_cluster(target_cluster_id)
        target = self._faces.get_cluster(target_cluster_id)
        other = self._faces.get_cluster(source_cluster_id)
        if (
            target is None
            or other is None
            or target.status == CLUSTER_STATUS_REJECTED
            or other.status == CLUSTER_STATUS_REJECTED
        ):
            raise ClusterNotFoundError("face cluster not found")
        if (target.scope_id or DEFAULT_SCOPE_ID) != (other.scope_id or DEFAULT_SCOPE_ID):
            raise ReviewConflictError("face clusters must belong to the same memory space")
        target_entity = self._entities.get(target.entity_id) if target.entity_id else None
        source_entity = self._entities.get(other.entity_id) if other.entity_id else None
        if (
            target_entity is not None
            and source_entity is not None
            and target_entity.id != source_entity.id
            and target_entity.status == ENTITY_STATUS_CONFIRMED
            and source_entity.status == ENTITY_STATUS_CONFIRMED
        ):
            # 两个都已经被人认过名字的人，合并等于丢掉一个名字，必须由人来决定。
            raise ReviewConflictError("two confirmed people cannot be merged automatically")

        self._faces.move_instances(other.id, target.id)
        self._faces.retire_cluster(other.id)
        self._faces.refresh_prototypes(target.id)
        if target.entity_id:
            self._entities.record_revision(
                target.entity_id,
                REVISION_FACE_CLUSTER_MERGE,
                other.id,
                target.id,
                source=source,
            )
        return self._require_cluster(target.id)

    def split(
        self,
        cluster_id: str,
        *,
        face_instance_id: str,
        source: str = SOURCE_USER_SPLIT,
    ) -> FaceClusterRecord:
        """这张脸不是这一簇的人：给它单开一个待确认的簇。

        新簇与它的 pending 实体一起建（与在线匹配建新簇是同一条规则、同一句文案）。
        审计写在**原簇的实体**上，证据是那张脸的 id——用户看到的两个簇要能解释得清。
        """
        cluster = self._faces.get_cluster(cluster_id)
        instance = self._faces.find_instance(cluster_id, face_instance_id)
        if cluster is None or instance is None:
            raise ClusterNotFoundError("face instance not found in cluster")
        scope_id = cluster.scope_id or DEFAULT_SCOPE_ID

        new_cluster = self._faces.create_cluster(
            instance.embedding, confidence=instance.quality, scope_id=scope_id
        )
        entity = self._entities.create_person(
            pending_cluster_name(new_cluster.id),
            status=ENTITY_STATUS_PENDING,
            confidence=instance.quality,
            summary=PENDING_CLUSTER_SUMMARY,
            scope_id=scope_id,
        )
        self._faces.link_entity(new_cluster.id, entity.id)
        self._faces.move_instance(instance.id, new_cluster.id)
        self._faces.refresh_prototypes(cluster.id)
        self._faces.refresh_prototypes(new_cluster.id)
        if cluster.entity_id:
            self._entities.record_revision(
                cluster.entity_id,
                REVISION_FACE_CLUSTER_SPLIT,
                cluster.id,
                new_cluster.id,
                source=source,
                evidence_ids=(instance.id,),
            )
        # 重取：两次 refresh 改过 member_count 与代表向量，内存里那份已经过期。
        return self._require_cluster(new_cluster.id)

    # ---- 共用 --------------------------------------------------------------------

    def _require_cluster(self, cluster_id: str) -> FaceClusterRecord:
        cluster = self._faces.get_cluster(cluster_id)
        if cluster is None:
            raise ClusterNotFoundError(f"face cluster not found: {cluster_id}")
        return cluster

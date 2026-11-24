"""事件归属用例：把一条新观察折进最合适的事件，或为它开一个新事件。

对应 1.0 ``backend/db.py:2287-2330`` 的 ``merge_observation_into_event`` +
``:1962-1990`` 的 ``_event_candidates``。流程一模一样：列本 scope 的活动事件 → 6 小时
硬窗过滤 → 八分量打分 → 取最高分 → ``>= 0.50`` 折进去，否则新建。

**人物桥是第二硬的约束：人脸必须先于事件归并。** 与已确认的人相像的脸在导入链的
前一步写下 ``entity_mentions``，这里才有 ``person_score`` 可算。顺序写反**不报错**——
两个来源都查得到、只是都返回空集，``person_score`` 恒为 0、``corroborated`` 更容易为假，
事件照分照建，只是分错了（对照测试在 Task 79/80）。

**归并顺序是硬约束：视觉向量必须先写。** ``score_event_candidate`` 的 ``visual_boost``
（权重 0.05）与 ``split_guard`` 的判定都来自 ``_event_visual_similarity``，而它读的是
``memory_vectors`` 里 ``space='visual' & source_type='asset'`` 的向量。1.0 在
``pipeline.process()`` 里显式把 ``upsert_vector`` 排在 ``merge_observation_into_event``
**之前**（``pipeline.py:315-325``），并且写了注释「Event selection needs the image
vector before an event exists」。把顺序倒过来不会报错——``_event_visual_similarity``
会返回 ``(0.0, False)``，于是 ``visual_boost`` 恒为 0、``split_guard`` 因为
``not visual_available`` 更容易命中——事件照分、照建，只是分错了。
见 ``s4-deviations.md`` §3 与 Task 56。

**本文件还收着「确认一个人之后事件侧要改什么」**（``ConfirmedPersonEvents``）：参与者
投影与重分段。它们与归属共用同一套锚点与八分量打分——重分段问的正是「这两个事件按
同一套规则算不算一场」，分开写会得到两套取值规则慢慢地漂开。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.identity.models import ENTITY_STATUS_CONFIRMED
from sentrix.domain.identity.ports import EntityRepository, RelationshipRepository
from sentrix.domain.media.models import Asset, Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import (
    CANDIDATE_THRESHOLD,
    CANDIDATE_WINDOW_HOURS,
    PARTICIPANT_ROLE_VISIBLE_SUBJECT,
    VISIBLE_SUBJECT_CONFIDENCE,
    EventAnchor,
    EventAnchorSet,
    EventCandidateScore,
    EventParticipantRecord,
    EventRecord,
    ObservationAnchor,
    VisualSignal,
    normalise_tokens,
    rank_candidates,
    score_event_candidate,
    within_candidate_window,
)
from sentrix.domain.memories.ports import EventParticipantRepository, EventRepository
from sentrix.domain.places.ports import AssetPlaceRepository
from sentrix.domain.retrieval.ports import VectorRepository

# 1.0 ``db.py:2221`` 的 ``list_events(1000, scope_id)``。活动事件超过这个数时
# 打分只在前 1000 个里做——1.0 就是这么写死的，2.0 不变，但把它写成常量而不是
# 函数里的字面量，免得被当成随手可调的数。
ACTIVE_EVENT_SCAN_LIMIT = 1000

# 1.0 ``db.py:2515`` / ``:2523-2525`` 那条共现**建议**的三个字面量。写成常量是因为
# 它们是产品行为：改一个数，所有共现边的置信度一起偏，而且不报错。
COOCCURRENCE_PREDICATE = "共同出现"
COOCCURRENCE_STATUS = "pending"
COOCCURRENCE_BASE_CONFIDENCE = 0.45
COOCCURRENCE_STEP = 0.1
COOCCURRENCE_CEILING = 0.9


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def observation_anchor(
    observation: Observation, asset: Asset, *, people: frozenset[str]
) -> ObservationAnchor:
    """一条观察的锚点。归属与重分段共用，避免同一份取值规则写两遍。

    **不收 ``Place``**：1.0 的 ``_event_anchor`` 只用 asset 上的 GPS 文本当
    ``location``，解析出来的地名是事件的 ``place`` 列用的，不参与打分。
    """
    return ObservationAnchor(
        captured_at=asset.captured_at or observation.captured_at,
        location=asset.captured_location.as_text() if asset.captured_location else "",
        gps=asset.captured_location,
        visual_place=observation.place.strip().lower(),
        visual_event_type=observation.event_type.strip().lower(),
        activity=observation.activity,
        objects=normalise_tokens(observation.objects),
        people=people,
    )


def event_anchors(
    observation_ids: Sequence[str], *, observations: ObservationRepository, assets: AssetRepository
) -> tuple[EventAnchor, ...]:
    """一个事件名下所有观察的锚点。取不到观察 / 资产的成员直接跳过（1.0 同样 continue）。"""
    anchors: list[EventAnchor] = []
    for observation_id in observation_ids:
        member = observations.get(observation_id)
        if member is None:
            continue
        asset = assets.get(member.asset_id)
        if asset is None:
            continue
        anchors.append(
            EventAnchor(
                captured_at=asset.captured_at or member.captured_at,
                location=asset.captured_location.as_text() if asset.captured_location else "",
                gps=asset.captured_location,
                visual_place=member.place.strip().lower(),
                visual_event_type=member.event_type.strip().lower(),
            )
        )
    return tuple(anchors)


def anchor_set_for_event(
    event: EventRecord,
    observation_ids: Sequence[str],
    *,
    observations: ObservationRepository,
    assets: AssetRepository,
    people: frozenset[str],
) -> EventAnchorSet:
    """候选事件的既有证据。归属与重分段读的是同一组字段，所以构造也只有这一处。"""
    return EventAnchorSet(
        event_id=event.id,
        time_start=event.time_start,
        activity=event.activity,
        anchors=event_anchors(observation_ids, observations=observations, assets=assets),
        object_sets=tuple(
            normalise_tokens(item.objects)
            for item in (observations.get(observation_id) for observation_id in observation_ids)
            if item is not None and item.objects
        ),
        people=people,
    )


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    event: EventRecord
    score: EventCandidateScore | None
    created: bool
    candidate_count: int


class EventVisualSimilarity:
    """候选观察与某事件之间最强的资产级视觉相似度。

    对应 1.0 ``db.py:2082-2110`` 的 ``_event_visual_similarity``：

    1. 先取候选观察的 asset 向量（按 ``updated_at DESC`` 取最新一条）；
    2. 再取事件里所有观察的 asset 向量，**要求 ``model_name`` 相同**且维度一致；
    3. 取余弦最大。

    第 2 条的模型名限定是 1.0 就有的（换过嵌入模型之后，旧向量与新向量算余弦是
    纯粹的噪声），2.0 保留。
    """

    def __init__(
        self,
        *,
        vectors: VectorRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
    ) -> None:
        self._vectors = vectors
        self._observations = observations
        self._assets = assets

    def for_observation(self, observation: Observation, event_observation_ids: Sequence[str]) -> VisualSignal:
        from sentrix.domain.retrieval.similarity import cosine_similarity

        incoming = self._vectors.get(space="visual", source_type="asset", source_id=observation.asset_id)
        if incoming is None or not incoming.vector:
            return VisualSignal()
        best: float | None = None
        for observation_id in event_observation_ids:
            other = self._observations.get(observation_id)
            if other is None:
                continue
            vector = self._vectors.get(
                space="visual",
                source_type="asset",
                source_id=other.asset_id,
                model_name=incoming.model_name,
            )
            if vector is None or len(vector.vector) != len(incoming.vector):
                continue
            score = cosine_similarity(incoming.vector, vector.vector)
            best = score if best is None else max(best, score)
        if best is None:
            return VisualSignal()
        return VisualSignal(similarity=best, available=True)


class AssignEvents:
    def __init__(
        self,
        *,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        places: AssetPlaceRepository,
        visual_similarity: EventVisualSimilarity,
        confirmed_people: Callable[[str], frozenset[str]],
        confirmed_event_people: Callable[[str], frozenset[str]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        self._places = places
        self._visual = visual_similarity
        self._clock = clock
        # 两个来源**都没有默认值**。S4 给它们留过「默认恒回空集」的默认值，理由是那时
        # 没有 entity_mentions；代价是「忘了接线」与「真的没有人」在类型上长得一模一样，
        # 而 person_score（权重 0.20）恒为 0 不会报错、只会让事件分错——正是本仓库一路
        # 在防的那类静默。Task 80 把默认值删掉，装配点少写一个就是 TypeError。
        #
        # 两个来源不是冗余：``people`` 是**候选观察**里的人，``event_people`` 是**事件
        # 名下所有观察**里的人，打分要的是两者的交集（``_person_score``）。
        self._confirmed_people = confirmed_people
        self._confirmed_event_people = confirmed_event_people

    def execute(self, observation: Observation) -> AssignmentResult:
        asset = self._assets.get(observation.asset_id)
        if asset is None:
            raise KeyError(observation.asset_id)
        scope_id = asset.scope_id
        anchor = self._anchor(observation, asset)

        scored: list[tuple[EventAnchorSet, EventCandidateScore]] = []
        for event in self._events.list_active(scope_id, limit=ACTIVE_EVENT_SCAN_LIMIT):
            if not within_candidate_window(anchor.captured_at, event.time_start):
                continue
            observation_ids = self._events.observation_ids(event.id)
            anchor_set = self._event_anchor_set(event, observation_ids)
            visual = self._visual.for_observation(observation, observation_ids)
            scored.append((anchor_set, score_event_candidate(anchor=anchor, event=anchor_set, visual=visual)))

        ranked = rank_candidates(scored)
        best = ranked[0] if ranked and ranked[0][1].is_candidate() else None
        candidates = [item for item in ranked if item[1].is_candidate()]

        if best is None:
            event = self._create(observation, asset, anchor, [item for item in ranked])
            return AssignmentResult(event=event, score=None, created=True, candidate_count=0)

        winner, score = best
        event = self._fold(observation, winner, score, asset, anchor)
        return AssignmentResult(event=event, score=score, created=False, candidate_count=len(candidates))

    # ---- 内部 ----------------------------------------------------------------

    def _anchor(self, observation: Observation, asset: Asset) -> ObservationAnchor:
        return observation_anchor(observation, asset, people=self._confirmed_people(observation.id))

    def _event_anchors(self, observation_ids: Sequence[str]) -> tuple[EventAnchor, ...]:
        return event_anchors(observation_ids, observations=self._observations, assets=self._assets)

    def _event_anchor_set(self, event: EventRecord, observation_ids: Sequence[str]) -> EventAnchorSet:
        return anchor_set_for_event(
            event,
            observation_ids,
            observations=self._observations,
            assets=self._assets,
            people=self._confirmed_event_people(event.id),
        )

    def _create(
        self,
        observation: Observation,
        asset: Asset,
        anchor: ObservationAnchor,
        diagnostics: Sequence[tuple[EventAnchorSet, EventCandidateScore]],
    ) -> EventRecord:
        place = self._places.get(asset.id)
        captured = anchor.captured_at
        event = self._events.create(
            event_id=_new_event_id(),
            scope_id=asset.scope_id,
            time_start=captured,
            time_end=captured,
            place=place.display() if place else "",
            admin_district=place.admin.district if place else "",
            activity=observation.activity,
            summary=observation.caption or observation.activity,
            confidence=observation.confidence,
            aggregation_score=0.0,
            aggregation_breakdown={
                "selected": False,
                # 1.0 ``db.py:2322``：候选多于一个就记 ambiguity，供人工复核。
                "ambiguity": len(diagnostics) > 1,
                "split_guard": next(
                    (score.split_guard for _, score in diagnostics if score.split_guard), None
                ),
                "candidates": [score.breakdown() for _, score in diagnostics[:5]],
            },
        )
        self._events.attach(event.id, observation.id)
        return event

    def _fold(
        self,
        observation: Observation,
        winner: EventAnchorSet,
        score: EventCandidateScore,
        asset: Asset,
        anchor: ObservationAnchor,
    ) -> EventRecord:
        place = self._places.get(asset.id)
        self._events.attach(winner.event_id, observation.id)
        return self._events.fold_observation(
            winner.event_id,
            time_start=anchor.captured_at,
            time_end=anchor.captured_at,
            place=place.display() if place else "",
            admin_district=place.admin.district if place else "",
            activity=observation.activity,
            summary=observation.caption or observation.activity,
            confidence=observation.confidence,
            aggregation_score=score.total,
            aggregation_breakdown=score.breakdown(),
        )


# 1.0 ``db.py:2601`` 的判据：摘要以「待」开头说明它还是占位（``待总结事件`` /
# ``待确认的家庭记录``），那时不能拿它当正文，只能退回描述性的一句。
PLACEHOLDER_SUMMARY_PREFIX = "待"
# 1.0 ``db.py:2599``：事件既没有 activity 也没有 event_type 时正文里的那个词。
DEFAULT_ACTIVITY_WORD = "活动"


def participant_summary(event: EventRecord, participants: Sequence[EventParticipantRecord]) -> str:
    """1.0 ``refresh_event_summary``（``db.py:2585-2610``）的确定性正文。

    两处与 1.0 的差异，都登记在 ``s5-deviations.md``：

    - 1.0 的地点走 ``_resolved_event_place(event_id, event)``（一张反查地名表加一层
      兜底）。2.0 的事件在归属时就把 ``asset_places`` 那一行写进 ``place`` 了，这里
      直接用 ``event.place``——再解析一次等于把同一条规则实现两遍。
    - 1.0 会在正文前面再拼一段 GPS 前缀（``_gps_prefix_for_event``）。2.0 的事件上
      没有 GPS 列，没有可拼的东西。
    """
    names = list(dict.fromkeys(item.person_name for item in participants if item.person_name))
    activity = event.activity or event.event_type or DEFAULT_ACTIVITY_WORD
    place = event.place
    existing = event.summary.strip()
    has_real_summary = bool(existing) and not existing.startswith(PLACEHOLDER_SUMMARY_PREFIX)
    if names:
        who = "、".join(names)
        # **人名前缀 + 原摘要，而不是二选一。** 模型写的摘要里有时间、地点和活动细节，
        # 直接覆盖会把它们丢掉；只保留原摘要则事件里永远看不到是谁。
        if has_real_summary:
            # **前缀只加一次。** 不能拿 `who` 做 startswith 判断——参与者的读取顺序
            # 每次可能不同（「芳芳、我」vs「我、芳芳」），那样每次刷新都会再叠一层。
            # 人名前缀的特征就是「开头一小段里出现全角冒号」，按它判即可。
            # 摘要前 20 字里已经出现任一位参与者，就说明带过人名了（可能是
            # 「我：我在…」也可能是「我在…参与…」），不再叠加。
            if "：" in existing[:40] or any(name in existing[:20] for name in names):
                return existing
            return f"{who}：{existing}"
        return f"{who}在{place}参与{activity}"
    if has_real_summary:
        return existing
    return f"在{place}发生的{activity}"


class ConfirmedPersonEvents:
    """一个人被确认之后，事件侧要跟着改的两件事。

    1.0 的 ``confirm_face_cluster``（``db.py:5179-5181``）在写完提及之后连着调三个方法::

        self._refresh_event_participants(observation_ids)
        self.resegment_events_for_confirmed_entity(entity["id"])
        memory = self.rebuild_person_memory(entity["id"])

    第三个（人物记忆 / 画像）属 S5c，前两个在这里。**两件事必须在一起**：重分段合并
    两个事件之后，被并进来的那些观察的参与者要在新事件上重新落一遍——分开接会得到
    「事件合并了，参与者却还挂在已经不存在的源事件上」。

    ``confirmed_people`` / ``confirmed_event_people`` 两个来源**都没有默认值**，
    理由与 ``AssignEvents`` 完全相同（Task 80）：少写一个就是 ``TypeError``，而不是
    一个恒空的 ``person_score``。

    **这一层不写人物画像，但写关系候选。** 1.0 的 ``upsert_event_participant`` 末尾还会
    调 ``_maintain_event_cooccurrence_candidates``（``db.py:2508``，往 ``relationships``
    写「共同出现」的待确认边）。S6 时这里写着「2.0 没有任何地方读 ``relationships``，
    写进去就是一张没有读者的表」——**S7a · T205 把读者造出来了**（T201 的实体详情、
    T202 的实体目录计数、T203 的关系图），所以这一半补回来。人物画像仍然不在这里。
    """

    def __init__(
        self,
        *,
        events: EventRepository,
        participants: EventParticipantRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        entities: EntityRepository,
        relationships: RelationshipRepository,
        visual_similarity: EventVisualSimilarity,
        confirmed_people: Callable[[str], frozenset[str]],
        confirmed_event_people: Callable[[str], frozenset[str]],
        after_event_update: Callable[[str], object] | None = None,
    ) -> None:
        self._events = events
        self._participants = participants
        self._observations = observations
        self._assets = assets
        self._entities = entities
        self._relationships = relationships
        self._visual = visual_similarity
        self._confirmed_people = confirmed_people
        self._confirmed_event_people = confirmed_event_people
        # 参与者/摘要变化后重发事件文档；失败不阻断身份确认本身。
        self._after_event_update = after_event_update

    # ---- 参与者投影 ------------------------------------------------------------

    def refresh_participants(self, observation_ids: Sequence[str]) -> tuple[str, ...]:
        """1.0 ``_refresh_event_participants``（``db.py:5549-5578``）。

        按事件分组，逐事件做两件事：给每条观察里**已确认**的人补一条 ``visible_subject``
        参与者；再把事件摘要按当前参与者重写一遍，``revision + 1``。

        1.0 那两件事是两条 UPDATE（先写 ``participants_json``、再 ``refresh_event_summary``），
        所以 ``revision`` 跳两格。2.0 没有 ``participants_json`` 这一列——参与者就是
        ``event_participants`` 表——第一条 UPDATE 的剩余效果只有 revision，于是合成
        一次 ``apply_summary``：**每个受影响事件恰好 +1**。

        1.0 在这里还有一条 ``source_type != 'video_scene'`` 的摘要豁免（``db.py:5576``）：
        ``video_scene`` 事件的摘要只在导入时算一次，之后**永远不刷新**。
        **2.0 不豁免**（S8b T512；``s8-deviations.md`` §2 第 2 条）：迁移 0010 已经把
        ``source_type`` 列建好，视频场景事件从 S8b 起真实存在，照搬那条判据会让
        「确认一个人之后参与者写进了场景事件、摘要却停在 ``视频场景 · 0.0s~60.0s``」
        变成一个没人看得见的状态。这是**修 1.0 的缺陷，不是偏离**。
        """
        grouped = self._events.events_for_observations(list(observation_ids))
        affected: list[str] = []
        for event_id in sorted(grouped):
            event = self._events.get(event_id)
            if event is None:
                continue
            for observation_id in grouped[event_id]:
                for person_id in self._confirmed_people(observation_id):
                    # 提及里出现的人一定建过实体；这一句防的是「实体被删、提及还在」
                    # 那种中间态，1.0 的 upsert 里也有同一句判据。
                    if self._entities.get(person_id) is None:
                        continue
                    self._participants.upsert_participant(
                        event_id,
                        person_id,
                        role=PARTICIPANT_ROLE_VISIBLE_SUBJECT,
                        evidence_ids=(observation_id,),
                        confidence=VISIBLE_SUBJECT_CONFIDENCE,
                    )
            self._suggest_cooccurrence(event_id)
            self._events.apply_summary(
                event_id,
                title=event.title,
                event_type=event.event_type,
                activity=event.activity,
                summary=participant_summary(event, self._participants.list_participants(event_id)),
            )
            affected.append(event_id)
        if self._after_event_update is not None:
            for event_id in affected:
                try:
                    self._after_event_update(event_id)
                except Exception:  # noqa: BLE001 - 文档失败不回溯身份确认
                    continue
        return tuple(affected)

    def _suggest_cooccurrence(self, event_id: str) -> None:
        """1.0 ``_maintain_event_cooccurrence_candidates``（``db.py:2508-2526``）。

        **只提议、不判定。** 1.0 的 docstring 原话：*Suggest co-occurrence only; users
        decide whether it represents a real relationship.* 所以写进去的是 ``pending``
        边，不是 active。

        1.0 在每次 ``upsert_event_participant`` 末尾都调它一次（同一个事件会被重复调，
        靠 ``create`` 的去重合并收敛）；2.0 在**一个事件的所有参与者落完之后调一次**——
        最终状态相同，少写几遍。

        三处照搬：

        - **只取 ``role='visible_subject'`` 且 ``person_status='confirmed'`` 的参与者**
          （``db.py:2510-2513``）。2.0 这一层写进去的参与者本来就都是已确认的人，所以
          这个过滤在**当前**调用链下恒真——但它是 1.0 的判据，而且 ``person_status``
          随实体状态走，留着才是照搬；
        - **无序对按 id ``sorted`` 定向**（``:2516``）。不排的话同一对会写出方向相反
          的两条边，而且不报错；
        - **证据是两人各自参与者行的证据并集**，而 ``confidence`` 数的是**跨全部事件**
          的同时出现次数（``:2517-2524``），不是当前这一个。
        """
        participants = [
            item
            for item in self._participants.list_participants(event_id)
            if item.role == PARTICIPANT_ROLE_VISIBLE_SUBJECT and item.person_status == ENTITY_STATUS_CONFIRMED
        ]
        for index, left in enumerate(participants):
            for right in participants[index + 1 :]:
                subject_id, object_id = sorted((left.person_id, right.person_id))
                self._relationships.create(
                    subject_entity_id=subject_id,
                    predicate=COOCCURRENCE_PREDICATE,
                    object_entity_id=object_id,
                    evidence_ids=list(dict.fromkeys((*left.evidence_ids, *right.evidence_ids))),
                    confidence=min(
                        COOCCURRENCE_CEILING,
                        COOCCURRENCE_BASE_CONFIDENCE
                        + COOCCURRENCE_STEP
                        * self._participants.cooccurrence_event_count(subject_id, object_id),
                    ),
                    status=COOCCURRENCE_STATUS,
                )

    # ---- 确认之后的重分段 --------------------------------------------------------

    def resegment(self, entity_id: str) -> tuple[tuple[str, str], ...]:
        """1.0 ``resegment_events_for_confirmed_entity``（``db.py:5450-5481``）。

        这个人牵涉的事件里，还有哪两个其实是同一场？对每一对（事件，另一个事件）：
        在后者里找到一条**属于这个人**的观察当桥 → 时间在 6 小时窗内 → 八分量总分
        ``>= 0.50``，就把后者并进前者。

        **桥必须是这个人的观察**：另一个人同时出现在两个事件里不能当作合并理由，
        那会让「确认 A」顺手改掉与 A 无关的事件。

        **它只在显式确认时跑**，不在导入链上——导入时人脸先于事件归并，那时的提及已经
        参与过打分（Task 79/80）。这里处理的是「确认之前被拆错的那几刀，确认之后合回来」。
        """
        entity = self._entities.get(entity_id)
        if entity is None:
            return ()
        event_ids = list(dict.fromkeys(self._entities.event_ids(entity_id)))
        if len(event_ids) < 2:
            return ()
        # 只有仍活着的事件能当合并的两端：1.0 用 ``list_events(1000)`` 过滤，同一条判据。
        active = {
            item.id for item in self._events.list_active(entity.scope_id, limit=ACTIVE_EVENT_SCAN_LIMIT)
        }
        merged: list[tuple[str, str]] = []
        for event_id in sorted(event_ids):
            event = self._events.get(event_id)
            if event is None or event_id not in active:
                continue
            for other_id in sorted(event_ids):
                if other_id == event_id:
                    continue
                other = self._events.get(other_id)
                if other is None:
                    continue
                observation_id = next(
                    (
                        item
                        for item in self._events.observation_ids(other_id)
                        if entity_id in self._confirmed_people(item)
                    ),
                    None,
                )
                if observation_id is None:
                    continue
                if not within_candidate_window(other.time_start, event.time_start):
                    continue
                score = self._score(observation_id, event)
                if score is None or score.total < CANDIDATE_THRESHOLD:
                    continue
                target_observations = self._events.observation_ids(event_id)
                source_observations = self._events.observation_ids(other_id)
                # 先搬投影再删事件：``event_participants`` 的外键挡在 ``DELETE FROM events``
                # 前面，顺序反了这句 DELETE 会直接失败（见 ports 里 ``rehome`` 的注释）。
                self._participants.rehome(other_id, event_id)
                self._events.merge(other_id, event_id)
                # 1.0 的 ``_merge_events`` 收尾也是这一句：合并之后两边观察的参与者
                # 要在**新**事件上重新落一遍。
                self.refresh_participants([*target_observations, *source_observations])
                merged.append((event_id, other_id))
                break
        return tuple(merged)

    def _score(self, observation_id: str, event: EventRecord) -> EventCandidateScore | None:
        observation = self._observations.get(observation_id)
        if observation is None:
            return None
        asset = self._assets.get(observation.asset_id)
        if asset is None:
            return None
        observation_ids = self._events.observation_ids(event.id)
        return score_event_candidate(
            anchor=observation_anchor(observation, asset, people=self._confirmed_people(observation.id)),
            event=anchor_set_for_event(
                event,
                observation_ids,
                observations=self._observations,
                assets=self._assets,
                people=self._confirmed_event_people(event.id),
            ),
            visual=self._visual.for_observation(observation, observation_ids),
        )


__all__ = [
    "ACTIVE_EVENT_SCAN_LIMIT",
    "CANDIDATE_WINDOW_HOURS",
    "AssignmentResult",
    "AssignEvents",
    "ConfirmedPersonEvents",
    "EventVisualSimilarity",
    "observation_anchor",
    "participant_summary",
]

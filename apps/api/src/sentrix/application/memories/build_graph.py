"""从 2.0 标准记忆端口构建可重建的结构化记忆图。"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime

from sentrix.domain.identity.ports import EntityRepository, FamilyGraphRepository, RelationshipRepository
from sentrix.domain.media.capture_time import elapsed_seconds, time_sort_key
from sentrix.domain.media.models import Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.media.scenes import KEYFRAME_DERIVED_KIND
from sentrix.domain.memories.causality import (
    CausalFrame,
    CausalParseError,
    extract_causal_pairs,
    signals_prompt,
)
from sentrix.domain.memories.email_links import normalise_label, reservation_context
from sentrix.domain.memories.events import EventRecord
from sentrix.domain.memories.graph import GraphBuildStats, GraphEdge, GraphNode
from sentrix.domain.memories.ports import (
    CausalJudge,
    EventParticipantRepository,
    EventRepository,
    FactRepository,
    MemoryGraphRepository,
)
from sentrix.domain.retrieval.ports import VectorRepository

from .image_graph import project_image_graph
from .keyframe_graph import project_keyframe_graph

log = logging.getLogger("sentrix.memories.build_graph")

GRAPH_READ_LIMIT = 100_000
TEMPORAL_EDGE_MAX_SECONDS = 6 * 60 * 60


def _aware(value: datetime) -> datetime:
    """事件时间可能来自无时区元数据；图内比较统一按 UTC。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _digest(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in (prefix, *parts))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _node_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


class BuildMemoryGraph:
    """把已有记忆投影成图。

    输入只有 2.0 的 domain 端口：资产、观察、事件、参与者、实体、事实、关系和
    可选的权威视觉向量。普通资产始终可构图；已有关键帧则额外构建 hpq 边族。
    不读取关键帧文件、Qdrant payload 或旧版内存库，也不运行旧版记忆生成器。
    """

    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        events: EventRepository,
        participants: EventParticipantRepository,
        entities: EntityRepository,
        facts: FactRepository,
        relationships: RelationshipRepository,
        graph: MemoryGraphRepository,
        causal_judge: CausalJudge | None = None,
        vectors: VectorRepository | None = None,
        family: FamilyGraphRepository | None = None,
        causal_batch_size: int | None = 12,
        build_lock: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._events = events
        self._participants = participants
        self._entities = entities
        self._facts = facts
        self._relationships = relationships
        self._graph = graph
        self._causal_judge = causal_judge
        self._vectors = vectors
        self._family = family
        self._causal_batch_size = causal_batch_size
        self._build_lock = build_lock

    def execute(self, scope_id: str) -> GraphBuildStats:
        # Serialize snapshot reads through publication, so a slower earlier build
        # cannot overwrite a newer graph after a name edit has synced.
        with self._build_lock():
            return self._execute(scope_id)

    def _execute(self, scope_id: str) -> GraphBuildStats:
        scope = str(scope_id or "").strip()
        if not scope:
            raise ValueError("构建记忆图需要明确的 scope_id")

        assets = tuple(self._assets.list_by_scope(scope, limit=GRAPH_READ_LIMIT))
        observations = tuple(self._observations.list_scope(scope, limit=GRAPH_READ_LIMIT))
        events = tuple(self._events.list_by_scope(scope, limit=GRAPH_READ_LIMIT))
        entities = tuple(self._entities.list_entities(scope_id=scope))
        facts = tuple(self._facts.list_facts(scope, limit=GRAPH_READ_LIMIT))
        relationships = tuple(self._relationships.list_relationships(scope))

        nodes: dict[str, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        observation_by_id = {item.id: item for item in observations}
        latest_by_asset: dict[str, Observation] = {}
        for observation in observations:
            current = latest_by_asset.get(observation.asset_id)
            if current is None or (observation.revision, observation.updated_at) > (
                current.revision,
                current.updated_at,
            ):
                latest_by_asset[observation.asset_id] = observation
        observation_fact_texts: dict[str, list[dict[str, object]]] = {}
        for observation in observations:
            observation_fact_texts.setdefault(observation.asset_id, [])
            if latest_by_asset.get(observation.asset_id) is observation:
                observation_fact_texts[observation.asset_id] = [
                    {
                        "subject": fact.subject,
                        "predicate": fact.predicate,
                        "object": fact.object,
                        "confidence": fact.confidence,
                        "observation_id": observation.id,
                    }
                    for fact in observation.facts
                ]
        persisted_facts_by_asset: dict[str, list[dict[str, object]]] = {}
        for fact in facts:
            if str(fact.status) != "active":
                continue
            for evidence_id in fact.evidence_ids:
                evidence = observation_by_id.get(evidence_id)
                if evidence is None:
                    continue
                persisted_facts_by_asset.setdefault(evidence.asset_id, []).append(
                    {
                        "fact_id": fact.id,
                        "subject": fact.subject,
                        "predicate": fact.predicate,
                        "object": fact.object,
                        "confidence": fact.confidence,
                        "observation_id": evidence_id,
                    }
                )
        asset_by_id = {item.id: item for item in assets}
        entity_by_id = {item.id: item for item in entities}
        confirmed_mentions = tuple(self._entities.list_mentions(scope, limit=GRAPH_READ_LIMIT))
        confirmed_by_asset: dict[str, set[str]] = {}
        for mention in confirmed_mentions:
            person = entity_by_id.get(mention.entity_id)
            observed = observation_by_id.get(mention.observation_id)
            if person and person.status == "confirmed" and observed:
                confirmed_by_asset.setdefault(observed.asset_id, set()).add(person.id)
        scene_by_asset: dict[str, str] = {}

        def add_node(node: GraphNode) -> None:
            nodes.setdefault(node.id, replace(node, attributes={"schema_version": 3, **node.attributes}))

        def add_edge(
            source_id: str,
            target_id: str,
            relation: str,
            *,
            weight: float = 1.0,
            attributes: Mapping[str, object] | None = None,
            discriminator: str = "",
        ) -> None:
            if source_id == target_id:
                return
            edge_id = _node_id(
                "edge",
                _digest(scope, source_id, target_id, relation, discriminator),
            )
            edges.setdefault(
                edge_id,
                GraphEdge(
                    id=edge_id,
                    scope_id=scope,
                    source_id=source_id,
                    target_id=target_id,
                    relation=relation,
                    weight=min(1.0, max(0.0, float(weight))),
                    attributes=dict(attributes or {}),
                ),
            )

        # Only confirmed people are identity vertices. Place/time/object semantics
        # remain searchable attributes on media and events.
        graph_person_ids = {
            item.id for item in entities if item.entity_type == "person" and item.status == "confirmed"
        }
        for asset in assets:
            add_node(
                GraphNode(
                    id=_node_id("asset", asset.id),
                    scope_id=scope,
                    kind="asset",
                    label=asset.file_name,
                    attributes={
                        "asset_id": asset.id,
                        "media_type": asset.media_type.value,
                        "captured_at": _iso(asset.captured_at),
                        "derived_kind": asset.derived_kind or "",
                        "parent_asset_id": asset.parent_asset_id or "",
                        "source_timestamp_sec": asset.source_timestamp_sec,
                        "confirmed_entity_ids": sorted(
                            confirmed_by_asset.get(asset.id, set()) & graph_person_ids
                        ),
                        # Observations and fact assertions are payload/provenance, not
                        # standalone graph vertices. Keep the latest observation on its media.
                        "observation": (
                            {
                                "observation_id": latest_by_asset[asset.id].id,
                                "revision": latest_by_asset[asset.id].revision,
                                "captured_at": _iso(latest_by_asset[asset.id].captured_at),
                                "caption": latest_by_asset[asset.id].caption,
                                "text_content": latest_by_asset[asset.id].text_content,
                                "external_id": latest_by_asset[asset.id].external_id,
                                "source_type": latest_by_asset[asset.id].source_type,
                                "source_metadata": latest_by_asset[asset.id].source_metadata,
                                "ocr_text": latest_by_asset[asset.id].ocr_text,
                                "activity": latest_by_asset[asset.id].activity,
                                "place": latest_by_asset[asset.id].place,
                                "event_type": latest_by_asset[asset.id].event_type,
                                "objects": list(latest_by_asset[asset.id].objects),
                                "clothing": list(latest_by_asset[asset.id].clothing),
                                "people": list(latest_by_asset[asset.id].people),
                                "facts": observation_fact_texts.get(asset.id, []),
                                "fact_texts": [
                                    f"{fact.subject} {fact.predicate} {fact.object}".strip()
                                    for fact in latest_by_asset[asset.id].facts
                                ],
                            }
                            if asset.id in latest_by_asset
                            else {}
                        ),
                        "confirmed_facts": persisted_facts_by_asset.get(asset.id, []),
                    },
                )
            )
            # 视频关键帧是普通 Asset，但它与原视频的血缘不是观察语义，不能只
            # 放在 attributes 里。把这条关系投影进图后，检索从关键帧能回到原视频，
            # 从原视频也能沿图找到对应关键帧；旧版 graph_memory 的 parent/derived
            # 关系因此在 2.0 标准 Asset 端口上有了等价落点。
            if asset.parent_asset_id and asset.parent_asset_id in asset_by_id:
                add_edge(
                    _node_id("asset", asset.id),
                    _node_id("asset", asset.parent_asset_id),
                    "asset_derived_from_asset",
                    attributes={
                        "derived_kind": asset.derived_kind or "",
                        "source_timestamp_sec": asset.source_timestamp_sec,
                    },
                )

        # 图中的实体节点只表示稳定、已确认的人物身份。地点/时间/普通物体仍
        # 保留在 asset/event/observation 属性中，不再被提升成高基数的伪实体节点。
        for entity in entities:
            if entity.id not in graph_person_ids:
                continue
            properties = {
                p.property_key: p.value
                for p in self._entities.list_properties(entity.id)
                if p.status == "active"
            }
            membership = self._family.get_effective_membership(scope, entity.id) if self._family else None
            portrait = self._family.get_active_portrait(scope, entity.id) if self._family else None
            add_node(
                GraphNode(
                    id=_node_id("entity", entity.id),
                    scope_id=scope,
                    kind="entity",
                    label=entity.canonical_name,
                    attributes={
                        "entity_id": entity.id,
                        "canonical_name": entity.canonical_name,
                        "summary": entity.summary,
                        "entity_type": entity.entity_type,
                        "status": entity.status,
                        "family_role": entity.family_role or "",
                        "properties": properties,
                        "membership": membership.membership if membership else "",
                        "portrait": portrait.portrait_text if portrait else "",
                    },
                )
            )

        if self._family is not None:
            for family_relation in self._family.list_effective_relationships(scope):
                if not family_relation.locked:
                    continue
                if (
                    family_relation.subject_entity_id in graph_person_ids
                    and family_relation.object_entity_id in graph_person_ids
                ):
                    add_edge(
                        _node_id("entity", family_relation.subject_entity_id),
                        _node_id("entity", family_relation.object_entity_id),
                        f"entity_relationship:{family_relation.predicate}",
                        weight=family_relation.confidence,
                        discriminator="family",
                        attributes={
                            "relationship_id": family_relation.id,
                            "source": family_relation.source,
                            "evidence_tier": "confirmed" if family_relation.locked else "inferred",
                        },
                    )

        for event in events:
            event_node = _node_id("event", event.id)
            event_observation_ids = tuple(self._events.observation_ids(event.id))
            event_participants = tuple(self._participants.list_participants(event.id))
            event_entities = tuple(self._participants.list_event_entities(event.id))
            add_node(
                GraphNode(
                    id=event_node,
                    scope_id=scope,
                    kind="event",
                    label=event.title or event.summary,
                    attributes={
                        "event_id": event.id,
                        "time_start": _iso(event.time_start),
                        "time_end": _iso(event.time_end),
                        "place": event.place,
                        "activity": event.activity,
                        "event_type": event.event_type,
                        "summary": event.summary,
                        "source_asset_id": event.source_asset_id or "",
                        "source_type": event.source_type,
                        "source_scene_index": event.source_scene_index,
                        "observation_ids": list(event_observation_ids),
                        "asset_ids": sorted(
                            {
                                observation_by_id[item].asset_id
                                for item in event_observation_ids
                                if item in observation_by_id
                                and observation_by_id[item].asset_id in asset_by_id
                            }
                        ),
                        "participant_entity_ids": [item.person_id for item in event_participants],
                        "event_entity_ids": [item.entity_id for item in event_entities],
                    },
                )
            )
            for observation_id in event_observation_ids:
                matched_observation = observation_by_id.get(observation_id)
                if matched_observation is None:
                    continue
                if matched_observation.asset_id in asset_by_id:
                    add_edge(
                        event_node, _node_id("asset", matched_observation.asset_id), "event_contains_asset"
                    )
                    if event.source_type == "video_scene":
                        scene_by_asset[matched_observation.asset_id] = event.id
            for participant in event_participants:
                if participant.person_id in graph_person_ids:
                    add_edge(
                        event_node,
                        _node_id("entity", participant.person_id),
                        "event_has_entity",
                        weight=participant.confidence or 1.0,
                        attributes={"role": participant.role},
                    )
            for event_entity in event_entities:
                if event_entity.entity_id in graph_person_ids:
                    add_edge(
                        event_node,
                        _node_id("entity", event_entity.entity_id),
                        "event_has_entity",
                        weight=event_entity.confidence or 1.0,
                        attributes={
                            "relation": event_entity.relation,
                            "evidence_ids": list(event_entity.evidence_ids),
                        },
                    )
            if event.source_asset_id in asset_by_id:
                add_edge(
                    event_node,
                    _node_id("asset", event.source_asset_id),
                    "event_source_asset",
                    attributes={
                        "source_type": event.source_type,
                        "source_scene_index": event.source_scene_index,
                        "source_start_sec": event.source_start_sec,
                        "source_end_sec": event.source_end_sec,
                    },
                )

        for mention in confirmed_mentions:
            mentioned_person = entity_by_id.get(mention.entity_id)
            if (
                mentioned_person
                and mentioned_person.id in graph_person_ids
                and mentioned_person.status == "confirmed"
                and mention.observation_id in observation_by_id
            ):
                mentioned_observation = observation_by_id[mention.observation_id]
                if mentioned_observation.asset_id in asset_by_id:
                    add_edge(
                        _node_id("asset", mentioned_observation.asset_id),
                        _node_id("entity", mentioned_person.id),
                        "asset_mentions_confirmed_entity",
                        attributes={
                            "entity_id": mentioned_person.id,
                            "observation_id": mention.observation_id,
                            "evidence_tier": "confirmed",
                        },
                    )

        overridden_pairs = set(self._family.user_relationship_pairs(scope)) if self._family else set()
        for relationship in relationships:
            if (
                tuple(sorted((relationship.subject_entity_id, relationship.object_entity_id)))
                in overridden_pairs
            ):
                continue
            if relationship.status in {"retracted", "superseded"}:
                continue
            if (
                relationship.subject_entity_id not in graph_person_ids
                or relationship.object_entity_id not in graph_person_ids
            ):
                continue
            add_edge(
                _node_id("entity", relationship.subject_entity_id),
                _node_id("entity", relationship.object_entity_id),
                f"entity_relationship:{relationship.predicate}",
                weight=relationship.confidence or 1.0,
                attributes={"relationship_id": relationship.id, "status": relationship.status},
            )

        media_events = tuple(e for e in events if e.source_type not in {"text", "email"})
        self._add_temporal_edges(media_events, scope, add_edge)
        # Mail records are communication events; their publication time must not
        # stand in for the date of the booked activity, or imply actual attendance.
        activities_by_date: dict[str, list] = {}
        for activity in events:
            if activity.source_type not in {"text", "email"} and activity.time_start:
                activities_by_date.setdefault(activity.time_start.date().isoformat(), []).append(activity)
        for mail in events:
            if mail.source_type not in {"text", "email"}:
                continue
            observation = latest_by_asset.get(mail.source_asset_id)
            if observation is None:
                continue
            text = observation.caption + "\n" + observation.text_content
            dates, place = reservation_context(text)
            if not dates:
                continue
            related = [activity for date in dates for activity in activities_by_date.get(date, [])]
            for event in sorted(related, key=lambda e: e.id)[:24]:
                label_match = bool(
                    place
                    and normalise_label(place)
                    in normalise_label(" ".join((event.place, event.title, event.summary)))
                )
                add_edge(
                    _node_id("event", mail.id),
                    _node_id("event", event.id),
                    "text_refers_to_event" if label_match else "text_time_candidate",
                    weight=0.85 if label_match else 0.35,
                    attributes={
                        "external_id": observation.external_id,
                        "observation_id": observation.id,
                        "evidence_ids": [observation.id],
                        "observation_revision": observation.revision,
                        "referenced_dates": list(dates),
                        "place_mention": place,
                        "source_excerpt": text[:4000],
                        "evidence_tier": "inferred" if label_match else "candidate",
                        "reason": "explicit_date_and_place" if label_match else "date_only_not_confirmed",
                        "not_causality": True,
                    },
                )

        keyframe_stats = project_keyframe_graph(
            assets=assets,
            latest_observations=latest_by_asset,
            scene_by_asset=scene_by_asset,
            vectors=self._vectors,
            add_edge=lambda source, target, relation, weight, attributes, discriminator: add_edge(
                source,
                target,
                relation,
                weight=weight,
                attributes=attributes,
                discriminator=discriminator,
            ),
        )

        project_image_graph(
            assets=assets,
            latest_observations=latest_by_asset,
            vectors=self._vectors,
            add_edge=lambda source, target, relation, weight, attributes, discriminator: add_edge(
                source,
                target,
                relation,
                weight=weight,
                attributes=attributes,
                discriminator=discriminator,
            ),
        )

        causal_started = time.perf_counter()
        frames: list[CausalFrame] = []
        for asset in assets:
            if asset.derived_kind != KEYFRAME_DERIVED_KIND or not asset.parent_asset_id:
                continue
            frame_observation = latest_by_asset.get(asset.id)
            if frame_observation is None or asset.source_timestamp_sec is None:
                continue
            frames.append(
                CausalFrame(
                    asset_id=asset.id,
                    parent_asset_id=asset.parent_asset_id,
                    timestamp_sec=asset.source_timestamp_sec,
                    frame_index=0,
                    objects=tuple(frame_observation.objects),
                    relations=tuple(
                        f"{fact.subject} {fact.predicate} {fact.object}".strip()
                        for fact in frame_observation.facts
                    ),
                )
            )
        pairs = extract_causal_pairs(tuple(frames))
        causal_status = "no_candidates" if not pairs else "not_configured"
        causal_edges = 0
        if pairs and self._causal_judge is not None:
            causal_status = "ok"
            batch_size = self._causal_batch_size or len(pairs) or 1
            for offset in range(0, len(pairs), batch_size):
                batch = pairs[offset : offset + batch_size]
                try:
                    verdicts = self._causal_judge.judge(signals_prompt(batch), expected_count=len(batch))
                except CausalParseError as error:
                    # hpq 对截断/不完整批次按更小的批次重试；只对解析错误做一次
                    # 单对重试。端点故障不重试，避免 Mac 16 GB 上把超时放大 12 倍。
                    log.warning(
                        "因果批次响应不完整 scope=%s batch=%d: %s",
                        scope,
                        offset // batch_size,
                        error,
                    )
                    recovered = []
                    for local_index, pair in enumerate(batch):
                        try:
                            one = self._causal_judge.judge(signals_prompt((pair,)), expected_count=1)
                            recovered.append(replace(one[0], pair_id=local_index))
                        except Exception as retry_error:
                            causal_status = "degraded"
                            log.warning(
                                "因果单对重试失败 scope=%s pair=%s→%s: %s",
                                scope,
                                pair.before.asset_id,
                                pair.after.asset_id,
                                retry_error,
                            )
                    verdicts = recovered
                except Exception as error:  # 图是派生投影；故障必须可观测，不能冒充无因果边
                    causal_status = "degraded"
                    log.warning("因果边判定失败 scope=%s batch=%d: %s", scope, offset // batch_size, error)
                    continue
                for verdict in verdicts:
                    if not verdict.has_causal or verdict.edge_type not in {"LEADS_TO", "ENABLES"}:
                        continue
                    pair = batch[verdict.pair_id]
                    add_edge(
                        _node_id("asset", pair.before.asset_id),
                        _node_id("asset", pair.after.asset_id),
                        f"asset_causes_asset:{verdict.edge_type}",
                        weight=verdict.confidence,
                        attributes={
                            "sub_type": verdict.edge_type,
                            "confidence": verdict.confidence,
                            "evidence_tier": "inferred",
                            "reason": verdict.reason,
                            "method": "rules_vlm",
                        },
                    )
                    causal_edges += 1
        causal_seconds = time.perf_counter() - causal_started

        revision_parts: list[str] = [scope]
        revision_parts.extend(f"node:{node.id}:{sorted(node.attributes.items())}" for node in nodes.values())
        revision_parts.extend(f"asset:{item.id}:{_iso(item.updated_at)}" for item in assets)
        revision_parts.extend(
            f"observation:{item.id}:{item.revision}:{item.updated_at.isoformat()}" for item in observations
        )
        revision_parts.extend(f"event:{item.id}:{item.revision}:{_iso(item.created_at)}" for item in events)
        revision_parts.extend(f"entity:{item.id}:{_iso(item.updated_at)}" for item in entities)
        revision_parts.extend(f"fact:{item.id}:{item.revision}:{item.status}" for item in facts)
        revision_parts.extend(
            f"relationship:{item.id}:{item.revision}:{_iso(item.updated_at)}" for item in relationships
        )
        revision_parts.extend(
            f"edge:{edge.id}:{edge.weight}:{sorted(edge.attributes.items())}" for edge in edges.values()
        )
        build_revision = hashlib.sha256("\n".join(sorted(revision_parts)).encode("utf-8")).hexdigest()
        result = self._graph.replace_scope(
            scope,
            nodes=tuple(nodes.values()),
            edges=tuple(edges.values()),
            build_revision=build_revision,
        )
        log.info(
            "记忆图已重建 scope=%s nodes=%d edges=%d revision=%s",
            scope,
            result.node_count,
            result.edge_count,
            build_revision[:12],
        )
        return replace(
            result,
            causal_candidate_pairs=len(pairs),
            causal_edges=causal_edges,
            causal_status=causal_status,
            causal_seconds=round(causal_seconds, 3),
            keyframe_count=keyframe_stats.frame_count,
            keyframe_temporal_edges=keyframe_stats.temporal_edges,
            keyframe_object_edges=keyframe_stats.object_edges,
            keyframe_relation_edges=keyframe_stats.relation_edges,
            keyframe_visual_edges=keyframe_stats.visual_edges,
            keyframe_visual_status=keyframe_stats.visual_status,
        )

    @staticmethod
    def _add_temporal_edges(
        events: Iterable[EventRecord],
        scope_id: str,
        add_edge: Callable[..., None],
    ) -> None:
        ordered = sorted(
            (event for event in events if event.time_start is not None),
            key=lambda event: (
                time_sort_key(event.time_start),
                event.id,
            ),
        )
        for left, right in zip(ordered, ordered[1:], strict=False):
            assert left.time_start is not None and right.time_start is not None
            if (left.time_start.utcoffset() is None) != (right.time_start.utcoffset() is None):
                continue
            gap = elapsed_seconds(right.time_start, left.time_start)
            if 0 <= gap <= TEMPORAL_EDGE_MAX_SECONDS:
                add_edge(
                    _node_id("event", left.id),
                    _node_id("event", right.id),
                    "event_temporal_next",
                    weight=max(0.1, 1.0 - gap / TEMPORAL_EDGE_MAX_SECONDS),
                    attributes={"gap_seconds": gap, "scope_id": scope_id},
                )

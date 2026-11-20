"""修正后的派生同步；失败保留持久任务，重试读取当前值。"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sentrix.application.memories.summarize_event import parse_projection_object
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.application.retrieval.text_memories import IndexTextMemories
from sentrix.domain.assistant.models import ChatMessage, ChatRole
from sentrix.domain.assistant.ports import ChatModel
from sentrix.domain.identity.ports import EntityRepository
from sentrix.domain.media.ports import ObservationRepository
from sentrix.domain.memories.ports import DependentTextMemory, EventRepository, MemoryCorrectionRepository
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import LexicalIndex, TextEmbedder, VectorRecord, VectorRepository

log = logging.getLogger(__name__)
SYNC_LOCK = threading.RLock()


class RebuildDependentText:
    """Rebuild automatic prose from live sources, retaining identity/evidence bindings."""

    def __init__(
        self,
        corrections: MemoryCorrectionRepository,
        observations: ObservationRepository,
        events: EventRepository,
        entities: EntityRepository,
        model: ChatModel,
    ) -> None:
        self.corrections = corrections
        self.observations = observations
        self.events = events
        self.entities = entities
        self.model = model

    def execute(self, scope: str) -> None:
        errors = []
        targets = self.corrections.dependent_text(scope)
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="memory-dependent") as pool:
            for start in range(0, len(targets), 4):
                batch = targets[start : start + 4]
                futures = [pool.submit(self._rebuild, scope, target) for target in batch]
                for target, future in zip(batch, futures, strict=True):
                    try:
                        future.result()
                    except Exception as error:  # noqa: BLE001 - independent targets retain retry state
                        errors.append(f"{target.kind}:{target.id}: {type(error).__name__}: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _rebuild(self, scope: str, target: DependentTextMemory) -> None:
        observations = self.observations.get_many(target.observation_ids)
        observations = {key: value for key, value in observations.items() if value.scope_id == scope}
        fields = dict.fromkeys(target.fields, "")
        if observations:
            event_ids = self.events.events_for_observations(tuple(observations))
            events = [event for key in event_ids if (event := self.events.get(key)) is not None]
            if any(not event.summary and event.source_type not in {"text", "email"} for event in events):
                raise RuntimeError("关联事件摘要尚未重建")
            entity = self.entities.get(target.entity_id)
            identities = []
            for identity in self.corrections.identity_updates(scope):
                refs = identity.get("observation_ids", [])
                if not isinstance(refs, list):
                    continue
                bound = [ref for ref in refs if ref in observations]
                if bound:
                    identities.append({**identity, "observation_ids": bound})
            messages: tuple[ChatMessage, ...] = (
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content=(
                        "根据当前证据重新生成关联记忆，只返回 JSON 对象，各字段为字符串。"
                        "输出必须与 response_contract 的键完全相同，将空值填写为正文，"
                        "不得添加其他字段，不要复述输入证据或套 fields 容器。"
                        "当人物有用户确认的 current_name，portrait_text 或 summary_zh 必须用该名字作为主语，"
                        "直接称呼其名字，不用‘一名小男孩’等泛称代替。"
                        "只使用给定观察和事件，不保留已删除证据或旧结论，不推断性格、亲属或偏好。"
                        "姓名以 identity_updates 中的实体 ID 与 observation_ids 为准，"
                        "已命名人物使用当前名字；pending 身份只能表述为用户标记的名字。"
                        "多人场景不得把其他未命名人物的动作或泛称全部归给该人。"
                        "活动、地点和外观只写当前直接证据，不足的字段返回空串。"
                    ),
                ),
                ChatMessage(
                    role=ChatRole.USER,
                    content=json.dumps(
                        {
                            "kind": target.kind,
                            "fields": list(fields),
                            "response_contract": dict.fromkeys(fields, ""),
                            "entity_id": target.entity_id,
                            "current_name": entity.canonical_name if entity else "",
                            "name_state": entity.name_state if entity else "",
                            "identity_status": entity.status if entity else "",
                            "identity_updates": identities,
                            "observations": [
                                {
                                    "id": item.id,
                                    "caption": item.caption,
                                    "text_content": item.text_content,
                                    "activity": item.activity,
                                    "place": item.place,
                                    "clothing": item.clothing,
                                    "people": item.people,
                                }
                                for item in observations.values()
                            ],
                            "events": [{"id": event.id, "summary": event.summary} for event in events],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
            for attempt in range(2):
                reply = self.model.complete(messages, ())
                parsed = parse_projection_object(reply.content)
                valid = isinstance(parsed, dict) and set(parsed) == set(fields)
                valid = valid and all(isinstance(value, str) for value in (parsed or {}).values())
                if (
                    valid
                    and entity
                    and entity.entity_type == "person"
                    and (entity.status == "confirmed" or entity.name_state == "confirmed")
                ):
                    for key in ("portrait_text", "summary_zh"):
                        if key in (parsed or {}) and entity.canonical_name not in str((parsed or {})[key]):
                            valid = False
                if valid and parsed is not None:
                    fields = {key: str(value) for key, value in parsed.items()}
                    break
                if attempt == 1:
                    raise ValueError("关联记忆重建未满足字段或人物姓名约束，请重试")
                messages = (
                    *messages,
                    ChatMessage(role=ChatRole.ASSISTANT, content=reply.content),
                    ChatMessage(
                        role=ChatRole.USER,
                        content="请按 response_contract 重新生成，只返回这些字符串字段；"
                        "人物画像的正文必须直接使用 current_name 称呼该人。",
                    ),
                )
        self.corrections.write_dependent_text(target, fields)


class ReindexCorrectedMemories:
    def __init__(
        self,
        observations: ObservationRepository,
        events: EventRepository,
        lexical: LexicalIndex,
        embedder: TextEmbedder,
        vectors: VectorRepository,
        corrections: MemoryCorrectionRepository,
        text_index: IndexTextMemories | None = None,
    ) -> None:
        self.observations = observations
        self.events = events
        self.lexical = lexical
        self.embedder = embedder
        self.vectors = vectors
        self.corrections = corrections
        self.text_index = text_index

    def reindex(self, scope: str) -> None:
        observations = [
            o
            for oid in self.corrections.affected_observations(scope)
            if (o := self.observations.get(oid)) is not None
        ]
        index = IndexObservationText(embedder=self.embedder, vectors=self.vectors)
        events = self.events.events_for_observations([o.id for o in observations])
        for observation in observations:
            self.lexical.refresh_from_observation(observation)
            event_ids = [event_id for event_id, ids in events.items() if observation.id in ids]
            selected_index = self.text_index if observation.text_content and self.text_index else index
            outcome = selected_index.index(observation, event_ids[0] if event_ids else None, force=True)
            if not outcome.indexed and outcome.reason != "empty_text":
                raise RuntimeError(outcome.reason)
        affected_event_ids = set(self.corrections.affected_events(scope))
        affected_event_ids.update(events)
        for event_id in sorted(affected_event_ids):
            event = self.events.get(event_id)
            if event is None or event.status != "active":
                continue
            text = " ".join((event.title, event.summary, event.place, event.activity)).strip()
            if not text:
                continue
            vector = self.embedder.embed_query(text)
            if len(vector) != self.embedder.dimension:
                raise RuntimeError("event embedding unavailable or incompatible")
            ids = self.events.observation_ids(event.id)
            self.vectors.upsert(
                VectorRecord(
                    scope_id=scope,
                    space=scheme.EPISODIC_SPACE,
                    source_type="event",
                    source_id=event.id,
                    model_name=self.embedder.model_id,
                    vector=tuple(vector),
                    metadata={"observation_id": ids[0] if ids else ""},
                )
            )


class SyncCorrections:
    def __init__(
        self,
        repository: MemoryCorrectionRepository,
        reindex: Callable[[str], None],
        graph: Callable[[str], None],
        summaries: Callable[[str], None] | None = None,
        dependent: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._reindex = reindex
        self._graph = graph
        self._summaries = summaries
        self._dependent = dependent

    def execute(self, scope_id: str) -> str:
        with SYNC_LOCK:
            errors = []
            operations: list[Callable[[str], None]] = [
                self._repository.refresh_people,
                self._repository.refresh_dependencies,
            ]
            if self._summaries is not None:
                operations.append(self._summaries)
            if self._dependent is not None:
                operations.append(self._dependent)
            operations.extend((self._graph, self._reindex))
            for operation in operations:
                try:
                    operation(scope_id)
                except Exception as exc:  # noqa: BLE001 - 各派生步骤独立，失败保留任务
                    message = f"{type(exc).__name__}: {exc}"
                    errors.append(message)
                    log.warning("记忆修正同步待重试 scope=%s: %s", scope_id, message)
            error = "; ".join(errors)
            self._repository.finish(scope_id, error)
            return error

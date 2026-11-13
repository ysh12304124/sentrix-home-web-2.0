"""修正后的派生同步；失败保留持久任务，重试读取当前值。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.domain.media.ports import ObservationRepository
from sentrix.domain.memories.ports import EventRepository, MemoryCorrectionRepository
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import LexicalIndex, TextEmbedder, VectorRecord, VectorRepository

log = logging.getLogger(__name__)
SYNC_LOCK = threading.RLock()


class ReindexCorrectedMemories:
    def __init__(
        self,
        observations: ObservationRepository,
        events: EventRepository,
        lexical: LexicalIndex,
        embedder: TextEmbedder,
        vectors: VectorRepository,
        corrections: MemoryCorrectionRepository,
    ) -> None:
        self.observations = observations
        self.events = events
        self.lexical = lexical
        self.embedder = embedder
        self.vectors = vectors
        self.corrections = corrections

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
            event_ids = events.get(observation.id, ())
            outcome = index.index(observation, event_ids[0] if event_ids else None, force=True)
            if not outcome.indexed and outcome.reason != "empty_text":
                raise RuntimeError(outcome.reason)
        affected_event_ids = set(self.corrections.affected_events(scope))
        affected_event_ids.update(eid for ids in events.values() for eid in ids)
        for event_id in sorted(affected_event_ids):
            event = self.events.get(event_id)
            if event is None:
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
    ) -> None:
        self._repository = repository
        self._reindex = reindex
        self._graph = graph

    def execute(self, scope_id: str) -> str:
        with SYNC_LOCK:
            errors = []
            for operation in (self._repository.refresh_people, self._graph, self._reindex):
                try:
                    operation(scope_id)
                except Exception as exc:  # noqa: BLE001 - 各派生步骤独立，失败保留任务
                    message = f"{type(exc).__name__}: {exc}"
                    errors.append(message)
                    log.warning("记忆修正同步待重试 scope=%s: %s", scope_id, message)
            error = "; ".join(errors)
            self._repository.finish(scope_id, error)
            return error

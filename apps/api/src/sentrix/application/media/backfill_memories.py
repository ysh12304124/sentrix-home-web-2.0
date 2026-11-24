"""H1 确定性回填、H2 按范围重建任务与执行器。

H1 只重建词法、出现 FTS 和事件文档，**0 模型调用**；H2 只处理用户选中的阶段与
范围，任务幂等，失败最多本地重试 3 次。H2 的 observation 阶段才会调用视觉模型。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.application.memories.publish_event_document import PublishEventDocument
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.domain.identity.ports import FaceRepository, PersonOccurrenceIndex
from sentrix.domain.media.models import (
    AssetStatus,
    MediaType,
    MemoryBuildTask,
    Observation,
)
from sentrix.domain.media.ports import (
    AssetRepository,
    MediaStorage,
    MemoryBuildRepository,
    MemoryBuildTaskRepository,
    MemoryEvidenceRepository,
    ObservationRepository,
    VlmObserver,
)
from sentrix.domain.memories.ports import EventDocumentRepository, EventRepository
from sentrix.domain.retrieval.ports import LexicalIndex, TextEmbedder, VectorRepository

log = logging.getLogger(__name__)

H2_STAGES = ("observation", "event_projection", "face_binding", "lexical_vector_index")


@dataclass(frozen=True, slots=True)
class BackfillResult:
    scope_id: str
    dry_run: bool
    counts: Mapping[str, int] = field(default_factory=dict)
    stages: tuple[str, ...] = ()


class BackfillMemories:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        events: EventRepository,
        lexical: LexicalIndex,
        occurrences: PersonOccurrenceIndex,
        publisher: PublishEventDocument,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._events = events
        self._lexical = lexical
        self._occurrences = occurrences
        self._publisher = publisher

    def execute(self, scope_id: str, *, dry_run: bool = True) -> BackfillResult:
        native = [
            asset
            for asset in self._assets.list_by_scope(scope_id, limit=100_000)
            if asset.media_type is MediaType.IMAGE and not asset.derived_kind
        ]
        observations = self._observations.list_scope(scope_id, limit=100_000)
        events = self._events.list_by_scope(scope_id, limit=100_000)
        counts = {
            "native_images": len(native),
            "observations": len(observations),
            "events": len(events),
            "model_calls": 0,
        }
        if dry_run:
            return BackfillResult(
                scope_id=scope_id,
                dry_run=True,
                counts=counts,
                stages=("lexical", "occurrence_index", "event_documents"),
            )
        lexical_terms = self._lexical.rebuild_scope(scope_id)
        occurrence_terms = self._occurrences.rebuild(scope_id)
        published = 0
        for event in events:
            if self._publisher.execute(event.id) is not None:
                published += 1
        counts.update(
            {
                "lexical_terms": int(lexical_terms),
                "occurrence_terms": int(occurrence_terms),
                "event_documents": published,
            }
        )
        return BackfillResult(
            scope_id=scope_id,
            dry_run=False,
            counts=counts,
            stages=("lexical", "occurrence_index", "event_documents"),
        )


@dataclass(frozen=True, slots=True)
class RebuildSubmission:
    scope_id: str
    dry_run: bool
    tasks: tuple[MemoryBuildTask, ...]
    missing: Mapping[str, int]
    estimated_model_calls: int


@dataclass(frozen=True, slots=True)
class RebuildRun:
    claimed: int
    done: int
    failed: int


class QueueMemoryRebuild:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        events: EventRepository,
        documents: EventDocumentRepository,
        tasks: MemoryBuildTaskRepository,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._events = events
        self._documents = documents
        self._tasks = tasks

    def submit(
        self,
        scope_id: str,
        *,
        asset_ids: Sequence[str] = (),
        stages: Sequence[str] = (),
        dry_run: bool = True,
    ) -> RebuildSubmission:
        selected_stages = tuple(stages) or ("observation", "event_projection")
        unknown = [stage for stage in selected_stages if stage not in H2_STAGES]
        if unknown:
            raise ValueError(f"unsupported rebuild stages: {unknown}")
        assets = [
            asset
            for asset in (
                [self._assets.get(asset_id) for asset_id in asset_ids]
                if asset_ids
                else self._assets.list_by_scope(scope_id, limit=100_000)
            )
            if asset is not None
            and asset.scope_id == scope_id
            and asset.media_type is MediaType.IMAGE
            and asset.status is AssetStatus.PROCESSED
            and not asset.derived_kind
        ]
        missing: Counter[str] = Counter()
        enqueued: list[MemoryBuildTask] = []
        calls = 0
        for asset in assets:
            observation = self._latest(asset.id)
            event_ids = (
                self._events.events_for_observations([observation.id]).get(observation.id, ())
                if observation is not None
                else ()
            )
            for stage in selected_stages:
                if stage == "observation" and observation is not None and observation.active_build_id:
                    continue
                if (
                    stage == "event_projection"
                    and event_ids
                    and all(self._documents.active_for_event(event_id) is not None for event_id in event_ids)
                ):
                    continue
                missing[stage] += 1
                calls += 2 if stage == "observation" else (1 if stage == "lexical_vector_index" else 0)
                if dry_run:
                    continue
                task = self._tasks.enqueue(
                    MemoryBuildTask(
                        id="",
                        scope_id=scope_id,
                        asset_id=asset.id,
                        stage=stage,
                        build_key=f"{scope_id}:{asset.id}:h2",
                        next_attempt_at=datetime.now(UTC),
                    )
                )
                enqueued.append(task)
        return RebuildSubmission(
            scope_id=scope_id,
            dry_run=dry_run,
            tasks=tuple(enqueued),
            missing=dict(missing),
            estimated_model_calls=calls,
        )

    def _latest(self, asset_id: str) -> Observation | None:
        return max(self._observations.list_by_asset(asset_id), key=lambda item: item.updated_at, default=None)


class RunMemoryRebuildTasks:
    def __init__(
        self,
        *,
        tasks: MemoryBuildTaskRepository,
        assets: AssetRepository,
        observations: ObservationRepository,
        events: EventRepository,
        observer: VlmObserver | None,
        storage: MediaStorage | None,
        builds: MemoryBuildRepository,
        evidence: MemoryEvidenceRepository,
        lexical: LexicalIndex,
        embedder: TextEmbedder,
        vectors: VectorRepository,
        publisher: PublishEventDocument,
        occurrences: FaceRepository,
    ) -> None:
        self._tasks = tasks
        self._assets = assets
        self._observations = observations
        self._events = events
        self._observer = observer
        self._storage = storage
        self._builds = builds
        self._evidence = evidence
        self._lexical = lexical
        self._embedder = embedder
        self._vectors = vectors
        self._publisher = publisher
        self._occurrences = occurrences

    def execute(self, *, limit: int = 5) -> RebuildRun:
        claimed = self._tasks.claim_due(limit=limit)
        done = failed = 0
        for task in claimed:
            try:
                self._execute_one(task)
                self._tasks.complete(task.id, status="done")
                done += 1
            except Exception as error:  # noqa: BLE001 - 任务失败保留可重试状态
                log.warning("H2 任务失败 asset=%s stage=%s: %s", task.asset_id, task.stage, error)
                self._tasks.complete(task.id, status="failed", error=f"{type(error).__name__}: {error}")
                failed += 1
        return RebuildRun(claimed=len(claimed), done=done, failed=failed)

    def _execute_one(self, task: MemoryBuildTask) -> None:
        asset = self._assets.get(task.asset_id)
        if asset is None:
            raise ValueError("asset missing")
        observation = max(
            self._observations.list_by_asset(asset.id),
            key=lambda item: item.updated_at,
            default=None,
        )
        if task.stage == "observation":
            if observation is None:
                raise ValueError("observation missing")
            if self._observer is None:
                raise ValueError("vision observer not configured")
            observer = ObserveAsset(
                observer=self._observer,
                observations=self._observations,
                storage=self._storage,
                builds=self._builds,
                evidence=self._evidence,
            )
            result = observer.observe(asset, observation)
            if result is None:
                raise ValueError("observation failed")
            return
        if task.stage == "event_projection":
            if observation is not None:
                for event_id in self._events.events_for_observations([observation.id]).get(
                    observation.id, ()
                ):
                    self._publisher.execute(event_id)
            return
        if task.stage in {"lexical_vector_index", "face_binding"}:
            if observation is not None:
                self._lexical.refresh_from_observation(observation)
                self._occurrences.refresh_occurrences_for_observation(observation.id)
                index = IndexObservationText(embedder=self._embedder, vectors=self._vectors)
                outcome = index.index(observation, None, force=True)
                if not outcome.indexed and outcome.reason != "empty_text":
                    raise RuntimeError(outcome.reason)
            return
        raise ValueError(f"unsupported stage: {task.stage}")


def build_task_progress(tasks: Sequence[MemoryBuildTask]) -> tuple[dict[str, int], list[dict[str, object]]]:
    counts = Counter(task.status for task in tasks)
    return dict(counts), [
        {
            "id": task.id,
            "asset_id": task.asset_id,
            "stage": task.stage,
            "status": task.status,
            "attempts": task.attempts,
            "last_error": task.last_error,
        }
        for task in tasks
    ]

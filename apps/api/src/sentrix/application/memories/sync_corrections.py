"""修正后的派生同步；失败保留持久任务，重试读取当前值。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.domain.identity.ports import FaceRepository
from sentrix.domain.media.ports import ObservationRepository
from sentrix.domain.memories.ports import EventRepository, MemoryCorrectionRepository
from sentrix.domain.retrieval.ports import LexicalIndex, TextEmbedder, VectorRepository

log = logging.getLogger(__name__)
SYNC_LOCK = threading.RLock()
# 有新任务入队时叫醒后台执行器，不必等下一次轮询。
_WAKE = threading.Event()


def wake_correction_sync() -> None:
    _WAKE.set()


class ReindexCorrectedMemories:
    def __init__(
        self,
        observations: ObservationRepository,
        events: EventRepository,
        lexical: LexicalIndex,
        embedder: TextEmbedder,
        vectors: VectorRepository,
        corrections: MemoryCorrectionRepository,
        faces: FaceRepository | None = None,
        event_publisher: Callable[[str], object] | None = None,
    ) -> None:
        self.observations = observations
        self.events = events
        self.lexical = lexical
        self.embedder = embedder
        self.vectors = vectors
        self.corrections = corrections
        self.faces = faces
        self.event_publisher = event_publisher

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
            if self.faces is not None:
                self.faces.refresh_occurrences_for_observation(observation.id)
            event_ids = events.get(observation.id, ())
            outcome = index.index(observation, event_ids[0] if event_ids else None, force=True)
            if not outcome.indexed and outcome.reason != "empty_text":
                raise RuntimeError(outcome.reason)
        affected_event_ids = set(self.corrections.affected_events(scope))
        affected_event_ids.update(eid for ids in events.values() for eid in ids)
        for event_id in sorted(affected_event_ids):
            if self.events.get(event_id) is None:
                continue
            # 事件正文/时间线/新索引统一从 active 文档重建；旧的 event 文本向量不再续写。
            if self.event_publisher is not None:
                self.event_publisher(event_id)


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
        """用户显式要求的同步（「重试同步」「修正并同步关联记忆」）：当场跑完并回报结果。"""
        with SYNC_LOCK, self._repository.graph_sync_lock():
            self._repository.requeue_legacy_sync(scope_id)
            batch = self._repository.next_legacy_sync_batch(scope_id, datetime.now(UTC).isoformat())
            error = self._derive(scope_id)
            self._repository.finish_legacy_sync_batch(batch[1] if batch else (), error)
            return error

    def execute_next_legacy_batch(self) -> bool:
        """后台：取一个有到期修正任务的相册，重建一次派生数据，覆盖这一批全部任务。"""
        if self._repository.next_legacy_sync_batch(None, datetime.now(UTC).isoformat()) is None:
            return False
        with SYNC_LOCK, self._repository.graph_sync_lock():
            batch = self._repository.next_legacy_sync_batch(None, datetime.now(UTC).isoformat())
            if batch is None:
                return False
            scope_id, ids = batch
            self._repository.finish_legacy_sync_batch(ids, self._derive(scope_id))
            return True

    def _derive(self, scope_id: str) -> str:
        errors = []
        for operation in (self._repository.refresh_people, self._graph, self._reindex):
            try:
                operation(scope_id)
            except Exception as exc:  # noqa: BLE001 - 各派生步骤独立，失败保留任务
                message = f"{type(exc).__name__}: {exc}"
                errors.append(message)
                log.warning("记忆修正同步待重试 scope=%s: %s", scope_id, message)
        return "; ".join(errors)

    def execute_next_name_batch(self) -> bool:
        if self._repository.next_name_sync_batch(datetime.now(UTC).isoformat()) is None:
            return False
        with SYNC_LOCK, self._repository.graph_sync_lock():
            batch = self._repository.next_name_sync_batch(datetime.now(UTC).isoformat())
            if batch is None:
                return False
            scope_id, through = batch
            error = ""
            try:
                # Name snapshots and person_bridge already commit with rename.
                # Neither canonical names nor aliases are inputs to text vectors.
                self._graph(scope_id)
            except Exception as exc:  # noqa: BLE001 - the committed name stays saved
                log.warning("人物姓名记忆图待重试 scope=%s: %s", scope_id, exc)
                error = "关联记忆图同步失败，请重试。"
            self._repository.finish_name_sync_batch(scope_id, through, error)
            return True


class CorrectionSyncExecutor:
    """在 HTTP 请求之外跑修正后的派生同步：姓名任务（只重建记忆图）与通用修正任务。

    关系、家庭归属等写入只登记任务就返回；重建记忆图（含因果判定）与重算向量要十几秒，
    不能让用户在请求里等。同一相册的多次写入在这里合并成一次重建。
    """

    def __init__(self, sync: SyncCorrections, *, idle_seconds: float = 0.5) -> None:
        self._sync = sync
        self._idle_seconds = idle_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="correction-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        _WAKE.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def run_once(self) -> bool:
        """跑完当前所有到期任务；有活干过就返回 True。"""
        worked = False
        while not self._stop.is_set():
            # 姓名任务只重建图、很快，先跑；两类任务互不吞并。
            progressed = self._sync.execute_next_name_batch()
            progressed = self._sync.execute_next_legacy_batch() or progressed
            if not progressed:
                break
            worked = True
        return worked

    def _run(self) -> None:
        while not self._stop.is_set():
            _WAKE.wait(self._idle_seconds)
            _WAKE.clear()
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - transient storage failures leave the job queued
                log.exception("无法读取或更新记忆修正同步任务")

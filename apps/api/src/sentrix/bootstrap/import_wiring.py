"""导入流水线的装配根。

单张上传、整册导入、视频关键帧与持久作业执行器共用这一处接线。放在 bootstrap
（组合根）而不是 api 路由里：执行器在进程内，不应该为了拿一个 ImportAssets 而
反向依赖 HTTP 层。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sentrix.application.identity.enroll_face import EnrollFace, EnrollFaces
from sentrix.application.identity.extract_moments import ExtractMoments
from sentrix.application.identity.family_graph_service import FamilyGraphService
from sentrix.application.identity.run_insights import RunInsights
from sentrix.application.media.extract_video_scenes import ExtractVideoScenes, VideoSceneExtraction
from sentrix.application.media.import_assets import ImportAssets, ImportResult, StagedImport
from sentrix.application.media.index_place import IndexAssetPlace
from sentrix.application.media.maintain_entities import MaintainObservationEntities
from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.application.memories.assign_events import (
    AssignEvents,
    ConfirmedPersonEvents,
    EventVisualSimilarity,
)
from sentrix.application.memories.build_graph import BuildMemoryGraph
from sentrix.application.memories.maintain_facts import MaintainFacts
from sentrix.application.memories.summarize_event import SummarizeEvent
from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.application.retrieval.index_visual import IndexAssetVisual
from sentrix.domain.media.import_jobs import ImportJob
from sentrix.domain.media.models import AssetStatus, MediaType, Observation
from sentrix.domain.memories.graph import GraphBuildStats
from sentrix.runtime.work import import_work

if TYPE_CHECKING:
    from sentrix.bootstrap.container import Container

log = logging.getLogger("sentrix.bootstrap.import_wiring")


def build_memory_graph(container: Container) -> BuildMemoryGraph:
    """把 2.0 标准记忆端口接到独立图投影用例。"""
    return BuildMemoryGraph(
        generation_guard=container.database.model_generation,
        assets=container.asset_repository,
        observations=container.observation_repository,
        events=container.event_repository,
        participants=container.event_participant_repository,
        entities=container.entity_repository,
        facts=container.fact_repository,
        relationships=container.relationship_repository,
        graph=container.memory_graph,
        causal_judge=container.causal_judge,
        vectors=container.vector_repository,
    )


def refresh_memory_graph(container: Container, scope_id: str) -> GraphBuildStats | None:
    """在写入链路结束后重建派生图；图失败不阻断权威记忆写入。"""
    if not _shared_memory_writable(container, scope_id):
        return None
    try:
        return build_memory_graph(container).execute(scope_id)
    except Exception as error:  # noqa: BLE001 - 派生投影必须可降级
        log.warning("记忆图重建失败（scope=%s），保留主链路结果: %s", scope_id, error)
        return None


def build_import_assets(container: Container, *, observe: bool) -> ImportAssets:
    """把导入用例的协作者接起来。**只在这里接一次**。"""
    keyframe_importer = _build_importer(container, observe=True)
    extractor = _build_video_scenes(container, keyframe_importer.process_image)
    return _build_importer(
        container,
        observe=observe,
        video_scene_extractor=extractor.execute,
    )


def build_media_postprocess(container: Container) -> Callable[[str, int], list[str]]:
    """一个相册代际的批末后处理：参与者 → 记忆图 → 事件总结 → 家族图 → 画像。

    返回未完成的阶段名。局部失败不阻断其他阶段，也不回滚已提交的资产；
    执行器据此把作业标为 ``partial`` 并保留具体阶段。阶段与事件进度写进
    ``scope_postprocess``，用户页据此展示第二段进度。
    """

    def run(scope_id: str, generation: int) -> list[str]:
        # 整段后处理都是“建库”工作，模型调用必须按 import 准入；否则事件总结会
        # 被当成问答占满槽位，用户提问反而要排队。
        with import_work(), container.database.model_generation(scope_id):
            return _run(scope_id, generation)

    def _stage(scope_id: str, generation: int, stage: str, total: int = 0) -> None:
        container.import_jobs.begin_postprocess_stage(
            scope_id,
            generation,
            stage,
            events_total=total,
            now=datetime.now(UTC),
        )

    def _progress(scope_id: str, generation: int, done: int, total: int) -> None:
        container.import_jobs.update_postprocess_progress(
            scope_id,
            generation,
            done=done,
            total=total,
            now=datetime.now(UTC),
        )

    def _run(scope_id: str, generation: int) -> list[str]:
        with container.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM media_scope_epochs WHERE scope_id=? AND paused=1 LIMIT 1",
                (scope_id,),
            ).fetchone():
                return ["media_isolation_deferred"]
        failures: list[str] = []
        _stage(scope_id, generation, "participants")
        observations = container.observation_repository.list_scope(scope_id, limit=100000)
        try:
            _confirmed_person_events(container).refresh_participants([item.id for item in observations])
        except Exception as error:  # noqa: BLE001 - 单个后处理阶段失败不阻断其他阶段
            log.warning("参与者刷新失败（scope=%s）: %s", scope_id, error)
            failures.append("participants")
        _stage(scope_id, generation, "memory_graph")
        try:
            refresh_memory_graph(container, scope_id)
        except Exception as error:  # noqa: BLE001
            log.warning("记忆图刷新失败（scope=%s）: %s", scope_id, error)
            failures.append("memory_graph")
        summarizer = SummarizeEvent(
            generation_guard=container.database.model_generation,
            events=container.event_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
            participants=container.event_participant_repository,
            model=container.chat_model,
        )
        # 事件之间彼此独立，模型调用可以并行；数据库写入由 30s busy_timeout + WAL 串行化。
        pending = [
            event
            for event in container.event_repository.list_by_scope(scope_id, limit=10000)
            # 事件归属会先写一句初步摘要，但标题仍是“待总结事件”；只按 summary
            # 判断会把这些事件永久跳过，时间线就一直是占位标题。
            if not (event.summary.strip() and event.title.strip() not in {"", "待总结事件"})
        ]
        total = len(pending)
        _stage(scope_id, generation, "event_summary", total=total)

        def summarize(event_id: str) -> None:
            # ThreadPoolExecutor 不继承 contextvars，必须在这里显式标成导入工作。
            with import_work():
                summarizer.execute(event_id)

        summary_failed = False
        done = 0
        workers = max(1, min(4, int(getattr(container.settings, "import_workers", 4))))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sentrix-summary") as pool:
            futures = {pool.submit(copy_context().run, summarize, event.id): event for event in pending}
            for future in as_completed(futures):
                done += 1
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001 - 单个事件失败不阻断整代
                    log.warning("事件总结失败（event=%s）: %s", futures[future].id, error)
                    summary_failed = True
                if done == total or done % 5 == 0:
                    _progress(scope_id, generation, done, total)
        if summary_failed:
            failures.append("event_summary")
        _stage(scope_id, generation, "family_graph")
        if not _run_family_graph(container, scope_id):
            failures.append("family_graph")
        _stage(scope_id, generation, "insights")
        try:
            _run_insights(container)(scope_id)
        except Exception as error:  # noqa: BLE001
            log.warning("画像刷新失败（scope=%s）: %s", scope_id, error)
            failures.append("insights")
        return failures

    return run


def _confirmed_person_events(container: Container) -> ConfirmedPersonEvents:
    return ConfirmedPersonEvents(
        events=container.event_repository,
        participants=container.event_participant_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        entities=container.entity_repository,
        relationships=container.relationship_repository,
        visual_similarity=EventVisualSimilarity(
            vectors=container.vector_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
        ),
        confirmed_people=container.entity_repository.confirmed_people_for_observation,
        confirmed_event_people=container.entity_repository.confirmed_people_for_event,
    )


def _run_family_graph(container: Container, scope_id: str) -> bool:
    """批末家族图推断；已有 queued/running 的 run 或失败都不阻断其他后处理。"""
    try:
        repository = container.family_graph_repository
        latest = repository.latest_run(scope_id)
        if latest is not None and latest.status in {"queued", "running"}:
            return True
        run = repository.create_run(scope_id, {"trigger_type": "ingest", "input_mode": "semantic_text_only"})
        service = FamilyGraphService(
            generation_guard=container.database.model_generation,
            family=repository,
            events=container.event_repository,
            entities=container.entity_repository,
            model=container.chat_model,
        )
        service.run(run.id, scope_id)
        return True
    except Exception as error:  # noqa: BLE001 - 家族图是导入副作用
        log.warning("家族图推断失败（scope=%s）: %s", scope_id, error)
        return False


def _run_insights(container: Container) -> RunInsights:
    return RunInsights(
        generation_guard=container.database.model_generation,
        faces=container.face_repository,
        entities=container.entity_repository,
        moments=container.moment_repository,
        relationships=container.relationship_repository,
        portraits=container.portrait_repository,
        extract_moments=ExtractMoments(
            generation_guard=container.database.model_generation,
            assets=container.asset_repository,
            faces=container.face_repository,
            entities=container.entity_repository,
            moments=container.moment_repository,
            previews=container.person_preview_renderer,
            vision=container.person_vision,
            workers=container.settings.album_workers,
        ),
        vision=container.person_vision,
    )


def build_video_scenes(
    container: Container,
    *,
    checkpoint: Callable[[str, int, int, int], None] | None = None,
    resume_event_completed: int = 0,
) -> ExtractVideoScenes:
    """独立的抽取链，给重跑与执行器共用。"""
    return _build_video_scenes(
        container,
        _build_importer(container, observe=True).process_image,
        checkpoint=checkpoint,
        resume_event_completed=resume_event_completed,
    )


def build_video_job_processor(
    container: Container,
) -> Callable[[ImportJob, StagedImport], ImportResult]:
    """让普通网页上传的视频也走 durable ``video_jobs``。

    导入执行器仍保留 ``import_jobs`` 作为上传/UI 的作业壳；视频的探测、ASR、抽帧、
    场景、事件与记忆阶段交给 ``video_job_stages``。执行器线程在这里等待视频作业跑完，
    所以上传后看到的仍是同一个导入作业，但断点粒度从“整条作业”细化到八个阶段。

    单视频作业池只有一个 worker（抽帧/ASR 是重活）；等待期间导入作业的心跳由
    执行器自己的 ``_Heartbeat`` 续租，不会因为等待而丢 lease。
    """
    timeout_s = 6 * 3600

    def process(job: ImportJob, staged: StagedImport) -> ImportResult:
        asset = staged.asset
        repository = container.video_repository
        existing = repository.get_job_for_asset(asset.id)
        if existing is None:
            from sentrix.api.routes.media import _video_config_hash

            existing = repository.create_job(
                asset.id,
                asset.content_sha256 or "",
                f"vjob_{uuid.uuid4().hex[:16]}",
                _video_config_hash(container),
            )
        video_job_id = str(existing["job_id"])
        from sentrix.api.routes.media import resume_video_job

        resume_video_job(container, video_job_id)

        deadline = time.monotonic() + timeout_s
        while True:
            current = repository.get_job(video_job_id)
            if current is None:
                raise RuntimeError(f"video job disappeared: {video_job_id}")
            status = str(current.get("status") or "")
            if status in {"COMPLETED", "DEGRADED"}:
                break
            if status in {"FAILED", "CANCELLED"}:
                detail = str(current.get("error_message") or current.get("error_code") or status)
                raise RuntimeError(f"video job {status}: {detail}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"video job timeout: {video_job_id}")
            time.sleep(1.0)

        scenes = len(repository.list_derived(asset.id))
        return ImportResult(
            asset_id=asset.id,
            observation_id=None,
            media_type=MediaType.VIDEO,
            status=AssetStatus.PROCESSED,
            deduplicated=False,
            video_probed=True,
            video_scenes=scenes,
        )

    return process


def _build_video_scenes(
    container: Container,
    process_image: Callable[..., object],
    *,
    checkpoint: Callable[[str, int, int, int], None] | None = None,
    resume_event_completed: int = 0,
) -> ExtractVideoScenes:
    return ExtractVideoScenes(
        assets=container.asset_repository,
        events=container.event_repository,
        places=container.asset_place_repository,
        video_repository=container.video_repository,
        keyframes=container.video_keyframe_extractor,
        observations=container.observation_repository,
        process_image=lambda asset, event_id: process_image(asset, forced_event_id=event_id),
        scene_planner=container.video_scene_planner,
        transcriber=container.audio_transcriber,
        cross_modal_embedder=container.cross_modal_embedder,
        has_memory=lambda asset: bool(container.observation_repository.list_by_asset(asset.id)),
        checkpoint=checkpoint,
        resume_event_completed=resume_event_completed,
    )


def _build_importer(
    container: Container,
    *,
    observe: bool,
    video_scene_extractor: Callable[[str], VideoSceneExtraction | int] | None = None,
) -> ImportAssets:
    """``ImportAssets`` 的装配本体。两个调用方共用：导入本身，以及关键帧那条流水线。"""
    assigner = AssignEvents(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        places=container.asset_place_repository,
        visual_similarity=EventVisualSimilarity(
            vectors=container.vector_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
        ),
        confirmed_people=container.entity_repository.confirmed_people_for_observation,
        confirmed_event_people=container.entity_repository.confirmed_people_for_event,
    )

    def assign_active(observation: Observation) -> str:
        with container.database.asset_commit(observation.asset_id):
            return _event_id(assigner, observation)

    return ImportAssets(
        assets=container.asset_repository,
        observations=container.observation_repository,
        storage=container.media_storage,
        metadata=container.metadata_reader,
        visual_indexer=IndexAssetVisual(
            embedder=container.visual_embedder,
            vectors=container.vector_repository,
            index=container.vector_index,
        ),
        place_indexer=IndexAssetPlace(
            resolver=container.place_resolver, places=container.asset_place_repository
        ),
        observer=(
            ObserveAsset(
                observer=container.vlm_observer,
                observations=container.observation_repository,
            )
            if observe
            else None
        ),
        shared_memory_writable=lambda scope: _shared_memory_writable(container, scope),
        event_assigner=assign_active,
        event_attacher=lambda event_id, observation: container.event_repository.attach(
            event_id, observation.id
        ),
        observation_event=lambda observation_id: _observation_event(container, observation_id),
        face_manifest=container.face_repository.list_face_manifest,
        face_enroller=_face_enroller(container),
        fact_maintainer=_fact_maintainer(container),
        text_indexer=_text_indexer(container),
        entity_extractor=_entity_extractor(container),
        lexical_index=container.lexical_index,
        spaces=container.spaces,
        video_metadata=container.video_metadata_reader,
        video_transcoder=container.video_transcoder,
        video_repository=container.video_repository,
        audio_metadata=container.audio_metadata_reader,
        audio_repository=container.audio_repository,
        video_scene_extractor=video_scene_extractor,
    )


def _face_enroller(container: Container) -> EnrollFaces:
    return EnrollFaces(
        detector=container.face_detector,
        enroll=EnrollFace(
            commit_guard=container.database.asset_commit,
            faces=container.face_repository,
            entities=container.entity_repository,
            vectors=container.vector_repository,
            assets=container.asset_repository,
        ),
    )


def _text_indexer(container: Container) -> Callable[[Observation, str | None], int]:
    index = IndexObservationText(
        embedder=container.text_embedder,
        vectors=container.text_vector_repository,
        generation_guard=container.database.model_generation,
    )

    def run(observation: Observation, event_id: str | None) -> int:
        return index.index(observation, event_id).rows

    return run


def _entity_extractor(container: Container) -> Callable[[Observation, str], tuple[str, ...]]:
    maintain = MaintainObservationEntities(
        entities=container.entity_repository,
        events=container.event_participant_repository,
        assets=container.asset_repository,
        relationships=container.relationship_repository,
    )

    def run(observation: Observation, event_id: str) -> tuple[str, ...]:
        with container.database.asset_commit(observation.asset_id):
            return tuple(entity.id for entity in maintain.execute(observation, event_id=event_id).entities)

    return run


def _fact_maintainer(container: Container) -> Callable[[Observation], tuple[str, ...]]:
    maintain = MaintainFacts(
        facts=container.fact_repository,
        candidates=lambda observation: observation.facts,
        confirmed_people=container.entity_repository.confirmed_person_names,
    )

    def run(observation: Observation) -> tuple[str, ...]:
        with container.database.asset_commit(observation.asset_id):
            return maintain.maintain(observation)

    return run


def _event_id(assigner: AssignEvents, observation: Observation) -> str:
    return assigner.execute(observation).event.id


def _observation_event(container: Container, observation_id: str) -> str | None:
    """该观察已经挂到的事件（取第一个）。重放时直接复用，不再跑候选打分。"""
    mapping = container.event_repository.events_for_observations([observation_id])
    event_ids = mapping.get(observation_id, ())
    return event_ids[0] if event_ids else None


def _shared_memory_writable(container: Container, scope_id: str) -> bool:
    with container.database.connect() as connection:
        return (
            connection.execute(
                "SELECT 1 FROM media_scope_epochs WHERE scope_id=? AND paused=1", (scope_id,)
            ).fetchone()
            is None
        )

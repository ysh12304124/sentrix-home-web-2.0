"""导入流水线的装配根。

单张上传、整册导入、视频关键帧与持久作业执行器共用这一处接线。放在 bootstrap
（组合根）而不是 api 路由里：执行器在进程内，不应该为了拿一个 ImportAssets 而
反向依赖 HTTP 层。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from sentrix.application.identity.enroll_face import EnrollFace, EnrollFaces
from sentrix.application.identity.extract_moments import ExtractMoments
from sentrix.application.identity.run_insights import RunInsights
from sentrix.application.media.extract_video_scenes import ExtractVideoScenes, VideoSceneExtraction
from sentrix.application.media.import_assets import ImportAssets
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
from sentrix.domain.media.models import Observation
from sentrix.domain.memories.graph import GraphBuildStats

if TYPE_CHECKING:
    from sentrix.bootstrap.container import Container

log = logging.getLogger("sentrix.bootstrap.import_wiring")


def build_memory_graph(container: Container) -> BuildMemoryGraph:
    """把 2.0 标准记忆端口接到独立图投影用例。"""
    return BuildMemoryGraph(
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


def build_media_postprocess(container: Container) -> Callable[[str], None]:
    """一个相册代际的批末后处理：参与者 → 记忆图 → 事件总结 → 人物画像。"""

    def run(scope_id: str) -> None:
        observations = container.observation_repository.list_scope(scope_id, limit=100000)
        _confirmed_person_events(container).refresh_participants(
            [item.id for item in observations]
        )
        refresh_memory_graph(container, scope_id)
        summarizer = SummarizeEvent(
            events=container.event_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
            participants=container.event_participant_repository,
            model=container.chat_model,
        )
        for event in container.event_repository.list_by_scope(scope_id, limit=10000):
            if event.summary.strip():
                continue
            try:
                summarizer.execute(event.id)
            except Exception as error:  # noqa: BLE001 - 单个事件失败不阻断整代
                log.warning("事件总结失败（event=%s）: %s", event.id, error)
        _run_insights(container)(scope_id)

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


def _run_insights(container: Container) -> RunInsights:
    return RunInsights(
        faces=container.face_repository,
        entities=container.entity_repository,
        moments=container.moment_repository,
        relationships=container.relationship_repository,
        portraits=container.portrait_repository,
        extract_moments=ExtractMoments(
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


def build_video_scenes(container: Container) -> ExtractVideoScenes:
    """独立的抽取链，给重跑与执行器共用。"""
    return _build_video_scenes(container, _build_importer(container, observe=True).process_image)


def _build_video_scenes(
    container: Container, process_image: Callable[..., object]
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
        event_assigner=lambda observation: _event_id(assigner, observation),
        event_attacher=lambda event_id, observation: container.event_repository.attach(
            event_id, observation.id
        ),
        observation_event=lambda observation_id: _observation_event(
            container, observation_id
        ),
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
            faces=container.face_repository,
            entities=container.entity_repository,
            vectors=container.vector_repository,
            assets=container.asset_repository,
        ),
    )


def _text_indexer(container: Container) -> Callable[[Observation, str | None], int]:
    index = IndexObservationText(
        embedder=container.text_embedder, vectors=container.vector_repository
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
        return tuple(entity.id for entity in maintain.execute(observation, event_id=event_id).entities)

    return run


def _fact_maintainer(container: Container) -> Callable[[Observation], tuple[str, ...]]:
    maintain = MaintainFacts(
        facts=container.fact_repository,
        candidates=lambda observation: observation.facts,
        confirmed_people=container.entity_repository.confirmed_person_names,
    )
    return maintain.maintain


def _event_id(assigner: AssignEvents, observation: Observation) -> str:
    return assigner.execute(observation).event.id


def _observation_event(container: Container, observation_id: str) -> str | None:
    """该观察已经挂到的事件（取第一个）。重放时直接复用，不再跑候选打分。"""
    mapping = container.event_repository.events_for_observations([observation_id])
    event_ids = mapping.get(observation_id, ())
    return event_ids[0] if event_ids else None

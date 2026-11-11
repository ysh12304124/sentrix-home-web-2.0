"""问答回合与检索的装配根。

HTTP 路由与进程内回合执行器共用同一份接线；执行器不能反向依赖 api 层。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from sentrix.application.assistant.query_memory_facts import QueryMemoryFacts
from sentrix.application.assistant.run_turn import RunTurn
from sentrix.application.assistant.tools import AssistantToolbox
from sentrix.application.retrieval.evaluate_conditions import EvaluateConditions
from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.application.retrieval.retrieve_evidence import RetrieveEvidence
from sentrix.application.retrieval.retrieve_graph import RetrieveGraph
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.assistant.profile import DEFAULT_PROFILE

if TYPE_CHECKING:
    from sentrix.bootstrap.container import Container


def build_retrieve_evidence(container: Container) -> RetrieveEvidence:
    return RetrieveEvidence(
        visual=RetrieveVisual(
            embedder=container.visual_embedder,
            vectors=container.vector_repository,
            index=container.vector_index,
        ),
        text=RetrieveTextAnn(embedder=container.text_embedder, vectors=container.text_vector_repository),
        entity=RetrieveEntity(
            index=container.lexical_search, observations=container.observation_repository
        ),
        lexical=RetrieveLexical(index=container.lexical_search),
        metadata=RetrieveMetadata(
            assets=container.asset_repository,
            places=container.asset_place_repository,
            aliases=container.place_aliases,
            recognize_place=lambda asset_id: _has_gps(container, asset_id),
        ),
        conditions=EvaluateConditions(
            assets=container.asset_repository,
            observations=container.observation_repository,
            places=container.asset_place_repository,
            aliases=container.place_aliases,
            confirmed_people=_confirmed_people(container),
        ),
        graph=RetrieveGraph(graph=container.memory_graph),
        # 重排只重排 RRF 后的头部；``container.reranker is None`` 时整段不执行。
        reranker=container.reranker,
        observations=container.observation_repository,
        rerank_top_k=container.settings.rerank_top_k,
    )


def build_run_turn(container: Container) -> RunTurn:
    return RunTurn(
        model=container.chat_model,
        operation_control=container.operation_control,
        conversations=container.conversation_repository,
        turns=container.turn_repository,
        toolbox=AssistantToolbox(
            retrieval=build_retrieve_evidence(container),
            assets=container.asset_repository,
            result_sets=container.result_set_repository,
            people=container.person_directory,
            memory_facts=QueryMemoryFacts(
                structured=container.structured_query_repository,
                entities=container.entity_repository,
            ),
            faces=container.face_repository,
            observations=container.observation_repository,
            photo_inspector=container.photo_inspector,
            person_crops=container.person_crop_renderer,
            events=container.event_repository,
            asset_places=container.asset_place_repository,
            videos=container.video_repository,
        ),
        profile=replace(
            DEFAULT_PROFILE,
            wall_time_s=container.settings.agent_wall_time_s,
            max_steps=container.settings.agent_max_steps,
            max_tool_calls=container.settings.agent_max_tool_calls,
            strict_model_step_budget=container.settings.vlm_exact_output_budget,
        ),
    )


def _confirmed_people(container: Container) -> Callable[[str | None], frozenset[str]]:
    def names(scope_id: str | None) -> frozenset[str]:
        if scope_id is None:
            return frozenset()
        return container.entity_repository.confirmed_person_names(scope_id)

    return names


def _has_gps(container: Container, asset_id: str) -> bool:
    asset = container.asset_repository.get(asset_id)
    return asset is not None and asset.captured_location is not None

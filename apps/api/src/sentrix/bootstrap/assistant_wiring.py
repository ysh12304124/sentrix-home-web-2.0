"""问答回合与检索的装配根。

HTTP 路由与进程内回合执行器共用同一份接线；执行器不能反向依赖 api 层。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from sentrix.application.assistant.query_memory_facts import QueryMemoryFacts
from sentrix.application.assistant.run_turn import RunTurn
from sentrix.application.assistant.tools import AssistantToolbox, ToolLimits
from sentrix.application.retrieval.evaluate_conditions import EvaluateConditions
from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.application.retrieval.retrieve_evidence import RetrieveEvidence
from sentrix.application.retrieval.retrieve_graph import RetrieveGraph
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.assistant.ports import ReasoningChatModel
from sentrix.domain.assistant.profile import CLOUD_PROFILE, DEFAULT_PROFILE, REASONING_PROFILE, TurnProfile

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
        entity=RetrieveEntity(index=container.lexical_search, observations=container.observation_repository),
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
        utility=container.visual_evidence_utility,
        utility_concurrency=container.settings.utility_vlm_concurrency,
        assets=container.asset_repository,
        media_storage=container.media_storage,
        # 重排只重排 RRF 后的头部；``container.reranker is None`` 时整段不执行。
        reranker=container.reranker,
        observations=container.observation_repository,
        rerank_top_k=container.settings.rerank_top_k,
    )


def _limit(agent: Mapping[str, Any], key: str, default: int | None) -> int | None:
    if key not in agent:
        return default
    value = agent[key]
    return None if value is None else int(value)


def _turn_profile(container: Container) -> TurnProfile:
    if container.model_profile is None:
        return replace(
            DEFAULT_PROFILE,
            wall_time_s=container.settings.agent_wall_time_s,
            max_steps=container.settings.agent_max_steps,
            max_tool_calls=container.settings.agent_max_tool_calls,
            strict_model_step_budget=container.settings.vlm_exact_output_budget,
        )
    agent = dict(container.model_profile.agent)
    return replace(
        CLOUD_PROFILE,
        max_steps=_limit(agent, "max_steps", CLOUD_PROFILE.max_steps),
        max_tool_calls=_limit(agent, "max_tool_calls", CLOUD_PROFILE.max_tool_calls),
        max_search_calls=_limit(agent, "max_search_calls", CLOUD_PROFILE.max_search_calls),
        max_page_calls=_limit(agent, "max_page_calls", CLOUD_PROFILE.max_page_calls),
        max_inspections=_limit(agent, "max_inspections", CLOUD_PROFILE.max_inspections),
        wall_time_s=float(agent.get("wall_time_s", CLOUD_PROFILE.wall_time_s)),
        history_candidate_messages=int(
            agent.get("history_candidate_messages", CLOUD_PROFILE.history_candidate_messages)
        ),
        history_char_budget=int(agent.get("history_char_budget", CLOUD_PROFILE.history_char_budget)),
        judge_transcript_messages=int(
            agent.get("judge_transcript_messages", CLOUD_PROFILE.judge_transcript_messages)
        ),
        judge_message_chars=int(agent.get("judge_message_chars", CLOUD_PROFILE.judge_message_chars)),
        claim_max_facts=int(agent.get("claim_max_facts", CLOUD_PROFILE.claim_max_facts)),
        claim_value_limit=int(agent.get("claim_value_limit", CLOUD_PROFILE.claim_value_limit)),
        compaction_mode=str(agent.get("compaction_mode", CLOUD_PROFILE.compaction_mode)),
        compaction_preview_rewrite=bool(
            agent.get("compaction_preview_rewrite", CLOUD_PROFILE.compaction_preview_rewrite)
        ),
        compaction_protect_latest_preview=bool(
            agent.get(
                "compaction_protect_latest_preview",
                CLOUD_PROFILE.compaction_protect_latest_preview,
            )
        ),
        compaction_max_preview_level=int(
            agent.get("compaction_max_preview_level", CLOUD_PROFILE.compaction_max_preview_level)
        ),
    )


def _tool_limits(container: Container) -> ToolLimits:
    if container.model_profile is None:
        return ToolLimits()
    agent = container.model_profile.agent
    return ToolLimits(
        summary_text_chars=int(agent.get("preview_summary_chars", 300)),
        summary_ocr_chars=int(agent.get("preview_ocr_chars", 120)),
        summary_list_items=int(agent.get("preview_list_items", 8)),
        speech_segments=int(agent.get("speech_segments", 12)),
        speech_chars=int(agent.get("speech_chars", 1000)),
        read_text_chars=int(agent.get("read_text_chars", 1600)),
        read_text_lines=int(agent.get("read_text_lines", 8)),
        read_text_line_chars=int(agent.get("read_text_line_chars", 150)),
    )


def build_run_turn(container: Container, *, reasoning: bool = False) -> RunTurn:
    profile = _turn_profile(container)
    model = container.chat_model
    if reasoning:
        if isinstance(model, ReasoningChatModel):
            model = model.for_reasoning()
        profile = replace(
            profile,
            name=REASONING_PROFILE.name,
            max_steps=container.settings.reasoning_max_model_calls,
            max_model_calls=container.settings.reasoning_max_model_calls,
            max_tool_calls=container.settings.reasoning_max_tool_calls,
            max_search_calls=container.settings.reasoning_max_search_calls,
            max_other_tool_calls=container.settings.reasoning_max_other_tool_calls,
            max_page_calls=REASONING_PROFILE.max_page_calls,
            max_inspections=REASONING_PROFILE.max_inspections,
            wall_time_s=max(profile.wall_time_s, REASONING_PROFILE.wall_time_s),
        )
    toolbox = AssistantToolbox(
        retrieval=build_retrieve_evidence(container),
        assets=container.asset_repository,
        result_sets=container.result_set_repository,
        people=container.person_directory,
        memory_facts=QueryMemoryFacts(
            structured=container.structured_query_repository,
            people=container.person_directory,
        ),
        spaces=container.spaces,
        faces=container.face_repository,
        observations=container.observation_repository,
        photo_inspector=container.photo_inspector,
        person_crops=container.person_crop_renderer,
        events=container.event_repository,
        asset_places=container.asset_place_repository,
        limits=_tool_limits(container),
    )
    return RunTurn(
        model=model,
        operation_control=container.operation_control,
        conversations=container.conversation_repository,
        turns=container.turn_repository,
        toolbox=toolbox,
        known_person_answer=toolbox.known_person_answer,
        observations=container.observation_repository,
        profile=profile,
        use_all_tools=reasoning,
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

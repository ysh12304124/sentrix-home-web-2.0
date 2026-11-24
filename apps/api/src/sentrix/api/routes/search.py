"""内容词检索。只做 HTTP 映射，召回与融合在 application 层。"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from sentrix.api.deps import get_container
from sentrix.api.errors import ConflictError, DomainErrorResponse, UnprocessableError
from sentrix.api.routes.media import to_summary
from sentrix.api.schemas.search import (
    ChannelTrace,
    OccurrenceMatch,
    SearchHit,
    SearchResponse,
)
from sentrix.application.identity.person_directory import assets_for_names
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, EvidenceResult
from sentrix.bootstrap.assistant_wiring import build_retrieve_evidence
from sentrix.bootstrap.container import Container
from sentrix.domain.assistant.models import AmbiguousPersonName
from sentrix.domain.identity.models import PersonOccurrenceRecord
from sentrix.domain.media.capture_time import within_time_bounds
from sentrix.domain.media.models import Asset, MediaType
from sentrix.domain.retrieval.conditions import Condition
from sentrix.domain.retrieval.time_expression import parse_time_expression

log = logging.getLogger("sentrix.api.search")

router = APIRouter(tags=["search"])


def _conditions(*, time_range: str, place: str, media_type: str) -> tuple[Condition, ...]:
    """把查询参数变成结构化条件。

    **每条都是 HARD。** 1.0 的 ``HardFilterContext.from_spec`` 只把
    ``strictness == HARD`` 的约束放进过滤上下文，而 HTTP 这一层没有「软条件」的入口——
    用户显式填进 ``time_range`` 的东西不该被当成建议。哪天要加 SEMANTIC 档，
    那是一个新的查询参数，不是改这里的默认值。

    **认不出来的时间表达式不产生条件。** ``parse_time_expression`` 返回 ``None`` 时
    这里跳过——若硬造一个空区间的条件，它的判定会是 UNKNOWN，而 HARD 档下
    UNKNOWN 一样排除候选，于是「用户输入了个看不懂的时间」会表现为「一张照片都搜不到」。
    """
    built: list[Condition] = []
    if time_range.strip() and parse_time_expression(time_range) is not None:
        built.append(Condition(dimension="time", value=time_range.strip(), key="time"))
    if place.strip():
        built.append(Condition(dimension="place", value=place.strip(), key="place"))
    if media_type.strip():
        built.append(Condition(dimension="media", value=media_type.strip(), key="media"))
    return tuple(built)


def _has_gps(container: Container, asset_id: str) -> bool:
    asset = container.asset_repository.get(asset_id)
    return asset is not None and asset.captured_location is not None


def _linked_assets(
    container: Container,
    names: list[str],
    person_ids: list[str],
    scope_id: str,
) -> tuple[str, ...] | None:
    """姓名与显式人物 ID 都是 AND 条件；返回 None 表示没有人物条件。"""
    linked = assets_for_names(container.person_directory, names, scope_id)
    for person_id in dict.fromkeys(item.strip() for item in person_ids if item.strip()):
        matching = set(container.face_repository.asset_ids_for_person(person_id, scope_id=scope_id))
        linked = (
            tuple(asset_id for asset_id in linked if asset_id in matching)
            if linked is not None
            else tuple(matching)
        )
    return linked


def _occurrence_matches(
    container: Container,
    *,
    scope_id: str,
    description: str,
    person_ids: list[str],
    limit: int,
) -> dict[str, list[PersonOccurrenceRecord]]:
    allowed = set(item.strip() for item in person_ids if item.strip())
    grouped: dict[str, list[PersonOccurrenceRecord]] = {}
    records = container.face_repository.list_occurrences(
        scope_id=scope_id,
        description_query=description,
        limit=max(1, limit),
    )
    for record in records:
        if allowed and (record.person_id or "") not in allowed:
            continue
        grouped.setdefault(record.asset_id, []).append(record)
    return grouped


def _occurrence_payload(record: PersonOccurrenceRecord) -> OccurrenceMatch:
    return OccurrenceMatch(
        face_instance_id=record.face_instance_id,
        asset_id=record.asset_id,
        observation_id=record.observation_id,
        person_id=record.person_id,
        display_label=record.display_label,
        identity_state=record.identity_state,
        bbox=list(record.bbox),
        description=record.description,
        matched_fields=list(record.matched_fields),
        evidence_refs=list(record.evidence_refs),
        source_revision=record.description_revision,
    )


def _with_occurrences(hit: SearchHit, occurrences: list[PersonOccurrenceRecord], *, rank: int) -> SearchHit:
    fields = sorted(
        set(hit.matched_fields) | {field for record in occurrences for field in record.matched_fields}
    )
    refs = sorted(set(hit.evidence_refs) | {ref for record in occurrences for ref in record.evidence_refs})
    channels = dict(hit.channels)
    channels.setdefault("person_occurrence", rank)
    return hit.model_copy(
        update={
            "channels": channels,
            "matched_fields": fields,
            "matched_occurrences": [_occurrence_payload(record) for record in occurrences],
            "evidence_refs": refs,
            "source_revision": max(
                [hit.source_revision, *(record.description_revision for record in occurrences)]
            ),
        }
    )


def _asset_allowed(
    asset: Asset,
    *,
    time_bounds: tuple[datetime, datetime] | None,
    media_types: tuple[MediaType, ...],
) -> bool:
    if media_types and asset.media_type not in media_types:
        return False
    return not (time_bounds is not None and within_time_bounds(time_bounds, asset.captured_at) is False)


@router.get(
    "/search",
    responses={409: {"model": DomainErrorResponse, "description": "有多位已确认人物匹配所填姓名或别名"}},
)
def search_assets(
    q: str = Query("", max_length=200, description="内容词，例如 桥 / 海边 / 红色"),
    # 空串与不传都表示**全部相册**（1.0 的 `if scope_id:`）。默认值从 "home-default"
    # 改成 "" 是行为修正：原来不传会去查一个叫 home-default 的空间，而它不存在。
    scope_id: str = "",
    limit: int = Query(20, ge=1, le=100),
    time_range: str = Query("", max_length=60, description="时间条件，例如 2023年10月 / 2025年-2026年"),
    person: list[str] = Query(default=[], description="人物条件，可重复：已确认人物的本名或别名"),
    person_id: list[str] = Query(default=[], description="显式人物 ID，可重复；未命名人物也可查"),
    description: str = Query("", max_length=200, description="人物描述，例如 灰色上衣"),
    place: str = Query("", max_length=60, description="地点条件，服务端按解析出的行政区/地名匹配"),
    media_type: str = Query("", max_length=20, description="媒体类型：image / video / audio / text"),
    container: Container = Depends(get_container),
) -> SearchResponse:
    try:
        media_types = (MediaType(media_type),) if media_type.strip() else ()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"未知的媒体类型: {media_type}") from error
    has_filter = any(
        (
            q.strip(),
            description.strip(),
            person,
            person_id,
            place.strip(),
            time_range.strip(),
            media_type.strip(),
        )
    )
    if not has_filter:
        raise UnprocessableError("至少需要一个检索条件")
    try:
        linked_assets = _linked_assets(container, person, person_id, scope_id)
    except AmbiguousPersonName as error:
        raise ConflictError("有多位已确认人物匹配这个姓名") from error

    time_bounds = parse_time_expression(time_range) if time_range.strip() else None
    occurrence_by_asset: dict[str, list[PersonOccurrenceRecord]] = {}
    if description.strip():
        occurrence_by_asset = _occurrence_matches(
            container,
            scope_id=scope_id,
            description=description,
            person_ids=person_id,
            limit=max(limit * 4, 50),
        )
        if linked_assets is not None:
            allowed = set(linked_assets)
            occurrence_by_asset = {
                asset_id: records for asset_id, records in occurrence_by_asset.items() if asset_id in allowed
            }

    result = EvidenceResult()
    if q.strip():
        result = build_retrieve_evidence(container).execute(
            EvidenceRequest(
                text=q,
                scope_id=scope_id or None,
                limit=limit,
                # 正向条件同时喂两处：**召回前筛**（metadata 通道用它决定要不要给信号）
                # 与**融合后排除**（conditions）。两者不是一回事，见 conditions.py 的模块注释。
                time_bounds=time_bounds,
                media_types=media_types,
                place=place.strip(),
                linked_person_assets=linked_assets,
                conditions=_conditions(time_range=time_range, place=place, media_type=media_type),
            )
        )

    items: list[SearchHit] = []
    positions: dict[str, int] = {}
    for hit in result.hits:
        asset = container.asset_repository.get(hit.asset_id)
        if asset is None:
            # 向量还在、资产没了（scope 被删等）。产品硬约束第 5 条要求查询返回
            # **可打开的**原图，所以只能丢弃——但丢弃这件事必须留痕。
            log.warning("检索命中了一个已不存在的资产，已丢弃: %s", hit.asset_id)
            continue
        positions[hit.asset_id] = len(items)
        items.append(
            SearchHit(
                asset=to_summary(asset),
                score=hit.score,
                channels=dict(hit.channels),
                raw_scores=dict(hit.raw_scores),
            )
        )

    # 描述通道是独立召回：只有词法落到了具体 face 才声明人物命中；图片语义分数不分摊。
    for rank, (asset_id, occurrences) in enumerate(occurrence_by_asset.items(), start=1):
        if linked_assets is not None and asset_id not in linked_assets:
            continue
        asset = container.asset_repository.get(asset_id)
        if asset is None or not _asset_allowed(asset, time_bounds=time_bounds, media_types=media_types):
            continue
        score = max((record.match_score for record in occurrences), default=0.0)
        if asset_id in positions:
            position = positions[asset_id]
            items[position] = _with_occurrences(items[position], occurrences, rank=rank)
            continue
        positions[asset_id] = len(items)
        items.append(
            _with_occurrences(
                SearchHit(
                    asset=to_summary(asset),
                    score=score,
                    channels={},
                    raw_scores={"person_occurrence": score},
                ),
                occurrences,
                rank=rank,
            )
        )

    # 只有显式人物条件、没有自由词/描述：按该人当前归属图片列举。
    if not q.strip() and not description.strip() and linked_assets:
        for rank, asset_id in enumerate(linked_assets, start=1):
            if asset_id in positions:
                continue
            asset = container.asset_repository.get(asset_id)
            if asset is None or not _asset_allowed(asset, time_bounds=time_bounds, media_types=media_types):
                continue
            positions[asset_id] = len(items)
            items.append(
                SearchHit(
                    asset=to_summary(asset),
                    score=0.0,
                    channels={"person": rank},
                    raw_scores={},
                )
            )

    items = items[:limit]
    return SearchResponse(
        query=q,
        backend=result.backend,
        degraded=result.degraded,
        degraded_reason=result.degraded_reason,
        authoritative_vectors=result.authoritative_vectors,
        excluded_count=result.excluded_count,
        channel_trace=[
            ChannelTrace(
                channel=trace.channel,
                status=trace.status,
                candidate_count=trace.candidate_count,
                latency_ms=trace.latency_ms,
                backend=trace.backend,
                reason=trace.reason,
            )
            for trace in result.channel_trace
        ],
        items=items,
    )

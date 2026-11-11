"""家庭图谱端点：关系图读取。

对应 1.0 ``app.py:1648`` 的 ``GET /api/family-graph``。

**读侧直连仓储，不建用例**——与 ``routes/identity.py`` 的实体目录同一体例：三步都是
「按 scope 取，再把记录搬进响应模型」，没有要落地的领域决定。真正带规则的
``family_graph_people``（1.0 那个函数就在 store 里）落在仓储层。

``run`` 在 **S7b2 · T313 补上了**（b1 时它不返回，因为那时没有任何东西会创建 run）。
它取 ``latest_run(scope_id)``——**入参那个 scope** 的最近一次，不是整张图的。

**``POST /family-graph/runs`` 是同步的。** 1.0 起线程后立刻返回 202；2.0 裁决成同步
（与相邻的 ``_maybe_run_insights`` 同体例），返回时那趟已经跑完，所以响应是**最终的
run**，没有 ``status`` 包装。

**``relationships`` 跨整张图。** 1.0 是一个对 ``scope_ids`` 的展平推导，所以合并过
scope 之后关系也跟着铺开——而 ``scope_id`` 那个键仍然只是入参那一个。照搬。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from sentrix.api.deps import get_container
from sentrix.api.errors import ConflictError, InvalidRequestError
from sentrix.api.schemas.family import (
    FamilyAnalysisRunPayload,
    FamilyGraphResponse,
    FamilyMembershipPayload,
    FamilyMembershipRequest,
    FamilyMembershipResponse,
    FamilyPersonPayload,
    FamilyPortraitPayload,
    FamilyPortraitRequest,
    FamilyPortraitResponse,
    FamilyRelationshipPayload,
    FamilyRelationshipRequest,
    FamilyRelationshipResponse,
    FamilyRepresentativeMedia,
    FamilyRetractRequest,
    FamilyRetractResponse,
    FamilyRunRequest,
    FamilyRunResponse,
    FamilyScopeMergeRequest,
    FamilyScopeMergeResponse,
)
from sentrix.application.identity.family_graph_service import FamilyGraphService
from sentrix.bootstrap.container import Container
from sentrix.domain.identity.family import (
    FamilyAnalysisRunRecord,
    FamilyMembershipRecord,
    FamilyPersonProjection,
    FamilyPortraitRecord,
    FamilyRelationshipRecord,
)

router = APIRouter(tags=["family"])

# 五个写路由**全部**写死的两个值（1.0 逐字）。
# ``confidence=1.0`` 是「用户说的，没有不确定」；``source`` 决定落库行的 ``locked``。
USER_SOURCE = "user_override"
USER_CONFIDENCE = 1.0

# 1.0 ``app.py:1715`` 的 409 判据——这两个状态下的 run 还在跑。
PENDING_RUN_STATUSES = ("queued", "running")

# 1.0 的 ``config`` 里那两个 trigger_type。手工重跑写 ``user_rerun``（``app.py:1719``）、
# 批末触发写 ``ingest``（``pipeline.py:750``）。
TRIGGER_USER = "user_rerun"
TRIGGER_INGEST = "ingest"
INPUT_MODE = "semantic_text_only"


def _optional_isoformat(value: datetime | None) -> str | None:
    """与 ``routes/identity.py`` / ``routes/relationships.py`` 的同名私有函数逐字相同。"""

    return value.isoformat() if value is not None else None


def _membership_payload(record: FamilyMembershipRecord) -> FamilyMembershipPayload:
    return FamilyMembershipPayload(
        id=record.id,
        scope_id=record.scope_id,
        entity_id=record.entity_id,
        membership=record.membership,
        source=record.source,
        confidence=record.confidence,
        evidence_refs=list(record.evidence_refs),
        inference_run_id=record.inference_run_id,
        state=record.state,
        locked=record.locked,
        revision=record.revision,
        supersedes_id=record.supersedes_id,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
    )


def _portrait_payload(record: FamilyPortraitRecord) -> FamilyPortraitPayload:
    return FamilyPortraitPayload(
        id=record.id,
        scope_id=record.scope_id,
        entity_id=record.entity_id,
        portrait_text=record.portrait_text,
        evidence_refs=list(record.evidence_refs),
        source=record.source,
        inference_run_id=record.inference_run_id,
        state=record.state,
        locked=record.locked,
        revision=record.revision,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
    )


def _relationship_payload(record: FamilyRelationshipRecord) -> FamilyRelationshipPayload:
    return FamilyRelationshipPayload(
        id=record.id,
        scope_id=record.scope_id,
        family_graph_id=record.family_graph_id,
        subject_entity_id=record.subject_entity_id,
        predicate=record.predicate,
        object_entity_id=record.object_entity_id,
        inverse_predicate=record.inverse_predicate,
        source=record.source,
        confidence=record.confidence,
        evidence_refs=list(record.evidence_refs),
        inference_run_id=record.inference_run_id,
        state=record.state,
        locked=record.locked,
        revision=record.revision,
        supersedes_id=record.supersedes_id,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
    )


def _person_payload(person: FamilyPersonProjection) -> FamilyPersonPayload:
    return FamilyPersonPayload(
        id=person.id,
        scope_id=person.scope_id,
        display_name=person.display_name,
        membership=(_membership_payload(person.membership) if person.membership is not None else None),
        portrait=_portrait_payload(person.portrait) if person.portrait is not None else None,
        avatar_face_instance_id=person.avatar_face_instance_id,
        appearance_count=person.appearance_count,
        representative_media=[
            FamilyRepresentativeMedia(
                id=str(item["id"]),
                media_type=str(item["media_type"]),
                file_name=str(item["file_name"]),
            )
            for item in person.representative_media
        ],
    )


@router.get("/family-graph")
def family_graph(
    scope_id: str,
    container: Container = Depends(get_container),
) -> FamilyGraphResponse:
    """一张家庭图谱。1.0 ``app.py:1648-1656``。

    ``scope_id`` 是**必填**（1.0 的签名里没有默认值），合并过 scope 时它会顺着
    ``family_scope_groups`` 展开成 ``scope_ids``。
    """
    repository = container.family_graph_repository
    scope_ids = repository.graph_scope_ids(scope_id)
    latest = repository.latest_run(scope_id)
    return FamilyGraphResponse(
        scope_id=scope_id,
        run=_run_payload(latest) if latest is not None else None,
        people=[_person_payload(item) for item in repository.family_graph_people(scope_ids)],
        scope_ids=list(scope_ids),
        relationships=[
            _relationship_payload(item)
            for item_scope_id in scope_ids
            for item in repository.list_effective_relationships(item_scope_id)
        ],
    )


# ---- 写侧（S7b1 · T306） ---------------------------------------------------------


def _required(message: str, *values: str) -> None:
    """五个写路由共用的必填校验：**任一项为空就 400，消息是 1.0 的原话**。

    1.0 是 ``if not scope_id or not membership:`` 这种裸判断，消息逐字写死——所以这里
    也逐字带出去，不做润色。
    """
    if not all(values):
        raise InvalidRequestError(message)


@router.patch("/family-graph/people/{person_id}/membership")
def update_family_membership(
    person_id: str,
    payload: FamilyMembershipRequest,
    container: Container = Depends(get_container),
) -> FamilyMembershipResponse:
    """改一个人的家庭归属。1.0 ``app.py:1660-1673``。

    ``source="user_override"`` 与 ``confidence=1.0`` 都是**写死的**：这是用户按下的动作，
    落库行 ``locked = 1``，此后模型的推断不许覆盖它。
    """
    scope_id = (payload.scope_id or "").strip()
    membership = (payload.membership or "").strip()
    _required("scope_id and membership are required", scope_id, membership)
    try:
        written = container.family_graph_repository.set_membership(
            scope_id,
            person_id,
            membership,
            source=USER_SOURCE,
            confidence=USER_CONFIDENCE,
            evidence_refs=payload.evidence_refs or (),
        )
    except ValueError as error:
        # **消息不改写**：1.0 是 ``detail=str(error)``，那几句话（unsupported family
        # membership / requires a person entity / …）是调用方用来区分失败的。
        raise InvalidRequestError(str(error)) from error
    return FamilyMembershipResponse(
        ok=True, membership=_membership_payload(written) if written is not None else None
    )


@router.put("/family-graph/relationships")
def update_family_relationship(
    payload: FamilyRelationshipRequest,
    container: Container = Depends(get_container),
) -> FamilyRelationshipResponse:
    """建一对亲属边。1.0 ``app.py:1676-1693``。

    这里**不判两个实体 id 是否给全**——1.0 只判 ``scope_id``，其余四个原样交给仓储，
    由它抛 ``family relationship requires both predicates`` 之类。少判一步的结果是
    错误消息不同，多判一步会挡掉仓储自己那几句话术。
    """
    scope_id = (payload.scope_id or "").strip()
    _required("scope_id is required", scope_id)
    try:
        written = container.family_graph_repository.set_relationship(
            scope_id,
            str(payload.subject_entity_id or ""),
            str(payload.predicate or ""),
            str(payload.object_entity_id or ""),
            str(payload.inverse_predicate or ""),
            source=USER_SOURCE,
            confidence=USER_CONFIDENCE,
            evidence_refs=payload.evidence_refs or (),
        )
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
    return FamilyRelationshipResponse(
        ok=True, relationships=[_relationship_payload(item) for item in written]
    )


@router.delete("/family-graph/relationships")
def retract_family_relationship(
    payload: FamilyRetractRequest,
    container: Container = Depends(get_container),
) -> FamilyRetractResponse:
    """撤回一对亲属边，**同时落一行 block 挡住模型恢复**。1.0 ``app.py:1696-1708``。"""
    scope_id = (payload.scope_id or "").strip()
    subject_entity_id = (payload.subject_entity_id or "").strip()
    object_entity_id = (payload.object_entity_id or "").strip()
    _required(
        "scope_id, subject_entity_id and object_entity_id are required",
        scope_id,
        subject_entity_id,
        object_entity_id,
    )
    try:
        written = container.family_graph_repository.retract_relationship(
            scope_id, subject_entity_id, object_entity_id
        )
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
    return FamilyRetractResponse(ok=True, retracted=[_relationship_payload(item) for item in written])


@router.patch("/family-graph/people/{person_id}/portrait")
def update_family_portrait(
    person_id: str,
    payload: FamilyPortraitRequest,
    container: Container = Depends(get_container),
) -> FamilyPortraitResponse:
    """手写一段家庭画像。1.0 ``app.py:1733-1740``。

    **1.0 这个端点没有 ``try/except``**（另外几个都有）——因为 ``write_family_portrait``
    不抛 ``ValueError``（它连 ``source`` 都不校验）。照搬：这里也不接。
    """
    scope_id = (payload.scope_id or "").strip()
    text = (payload.portrait_text or "").strip()
    _required("scope_id and portrait_text are required", scope_id, text)
    written = container.family_graph_repository.write_portrait(
        scope_id,
        person_id,
        text,
        source=USER_SOURCE,
        evidence_refs=payload.evidence_refs or (),
    )
    return FamilyPortraitResponse(
        ok=True, portrait=_portrait_payload(written) if written is not None else None
    )


@router.post("/family-graph/scopes/merge")
def merge_family_graph_scopes(
    payload: FamilyScopeMergeRequest,
    container: Container = Depends(get_container),
) -> FamilyScopeMergeResponse:
    """把若干 scope 并成一张图。1.0 ``app.py:1725-1730``。

    **响应没有 ``ok`` 键**：1.0 直接 ``return store.merge_family_scopes(...)``，
    而那个函数返回 ``{"graph_id", "scope_ids"}``。与另外四个写路由不一致，照搬。
    """
    try:
        graph_id, scope_ids = container.family_graph_repository.merge_scopes(payload.scope_ids or [])
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
    return FamilyScopeMergeResponse(graph_id=graph_id, scope_ids=list(scope_ids))


# ---- 推断运行（S7b2 · T313） ------------------------------------------------------


def _run_payload(record: FamilyAnalysisRunRecord) -> FamilyAnalysisRunPayload:
    return FamilyAnalysisRunPayload(
        id=record.id,
        scope_id=record.scope_id,
        status=record.status,
        current_stage=record.current_stage,
        config=dict(record.config),
        stats=dict(record.stats),
        error=record.error,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
        completed_at=_optional_isoformat(record.completed_at),
    )


def _family_graph_service(container: Container) -> FamilyGraphService:
    """推断服务的装配。**只有这里知道它要哪几个协作者。**

    ``model`` 传容器里那一个：没有配模型时它是 ``None``，服务会走那条「空结果、
    照样 completed」的降级路径（见 ``FamilyGraphService`` 的 docstring）。
    """
    return FamilyGraphService(
        family=container.family_graph_repository,
        events=container.event_repository,
        # 已确认人物的 family_role 用来补确定性的关系边（模型那条推断被证据闸挡住）。
        entities=container.entity_repository,
        model=container.chat_model,
    )


def run_family_graph_for_scope(
    container: Container, scope_id: str, *, trigger_type: str = TRIGGER_USER
) -> FamilyRunResponse | None:
    """跑一趟推断。**同步**——与 ``run_insights_for_scope`` 同体例。

    ``POST /family-graph/runs`` 与批末触发（T314）共用这一处，与
    ``api/routes/insights.py`` 把那两个入口收在一个模块级函数里是同一条理由。

    ``trigger_type`` 是**留痕不是判据**：1.0 手工路由写 ``user_rerun``、
    批末触发写 ``ingest``，两者都会出现在 run 的 ``config`` 里。所以这个参数
    不在服务里判，只在建 run 时如实记下。

    返回 ``None`` 表示「已经有 queued/running 的 run，没开新的」（1.0 的 409 分支）；
    其余情况返回那趟跑完之后的 run（``completed`` 或 ``failed``）。

    **回的是响应模型不是记录**：批末触发与手工路由都要把同一趟 run 放进响应，
    转换只做一次，省掉跨模块引一个私有函数。
    """
    repository = container.family_graph_repository
    latest = repository.latest_run(scope_id)
    # 1.0 ``app.py:1714-1716`` 的 409。同步模式下 ``queued`` 不会停留，所以这条实际
    # 挡的是「上一次崩在半路、状态还写着 running」的行。
    if latest is not None and latest.status in PENDING_RUN_STATUSES:
        return None
    run = repository.create_run(scope_id, {"trigger_type": trigger_type, "input_mode": INPUT_MODE})
    finished = _family_graph_service(container).run(run.id, scope_id)
    return FamilyRunResponse(run=_run_payload(finished)) if finished is not None else None


@router.post("/family-graph/runs")
def start_family_graph_run(
    payload: FamilyRunRequest,
    container: Container = Depends(get_container),
) -> FamilyRunResponse:
    """手工跑一趟家庭图谱推断。1.0 ``app.py:1711-1723``。

    **1.0 的前端从不调它**（``api.js:110`` 全仓只有定义），所以这是运维与调试入口——
    自动那条走批末触发（T314）。**照搬它，不为它编造消费场景。**
    """
    scope_id = (payload.scope_id or "").strip()
    _required("scope_id is required", scope_id)
    run = run_family_graph_for_scope(container, scope_id)
    if run is None:
        raise ConflictError("family analysis run already in progress")
    return run

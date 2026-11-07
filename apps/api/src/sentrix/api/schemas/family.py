"""家庭图谱端点的传输模型（S7b1 · T305）。

字段与 1.0 ``app.py:1648-1656`` 那个响应逐一对齐：``membership`` / ``portrait`` /
``relationships`` 三处 1.0 直接返回**整行**（不像别处会裁），这里也照给全列。

``run`` 在 **S7b2 · T313 补上了**（b1 时它不返回，因为那时没有任何东西会创建 run）。
它是 ``latest_run(scope_id)``——**入参那个 scope 的**最近一次，不是整张图的。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FamilyMembershipPayload(BaseModel):
    """``family_person_memberships`` 一行。"""

    id: str
    scope_id: str
    entity_id: str
    membership: str
    source: str
    confidence: float
    evidence_refs: list[str]
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    supersedes_id: str | None
    created_at: str | None
    updated_at: str | None


class FamilyPortraitPayload(BaseModel):
    """``family_person_portraits`` 一行。**与人物画像那套（``PersonPortrait*``）不是一张表。**"""

    id: str
    scope_id: str
    entity_id: str
    portrait_text: str
    evidence_refs: list[str]
    source: str
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    created_at: str | None
    updated_at: str | None


class FamilyRelationshipPayload(BaseModel):
    """``family_relationships`` 一行。库里一条边是**两行**，所以这里也是两条。"""

    id: str
    scope_id: str
    family_graph_id: str | None
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    inverse_predicate: str
    source: str
    confidence: float
    evidence_refs: list[str]
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    supersedes_id: str | None
    created_at: str | None
    updated_at: str | None


class FamilyRepresentativeMedia(BaseModel):
    """``representative_media`` 的一项。**三个键**，1.0 ``db.py:5662-5669`` 逐字。

    这是「**这个人的脸出现在哪些照片**」，不是「这个实体最新的观察」——两条查询的
    连接路径都不同（``face_instances⋈face_clusters⋈assets`` 对
    ``entity_observations⋈observations⋈assets``）。
    """

    id: str
    media_type: str
    file_name: str


class FamilyPersonPayload(BaseModel):
    """``people[]`` 的一项。``display_name`` 的占位统一为 **「未确认姓名人物」**，
    任何端点都不把落库的内部 cluster id 带给用户。"""

    id: str
    scope_id: str
    display_name: str
    membership: FamilyMembershipPayload | None
    portrait: FamilyPortraitPayload | None
    avatar_face_instance_id: str | None
    appearance_count: int
    representative_media: list[FamilyRepresentativeMedia]


class FamilyGraphResponse(BaseModel):
    """``GET /family-graph`` 的响应。

    **``relationships`` 是跨整张图的**：1.0 ``app.py:1655`` 是一个对 ``scope_ids``
    的展平推导——``people`` 走合并后的全部 scope，关系也一样。这条容易看漏，
    因为 ``scope_id`` 那个键仍然只是入参那一个。
    """

    scope_id: str
    run: FamilyAnalysisRunPayload | None
    people: list[FamilyPersonPayload]
    scope_ids: list[str]
    relationships: list[FamilyRelationshipPayload]


# ---- 写侧（S7b1 · T306） ---------------------------------------------------------
#
# 五个写操作的请求体字段**全部可省**：1.0 是裸 ``dict`` 收的，用 ``str(... or "").strip()``
# 取值再判空，所以「给了 null」「给空串」「没给」在 1.0 里**走的是同一个 400 分支**。
# 写成必填字段会把那三种都变成 422 加 pydantic 的话术——同一个产品行为换一个码。
# 与 ``RelationshipCreateRequest`` / ``EntityPropertyRequest`` 是同一条理由。


class FamilyMembershipRequest(BaseModel):
    """``PATCH /family-graph/people/{id}/membership`` 的请求体。"""

    scope_id: str | None = None
    membership: str | None = None
    evidence_refs: list[str] | None = None


class FamilyMembershipResponse(BaseModel):
    """1.0 的 ``{"ok": True, "membership": <整行>}``。"""

    ok: bool
    membership: FamilyMembershipPayload | None


class FamilyRelationshipRequest(BaseModel):
    """``PUT /family-graph/relationships`` 的请求体。"""

    scope_id: str | None = None
    subject_entity_id: str | None = None
    predicate: str | None = None
    object_entity_id: str | None = None
    inverse_predicate: str | None = None
    evidence_refs: list[str] | None = None


class FamilyRelationshipResponse(BaseModel):
    """``{"ok": True, "relationships": [正行, 反行]}``——**一次落两行**，所以是列表。"""

    ok: bool
    relationships: list[FamilyRelationshipPayload]


class FamilyRetractRequest(BaseModel):
    """``DELETE /family-graph/relationships`` 的请求体。

    **``DELETE`` 带 body 是 1.0 原样**（两个实体 id 塞不进路径），FastAPI 支持。
    """

    scope_id: str | None = None
    subject_entity_id: str | None = None
    object_entity_id: str | None = None


class FamilyRetractResponse(BaseModel):
    """``{"ok": True, "retracted": [...]}``——返回的是**撤回之后**的行（``state='retracted'``）。"""

    ok: bool
    retracted: list[FamilyRelationshipPayload]


class FamilyPortraitRequest(BaseModel):
    """``PATCH /family-graph/people/{id}/portrait`` 的请求体。"""

    scope_id: str | None = None
    portrait_text: str | None = None
    evidence_refs: list[str] | None = None


class FamilyPortraitResponse(BaseModel):
    ok: bool
    portrait: FamilyPortraitPayload | None


class FamilyScopeMergeRequest(BaseModel):
    """``POST /family-graph/scopes/merge`` 的请求体。"""

    scope_ids: list[str] | None = None


class FamilyScopeMergeResponse(BaseModel):
    """**没有 ``ok`` 键**——1.0 这个端点直接返回仓储那个 dict。

    与另外四个写路由的 ``{"ok": True, ...}`` 不一致，照搬：统一它会让这个端点的响应
    多一个 1.0 没有的键。
    """

    graph_id: str
    scope_ids: list[str]


# ---- 推断运行（S7b2 · T313） ------------------------------------------------------


class FamilyAnalysisRunPayload(BaseModel):
    """``family_analysis_runs`` 一行。**``stats`` 里的 ``event_watermark`` 是触发去抖
    的唯一依据**——完成时写入当时的事件数，下次触发时事件数没涨就不跑。"""

    id: str
    scope_id: str
    status: str
    current_stage: str
    config: dict[str, Any]
    stats: dict[str, Any]
    error: str | None
    created_at: str | None
    updated_at: str | None
    completed_at: str | None


class FamilyRunRequest(BaseModel):
    """``POST /family-graph/runs`` 的请求体。字段可省的理由同写路由那一组。"""

    scope_id: str | None = None


class FamilyRunResponse(BaseModel):
    """``POST /family-graph/runs`` 的响应。

    **与 1.0 的 ``{"status": 202, "run": ...}`` 不同**：1.0 起线程后立刻返回，
    202「已受理」是对的；2.0 裁决成**同步**（与相邻的 ``_maybe_run_insights`` 同体例），
    返回时那趟已经跑完，再报 202 就是假话。所以去掉 ``status`` 包装、直接给最终的 run。
    读 ``response.run`` 的调用方两边都能用；读 ``response.status == 202`` 的调用方
    在 2.0 本来也会因为「同步」而拿不到它想要的东西。
    """

    run: FamilyAnalysisRunPayload

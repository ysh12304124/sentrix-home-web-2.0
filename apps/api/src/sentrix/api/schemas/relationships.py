"""关系端点的传输模型。字段与仓储的记录、与批量导入的结论一一对应。

**批量结果那一项的 ``id`` / ``error`` 是「二选一」。** 1.0 逐条构造的 dict 成功时只有
``id`` 键、失败时只有 ``error`` 键——**键的缺席**。2.0 用 ``None`` 表达，理由与
``EntitySummaryPayload`` 的 ``single_sample`` / ``private`` 相同：全仓没有
``exclude_none`` 的先例，固定响应模型下「缺席」只能靠 ``null``。
"""

from __future__ import annotations

from pydantic import BaseModel


class RelationshipPayload(BaseModel):
    """``relationships`` 一行 + 两端实体的名字。

    ``subject_name`` / ``object_name`` 是仓储 ``JOIN entities`` 带出来的，不在这里再查。

    **写侧三个端点这两个字段是空串。** 1.0 的 ``POST`` / ``retract`` 返回的那条 SQL
    没有 JOIN（``retract`` 甚至没解码 ``evidence_ids_json``），只有 ``confirm`` 有——
    三处不一样是 1.0 的副作用，而它的前端哪个都不读（理由写在
    ``api/routes/relationships.py`` 的模块 docstring 里）。2.0 统一成一个形状。
    """

    id: str
    scope_id: str
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    inverse_predicate: str | None
    status: str
    confidence: float
    evidence_ids: list[str]
    revision: int
    created_at: str | None
    updated_at: str | None
    subject_name: str
    object_name: str


class RelationshipNode(BaseModel):
    """关系图里的一个点。1.0 ``app.py:2104`` 的四个键，逐字。"""

    id: str
    label: str
    status: str
    entity_type: str


class RelationshipEdge(BaseModel):
    """关系图里的一条边。1.0 ``app.py:2105`` 把关系行的五个字段**改了名**——
    ``subject_entity_id`` → ``source``、``object_entity_id`` → ``target``、
    ``predicate`` → ``label``。图渲染库要的是后一组名字。"""

    source: str
    target: str
    label: str
    status: str
    id: str


class RelationshipGraphResponse(BaseModel):
    """``GET /api/relationships`` 的响应。外层三个键，1.0 ``app.py:2106``。"""

    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
    relationships: list[RelationshipPayload]


class RelationshipCreateRequest(BaseModel):
    """``POST /api/relationships`` 的请求体。

    **三个 id / 谓词都可省，不是笔误。** 1.0 的路由用 ``payload.get()`` 取值再
    ``if not all(...)`` 判，所以「缺一个」要给 400 加那句具体的话，而不是 pydantic 的
    422。写成必填字段就把同一个产品行为换成了一个码——与 ``EntityPropertyRequest``
    是同一条理由。

    ``status`` 的「缺省」与「显式 null」在这里**不是一回事**：1.0 是
    ``payload.get("status", "pending")``，键缺席才用默认值，显式 ``null`` 会把 ``None``
    一路传进 INSERT。路由按 ``model_fields_set`` 区分，见那里的注释。
    """

    subject_entity_id: str | None = None
    predicate: str | None = None
    object_entity_id: str | None = None
    evidence_ids: list[str] | None = None
    confidence: float | None = None
    status: str | None = None


class RelationshipBatchItem(BaseModel):
    """manifest 里的一条。字段可省，省钱的理由同上。"""

    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    confidence: float | None = None


class RelationshipBatchRequest(BaseModel):
    """``POST /api/relationships/batch`` 的请求体。

    ``entity_by_name`` 是 PhotoBench 上一步 ``/api/people/seed-batch`` 换出来的
    ``{名字: entity_id}``；没有它也能跑，那时全靠用例里的名字索引。
    """

    scope_id: str | None = None
    graph_enabled: bool = True
    entity_by_name: dict[str, str] | None = None
    relationships: list[RelationshipBatchItem] | None = None


class RelationshipBatchResultItem(BaseModel):
    """逐条结论。``id`` 与 ``error`` 二选一，见模块 docstring。"""

    subject: str
    predicate: str
    object: str
    id: str | None = None
    error: str | None = None


class RelationshipBatchResponse(BaseModel):
    """``requested`` 是**请求条数**（不是解析成功的条数），``imported`` 是没有 error 的
    那些——1.0 ``app.py:2165-2166``。"""

    requested: int
    imported: int
    results: list[RelationshipBatchResultItem]

"""关系端点的契约测试（S7a · T203）。

对应 1.0 ``app.py:2098-2182`` 的五条路由。只跑 HTTP，不碰模型。

这里钉的是三组**容易被「统一」掉**的东西：

- ``GET`` 两条分支的**状态口径相反**：``kind=person`` 排 ``retracted``，
  不带 ``kind`` 的那支连 ``retracted`` 都给（1.0 是两条 SQL，``db.py:5601`` 对 ``:5590``）；
- ``POST`` 的 400 是**路由判的**，消息是 1.0 的原话——写成 pydantic 必填会变成 422，
  同一个产品行为换一个码；
- 批量导入的三条照搬：名字/角色**双索引**、逐条失败不让整批失败、**恒 pending**。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

NOW = "2026-09-23T10:00:00+00:00"

HOME = "album1"

REQUIRED_MESSAGE = "subject_entity_id, predicate and object_entity_id are required"
UNRESOLVED = "unresolved entity"


def _db(client: TestClient):
    return client.app.state.container.database


def _add_relationship(
    client: TestClient,
    relationship_id: str,
    subject: str,
    obj: str,
    *,
    predicate: str = "出现在",
    status: str = "pending",
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO relationships (id, scope_id, subject_entity_id, predicate,"
            " object_entity_id, status, confidence, evidence_ids_json, revision,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 0.5, '[]', 1, ?, ?)",
            (relationship_id, HOME, subject, predicate, obj, status, NOW, NOW),
        )


def _ids(client: TestClient, **params: str) -> set[str]:
    body = client.get("/api/v1/relationships", params=params).json()
    return {item["id"] for item in body["relationships"]}


@pytest.fixture
def relationship_client(client: TestClient) -> TestClient:
    """一个空间 + 三个实体：两个人物（其中一个带 ``family_role``）和一个地点。

    ``family_role`` 是给批量导入的**角色索引**用的——那里 ``canonical_name`` 与
    ``family_role`` 都能解析名字。
    """
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (HOME, HOME, NOW, NOW),
        )
        for entity_id, entity_type, name, role in (
            ("ent_mom", "person", "妈妈", "母亲"),
            ("ent_dad", "person", "爸爸", None),
            ("ent_west", "place", "西湖", None),
        ):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " family_role, summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'confirmed', ?, '', 0.0, ?, ?)",
                (entity_id, HOME, entity_type, name, role, NOW, NOW),
            )
    return client


# ---- 关系图 ----------------------------------------------------------------------


def test_the_graph_without_kind_shows_every_entity_and_every_edge(relationship_client):
    """不带 ``kind``：**不过滤 status**，连 ``retracted`` 的边都给。

    与 ``kind=person`` 那支相反（见下一条）。1.0 是两条 SQL，口径本来就不一样，
    统一了必有一边错。
    """
    _add_relationship(relationship_client, "rel_live", "ent_mom", "ent_west", status="active")
    _add_relationship(relationship_client, "rel_gone", "ent_dad", "ent_west", status="retracted")

    body = relationship_client.get("/api/v1/relationships").json()

    assert {item["id"] for item in body["relationships"]} == {"rel_live", "rel_gone"}
    # 点也不分类型——不带的这支给全部实体。
    assert {node["id"] for node in body["nodes"]} == {"ent_mom", "ent_dad", "ent_west"}


def test_an_edge_renames_three_of_the_relationship_columns(relationship_client):
    """边把关系行的字段**改了名**：``subject_entity_id`` → ``source``、
    ``object_entity_id`` → ``target``、``predicate`` → ``label``（1.0 ``app.py:2105``）。
    图渲染库要的是后一组名字，两个键都得在。
    """
    _add_relationship(relationship_client, "rel_live", "ent_mom", "ent_west")

    edge = relationship_client.get("/api/v1/relationships").json()["edges"][0]

    assert (edge["source"], edge["target"], edge["label"]) == ("ent_mom", "ent_west", "出现在")
    assert edge["id"] == "rel_live"


def test_the_person_graph_only_shows_people_and_drops_retracted(relationship_client):
    """``kind=person``：**两端都必须是人物**，且排掉 ``retracted``（1.0 ``db.py:5601``）。

    与被撤回的那条一起钉住口径：``rel_gone`` 两端都是人物，但它是 retracted，所以不在。
    """
    _add_relationship(relationship_client, "rel_people", "ent_mom", "ent_dad", status="active")
    _add_relationship(relationship_client, "rel_place", "ent_mom", "ent_west", status="active")
    _add_relationship(relationship_client, "rel_gone", "ent_mom", "ent_dad", status="retracted")

    body = relationship_client.get("/api/v1/relationships", params={"kind": "person"}).json()

    assert {item["id"] for item in body["relationships"]} == {"rel_people"}
    assert {node["id"] for node in body["nodes"]} == {"ent_mom", "ent_dad"}


# ---- 建关系 ----------------------------------------------------------------------


def test_a_pending_person_can_be_an_endpoint(relationship_client):
    """S3：未命名人物也能在人物页维护关系；pending 不是关系的排除条件。"""
    with _db(relationship_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
            " summary, confidence, created_at, updated_at)"
            " VALUES ('ent_stranger', ?, 'person', '待确认人物簇 · cluster-x', 'pending',"
            " '', 0.0, ?, ?)",
            (HOME, NOW, NOW),
        )

    response = relationship_client.post(
        "/api/v1/relationships",
        json={
            "subject_entity_id": "ent_stranger",
            "predicate": "朋友",
            "object_entity_id": "ent_mom",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    body = relationship_client.get("/api/v1/relationships", params={"kind": "person"}).json()
    assert "ent_stranger" in {node["id"] for node in body["nodes"]}


def test_creating_without_a_required_field_is_a_400_with_the_literal_message(relationship_client):
    """1.0 ``app.py:2113-2114`` 的原话，逐字。

    定点变异：把 ``subject_entity_id`` 在 ``RelationshipCreateRequest`` 里写成必填，
    这条会变成 422——那正是它要挡的「同一个产品行为换一个码」。
    """
    response = relationship_client.post("/api/v1/relationships", json={"predicate": "出现在"})

    assert response.status_code == 400
    assert response.json()["message"] == REQUIRED_MESSAGE


def test_creating_defaults_to_pending_half_confidence_and_no_evidence(relationship_client):
    """1.0 ``app.py:2116`` 的三个默认值 + 两端名字由仓储 JOIN 带出。"""
    body = relationship_client.post(
        "/api/v1/relationships",
        json={
            "subject_entity_id": "ent_mom",
            "predicate": "出现在",
            "object_entity_id": "ent_west",
        },
    ).json()

    assert body["status"] == "pending"
    assert body["confidence"] == 0.5
    assert body["evidence_ids"] == []
    assert body["revision"] == 1
    assert body["subject_entity_id"] == "ent_mom"
    assert body["object_entity_id"] == "ent_west"


def test_creating_the_same_triple_twice_merges_instead_of_adding_a_row(relationship_client):
    """同三元组去重在仓储里：证据并集、``MAX(confidence)``、``revision + 1``、
    **id 不变**（1.0 ``db.py:5978-5990``）。
    """
    payload = {
        "subject_entity_id": "ent_mom",
        "predicate": "出现在",
        "object_entity_id": "ent_west",
    }
    first = relationship_client.post("/api/v1/relationships", json=payload).json()
    second = relationship_client.post(
        "/api/v1/relationships",
        json={**payload, "evidence_ids": ["obs_1"], "confidence": 0.9},
    ).json()

    assert second["id"] == first["id"]
    assert second["revision"] == 2
    assert second["confidence"] == 0.9
    assert second["evidence_ids"] == ["obs_1"]
    assert len(relationship_client.get("/api/v1/relationships").json()["relationships"]) == 1


def test_the_three_write_endpoints_all_answer_with_the_same_shape(relationship_client):
    """**写侧三个端点回同一个形状，名字是空的**——这是 2.0 的一处**有意统一**。

    1.0 三处的返回投影不一样，而且是三条 SQL 的副作用不是设计：``POST`` 没有 JOIN
    （``db.py:5998``）、``confirm`` 走 ``list_relationships()`` 有 JOIN（``:6026``）、
    ``retract`` 回**未解码**的原始行（``:5615``，``evidence_ids_json`` 是字符串）。
    而 1.0 的前端一个都不读（``app.js:1831`` 的 ``created`` 赋值完就再没用过）。

    定点变异：给 ``create`` 或 ``retract`` 加一次「回读带名字」的查询，这条变红——
    那正是它要挡的「顺手把 1.0 没有的投影补上」。
    """
    created = relationship_client.post(
        "/api/v1/relationships",
        json={
            "subject_entity_id": "ent_mom",
            "predicate": "出现在",
            "object_entity_id": "ent_west",
        },
    ).json()
    confirmed = relationship_client.post(f"/api/v1/relationships/{created['id']}/confirm").json()
    retracted = relationship_client.post(f"/api/v1/relationships/{created['id']}/retract").json()

    for body in (created, confirmed, retracted):
        assert body["subject_name"] == ""
        assert body["object_name"] == ""
        assert isinstance(body["evidence_ids"], list)
    assert (created["status"], confirmed["status"], retracted["status"]) == (
        "pending",
        "active",
        "retracted",
    )


# ---- 确认 / 撤回 ------------------------------------------------------------------


def test_confirming_an_unknown_relationship_is_a_404(relationship_client):
    response = relationship_client.post("/api/v1/relationships/rel_nope/confirm")

    assert response.status_code == 404
    assert response.json()["message"] == "relationship not found"


def test_confirming_turns_a_pending_relationship_active(relationship_client):
    _add_relationship(relationship_client, "rel_1", "ent_mom", "ent_west", status="pending")

    body = relationship_client.post("/api/v1/relationships/rel_1/confirm").json()

    assert body["status"] == "active"


def test_retracting_keeps_the_row_and_bumps_the_revision(relationship_client):
    """1.0 ``db.py:5609``：``status='retracted'`` + ``revision + 1``，**不删行**。"""
    _add_relationship(relationship_client, "rel_1", "ent_mom", "ent_west")

    body = relationship_client.post("/api/v1/relationships/rel_1/retract").json()

    assert body["status"] == "retracted"
    assert body["revision"] == 2
    # 不带 kind 的图里仍然看得到它——就是上面那条「不过滤 status」。
    assert "rel_1" in _ids(relationship_client)


# ---- 批量导入 --------------------------------------------------------------------


def test_batch_resolves_by_name_and_also_by_family_role(relationship_client):
    """1.0 ``app.py:2126-2137``：``canonical_name`` 与 ``family_role`` **都进索引**。

    「妈妈」是名字，「母亲」是她的 ``family_role``——两条都该解析到 ``ent_mom``。
    这条别名规则在 1.0 里没有任何提示，也没有第二个地方这么用 ``family_role``。
    """
    body = relationship_client.post(
        "/api/v1/relationships/batch",
        json={
            "scope_id": HOME,
            "relationships": [
                {"subject": "妈妈", "predicate": "出现在", "object": "西湖"},
                {"subject": "母亲", "predicate": "记录于", "object": "西湖"},
            ],
        },
    ).json()

    assert body["requested"] == 2
    assert body["imported"] == 2
    assert all(item["id"] for item in body["results"])
    assert _ids(relationship_client) == {item["id"] for item in body["results"]}


def test_batch_records_the_unresolved_ones_and_keeps_going(relationship_client):
    """解析不到、或谓词为空的那条**不让整批失败**：逐条记 ``"unresolved entity"``。

    1.0 ``app.py:2147-2150`` 的三个条件是 ``not subject_id or not object_id or not
    predicate``——**谓词为空走的是同一个分支**，报的也是同一句话。
    """
    body = relationship_client.post(
        "/api/v1/relationships/batch",
        json={
            "scope_id": HOME,
            "relationships": [
                {"subject": "妈妈", "predicate": "出现在", "object": "西湖"},
                {"subject": "查无此人", "predicate": "出现在", "object": "西湖"},
                {"subject": "妈妈", "predicate": "", "object": "西湖"},
            ],
        },
    ).json()

    assert body["requested"] == 3
    assert body["imported"] == 1
    assert [item["error"] for item in body["results"]] == [None, UNRESOLVED, UNRESOLVED]


def test_batch_prefers_the_caller_supplied_id_map_over_the_name_index(relationship_client):
    """``entity_by_name`` **优先于**名字索引（1.0 ``app.py:2142-2143`` 的顺序）。

    传一个把「西湖」指向 ``ent_dad`` 的映射：落库的边必须指向 ``ent_dad``，而不是
    名字索引给的 ``ent_west``。调换那两行的顺序就会红。
    """
    body = relationship_client.post(
        "/api/v1/relationships/batch",
        json={
            "scope_id": HOME,
            "entity_by_name": {"西湖": "ent_dad"},
            "relationships": [{"subject": "妈妈", "predicate": "出现在", "object": "西湖"}],
        },
    ).json()

    assert body["imported"] == 1
    edges = relationship_client.get("/api/v1/relationships").json()["relationships"]
    assert [(item["subject_entity_id"], item["object_entity_id"]) for item in edges] == [
        ("ent_mom", "ent_dad")
    ]


def test_batch_always_imports_as_pending_without_evidence(relationship_client):
    """1.0 ``app.py:2153-2154`` 硬编码：``evidence_ids=[]``、``status="pending"``；
    ``confidence`` 走 ``float(... or 0.5)``——**传 ``0`` 也变成 ``0.5``**。
    """
    relationship_client.post(
        "/api/v1/relationships/batch",
        json={
            "scope_id": HOME,
            "relationships": [
                {"subject": "妈妈", "predicate": "出现在", "object": "西湖", "confidence": 0},
            ],
        },
    )

    edge = relationship_client.get("/api/v1/relationships").json()["relationships"][0]
    assert edge["status"] == "pending"
    assert edge["evidence_ids"] == []
    assert edge["confidence"] == 0.5


# ---- 确认时「写入长期记忆」（S7a · T206） -----------------------------------------


def _create(client: TestClient, *, status: str, predicate: str = "出现在", obj: str = "ent_west"):
    return client.post(
        "/api/v1/relationships",
        json={
            "subject_entity_id": "ent_mom",
            "predicate": predicate,
            "object_entity_id": obj,
            "status": status,
        },
    )


def _claims(client: TestClient) -> list[dict]:
    return client.get("/api/v1/knowledge").json()["claims"]


def test_an_active_relationship_writes_a_semantic_claim(relationship_client):
    """``status="active"`` 时写一条 claim——1.0 那句「已保存家庭关系：X，**并写入长期记忆**」。

    1.0 前端三处建关系传的都是 ``active``（``app.js:1732`` / ``:1765`` / ``:1831``），
    所以这是主路径不是边角。claim 挂在 **subject** 上，``value_text`` 是**对方的名字**。
    """
    _create(relationship_client, status="active")

    claims = _claims(relationship_client)
    assert len(claims) == 1
    assert claims[0]["person_id"] == "ent_mom"
    assert claims[0]["dimension"] == "relationship"
    assert claims[0]["predicate"] == "出现在"
    assert claims[0]["value_text"] == "西湖"
    assert claims[0]["status"] == "active"
    assert claims[0]["confidence_source"] == "user-confirmed"


def test_the_relationship_evidence_lands_on_the_claim(relationship_client):
    """``evidence_ids`` 从关系行带到 claim（1.0 ``db.py:5958``）。"""
    relationship_client.post(
        "/api/v1/relationships",
        json={
            "subject_entity_id": "ent_mom",
            "predicate": "出现在",
            "object_entity_id": "ent_west",
            "status": "active",
            "evidence_ids": ["obs_1", "obs_2"],
        },
    )

    assert _claims(relationship_client)[0]["evidence_ids"] == ["obs_1", "obs_2"]


def test_a_pending_relationship_writes_no_claim(relationship_client):
    """``pending`` 不写。**这是对照臂**：没有它，把 ``status == 'active'`` 那个条件
    去掉不会有任何东西报错——只是表里多了几行。
    """
    _create(relationship_client, status="pending")

    assert _claims(relationship_client) == []


def test_confirming_a_pending_relationship_writes_no_claim(relationship_client):
    """**``confirm`` 不写 claim。** 1.0 ``db.py:6014`` 里没有那一步，只有
    ``create_relationship(status='active')`` 有（``:6002``）。

    这条反直觉：用户点「确认」把一条待确认关系变成 active，**长期记忆里什么都不会多**。
    照搬——顺手「补上」会产出 1.0 没有的 claim，所以它必须有用例钉住。
    """
    created = _create(relationship_client, status="pending").json()
    relationship_client.post(f"/api/v1/relationships/{created['id']}/confirm")

    assert _claims(relationship_client) == []


def test_the_claim_predicate_goes_through_the_alias_table(relationship_client):
    """1.0 ``db.py:2655-2659`` 的九条谓词别名：``位于`` → ``出现在``。

    别名表是**改写**不是白名单——没命中的谓词原样落库。
    """
    _create(relationship_client, status="active", predicate="位于")

    assert [item["predicate"] for item in _claims(relationship_client)] == ["出现在"]


def test_the_claim_keeps_an_unknown_predicate_verbatim(relationship_client):
    """别名表没命中的原样落库（1.0 那个 ``.get(key, key)`` 的兜底）。

    与上一条成对：只测命中那半边的话，把整张表换成「凡是谓词都改写成出现在」也照样绿。
    """
    _create(relationship_client, status="active", predicate="妻子")

    assert [item["predicate"] for item in _claims(relationship_client)] == ["妻子"]


def test_a_second_active_relationship_on_the_same_predicate_supersedes_the_first(
    relationship_client,
):
    """``relationship`` **不在多值维度里**（1.0 ``db.py:2689`` 的集合只有
    activity / place / clothing / capture / attendance），所以同人同谓词的第二条 claim
    落 ``pending``、记下 ``supersedes_claim_id``、``revision`` 取旧值 + 1。

    把 ``relationship`` 混进多值维度的话这里会变成两条 active——而「一个人不能在同一个
    谓词上同时有两个值」正是 1.0 用 pending 表达的东西。
    """
    _create(relationship_client, status="active", obj="ent_west")
    _create(relationship_client, status="active", obj="ent_dad")

    claims = {item["value_text"]: item for item in _claims(relationship_client)}
    assert set(claims) == {"西湖", "爸爸"}
    assert claims["西湖"]["status"] == "active"
    assert claims["西湖"]["revision"] == 1
    assert claims["爸爸"]["status"] == "pending"
    assert claims["爸爸"]["supersedes_claim_id"] == claims["西湖"]["id"]
    assert claims["爸爸"]["revision"] == 2


def test_a_same_named_person_merges_into_the_same_claim_and_keeps_the_higher_confidence(
    relationship_client,
):
    """claim 的合并键用**对方的名字**（``value_text``）不是对方的 id，所以两个**同名**
    实体会并到同一条 claim 上；``confidence`` 取 ``MAX``（1.0 ``db.py:2678``）。

    这条钉两件在别处看不到的事：

    - **合并键是名字**：两次写入落到**一行** claim；
    - **MAX 的方向**：后写的那次置信度**更低**时结果仍是高的那个。

    定点变异：把 ``MAX(confidence, ?)`` 换成 ``?``，这条变红（结果变成 0.4）——而
    「同一条关系写两遍」那种夹具是杀不掉它的（关系行自己的 ``confidence`` 已经单调，
    喂进来的值只增不减）。
    """
    with _db(relationship_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
            " family_role, summary, confidence, created_at, updated_at)"
            " VALUES ('ent_west_2', ?, 'place', '西湖', 'confirmed', NULL, '', 0.0, ?, ?)",
            (HOME, NOW, NOW),
        )
    payload = {"subject_entity_id": "ent_mom", "predicate": "出现在", "status": "active"}
    relationship_client.post(
        "/api/v1/relationships",
        json={**payload, "object_entity_id": "ent_west", "confidence": 0.9},
    )
    relationship_client.post(
        "/api/v1/relationships",
        json={**payload, "object_entity_id": "ent_west_2", "confidence": 0.4},
    )

    claims = _claims(relationship_client)
    assert len(claims) == 1
    assert claims[0]["confidence"] == 0.9


def test_the_same_triple_twice_merges_the_claim_instead_of_adding_one(relationship_client):
    """同键的二次写入**并掉**：证据并集、``confidence`` 取 ``MAX``，claim 仍只有一条。

    第二次建的还是同一条关系（``create`` 会合并），返回的 ``status`` 仍是 active，
    所以 claim 会被再写一遍——那一次必须并到同一行上（1.0 ``db.py:2675-2685``）。
    """
    payload = {
        "subject_entity_id": "ent_mom",
        "predicate": "出现在",
        "object_entity_id": "ent_west",
        "status": "active",
        "confidence": 0.4,
    }
    relationship_client.post("/api/v1/relationships", json=payload)
    relationship_client.post(
        "/api/v1/relationships", json={**payload, "confidence": 0.9, "evidence_ids": ["obs_1"]}
    )

    claims = _claims(relationship_client)
    assert len(claims) == 1
    assert claims[0]["confidence"] == 0.9
    assert claims[0]["evidence_ids"] == ["obs_1"]

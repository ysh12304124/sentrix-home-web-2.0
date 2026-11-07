"""家庭图谱端点的契约测试（S7b1 · T305）。

对应 1.0 ``app.py:1648-1656`` 的 ``GET /api/family-graph``。

**这里钉的核心是 ``family_graph_people`` 的三个派生字段与 ``EntityRepository.decorate_entities``
不一样**。它们长得像（都是「头像 / 照片数 / 代表照」），但三处的连接路径或排序都不同，
「看起来能复用」正是最危险的地方：

- ``representative_media`` 走**人脸**（``face_instances⋈face_clusters⋈assets``），
  ``preview_*`` 走**观察**（``entity_observations⋈observations⋈assets``）；
- ``appearance_count`` 数**人脸实例行数**，``photo_count`` 数 ``COUNT(DISTINCT a.id)``；
- 头像的排序键多一位 ``fi.quality DESC``。

所以每条都造一个「两种口径给出不同答案」的夹具。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

NOW = "2026-10-02T10:00:00+00:00"

HOME = "album1"
OTHER = "album2"


def _db(client: TestClient):
    return client.app.state.container.database


@pytest.fixture
def family_client(client: TestClient) -> TestClient:
    """两个空间 · 两个人物（一个 pending）· 一个地点 · 两个资产 · 两条观察。

    外键是开的，父数据按顺序落。
    """
    with _db(client).connect() as connection, connection:
        for scope_id in (HOME, OTHER):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                (scope_id, scope_id, NOW, NOW),
            )
        for asset_id, scope_id in (("asset_1", HOME), ("asset_2", HOME), ("asset_3", OTHER)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for observation_id, scope_id, asset_id in (
            ("obs_1", HOME, "asset_1"),
            ("obs_2", HOME, "asset_2"),
            ("obs_3", OTHER, "asset_3"),
        ):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
                (observation_id, scope_id, asset_id, NOW, NOW, NOW),
            )
        for entity_id, scope_id, kind, name, status in (
            ("mom", HOME, "person", "妈妈", "confirmed"),
            ("kid", HOME, "person", "待确认人物簇 · cluster_1", "pending"),
            ("west", HOME, "place", "西湖", "confirmed"),
            ("neighbour", OTHER, "person", "邻居", "confirmed"),
        ):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, '', 0.0, ?, ?)",
                (entity_id, scope_id, kind, name, status, NOW, NOW),
            )
    return client


def _add_cluster(client: TestClient, cluster_id: str, entity_id: str, *, member_count: int = 1) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id,"
            " representative_embedding_json, member_count, revision, created_at, updated_at)"
            " VALUES (?, ?, 'pending', ?, '[]', ?, 1, ?, ?)",
            (cluster_id, HOME, entity_id, member_count, NOW, NOW),
        )


def _add_face(
    client: TestClient,
    face_id: str,
    cluster_id: str,
    *,
    asset_id: str,
    observation_id: str,
    quality: float,
    detection_confidence: float,
    created_at: str = NOW,
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
            " embedding_model, embedding_version, embedding_quality_signal, validity,"
            " area_ratio, sharpness, created_at)"
            " VALUES (?, ?, ?, ?, '[0,0,1,1]', '[]', ?, ?, '[]', 'frontal', 'buffalo_l',"
            " 'w600k_r50', 0.5, 'verified', 0.1, 0.0, ?)",
            (face_id, asset_id, observation_id, cluster_id, detection_confidence, quality, created_at),
        )


def _people(client: TestClient, scope_id: str = HOME) -> list[dict]:
    return client.get("/api/v1/family-graph", params={"scope_id": scope_id}).json()["people"]


def test_an_unknown_scope_is_its_own_graph_with_no_people(family_client: TestClient) -> None:
    """没并过图的 scope 返回**它自己一个**，而且此时图上什么都没有。"""
    body = family_client.get("/api/v1/family-graph", params={"scope_id": "album_nope"}).json()

    assert body["scope_id"] == "album_nope"
    assert body["scope_ids"] == ["album_nope"]
    assert body["people"] == []
    assert body["relationships"] == []


def test_only_people_appear_and_the_pending_one_gets_the_family_graph_placeholder(
    family_client: TestClient,
) -> None:
    """只出 person；pending 的用统一的「未确认姓名人物」。

    两个端点过去各叫一个词，现在收成同一句用户文案，避免内部 cluster id 泄漏。
    """
    people = _people(family_client)

    assert {item["id"] for item in people} == {"mom", "kid"}
    by_id = {item["id"]: item for item in people}
    assert by_id["mom"]["display_name"] == "妈妈"
    assert by_id["kid"]["display_name"] == "未确认姓名人物"
    assert by_id["mom"]["membership"] is None
    assert by_id["mom"]["portrait"] is None


def test_representative_media_comes_from_faces_not_from_observations(family_client: TestClient) -> None:
    """**这张表走人脸，不走观察。**

    夹具故意让两条口径给出**不同**的答案：``mom`` 有两条观察（``obs_1``/``obs_2``），
    但只在 ``asset_1`` 上有一张脸。走观察的那条会给出 ``preview_*`` 的资产；
    走人脸的这条只给 ``asset_1``。

    定点变异：把 ``_MEDIA_SQL`` 换成 ``entity_observations`` 那条，这条变红。
    """
    _add_cluster(family_client, "cluster_mom", "mom")
    _add_face(
        family_client,
        "face_1",
        "cluster_mom",
        asset_id="asset_1",
        observation_id="obs_1",
        quality=0.5,
        detection_confidence=0.5,
    )
    with _db(family_client).connect() as connection, connection:
        for observation_id in ("obs_1", "obs_2"):
            connection.execute(
                "INSERT INTO entity_observations (entity_id, observation_id, confidence,"
                " source, created_at) VALUES ('mom', ?, 0.5, 'observation_extraction', ?)",
                (observation_id, NOW),
            )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert [item["id"] for item in person["representative_media"]] == ["asset_1"]
    assert person["representative_media"][0]["file_name"] == "asset_1.jpg"


def test_representative_media_is_capped_at_three_and_ordered_by_asset_id_desc(
    family_client: TestClient,
) -> None:
    """``ORDER BY a.id DESC LIMIT 3``——**倒序**且**最多三张**。

    定点变异：把 ``a.id DESC`` 写成 ``ASC``，这条变红（顺序反了）；去掉 LIMIT，
    条数变四。两者都不会报错，只会让「代表照」换一批。
    """
    with _db(family_client).connect() as connection, connection:
        for index in range(4):
            asset_id = f"asset_x{index}"
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, HOME, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
                (f"obs_x{index}", HOME, asset_id, NOW, NOW, NOW),
            )
    _add_cluster(family_client, "cluster_mom", "mom")
    for index in range(4):
        _add_face(
            family_client,
            f"face_x{index}",
            "cluster_mom",
            asset_id=f"asset_x{index}",
            observation_id=f"obs_x{index}",
            quality=0.5,
            detection_confidence=0.5,
        )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert [item["id"] for item in person["representative_media"]] == [
        "asset_x3",
        "asset_x2",
        "asset_x1",
    ]


def test_appearance_count_counts_faces_not_distinct_assets(family_client: TestClient) -> None:
    """``COUNT(*)`` over 人脸实例——**同一个人的三张脸在同一张照片上也数三**。

    与 ``photo_count``（``COUNT(DISTINCT a.id)``）正好相反。定点变异：把
    ``_APPEARANCE_SQL`` 换成带 DISTINCT 的那条，这条变红（3 变 1）。
    """
    _add_cluster(family_client, "cluster_mom", "mom", member_count=3)
    for index in range(3):
        _add_face(
            family_client,
            f"face_{index}",
            "cluster_mom",
            asset_id="asset_1",
            observation_id="obs_1",
            quality=0.5,
            detection_confidence=0.5,
        )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert person["appearance_count"] == 3


def test_the_avatar_prefers_quality_over_detection_confidence(family_client: TestClient) -> None:
    """头像排序是 ``fi.quality DESC`` **优先**，然后是 ``detection_confidence``。

    夹具让两个键给出相反的答案：``face_low_quality`` 的检测置信度更高、
    ``face_high_quality`` 的质量更高。

    定点变异：去掉 ``fi.quality DESC`` 那一位（只剩 detection_confidence），
    这条变红——而头像换一张是**看不出对错**的。
    """
    _add_cluster(family_client, "cluster_mom", "mom")
    _add_face(
        family_client,
        "face_low_quality",
        "cluster_mom",
        asset_id="asset_1",
        observation_id="obs_1",
        quality=0.2,
        detection_confidence=0.99,
    )
    _add_face(
        family_client,
        "face_high_quality",
        "cluster_mom",
        asset_id="asset_2",
        observation_id="obs_2",
        quality=0.9,
        detection_confidence=0.30,
    )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert person["avatar_face_instance_id"] == "face_high_quality"


def test_a_merged_graph_spans_people_and_relationships_across_scopes(family_client: TestClient) -> None:
    """合并过 scope 之后，``people`` 与 ``relationships`` 都跨整张图。

    1.0 ``app.py:1655`` 的 ``relationships`` 是一个对 ``scope_ids`` 的**展平推导**，
    所以它跟着 ``people`` 一起铺开——而 ``scope_id`` 那个键仍然只是入参那一个。
    这条容易看漏。
    """
    with _db(family_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
            " VALUES (?, 'graph_1', ?, ?)",
            (HOME, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
            " VALUES (?, 'graph_1', ?, ?)",
            (OTHER, NOW, NOW),
        )
        for entity_id, subject, predicate, obj, inverse in (
            ("rel_home", "mom", "母亲", "kid", "儿子"),
            ("rel_other", "neighbour", "朋友", "neighbour", "朋友"),
        ):
            for row_id, left, relation, right, back in (
                (f"{entity_id}_a", subject, predicate, obj, inverse),
                (f"{entity_id}_b", obj, inverse, subject, predicate),
            ):
                connection.execute(
                    "INSERT INTO family_relationships (id, scope_id, subject_entity_id,"
                    " predicate, object_entity_id, inverse_predicate, source, state, locked,"
                    " revision, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'user_override', 'effective', 1, 1, ?, ?)",
                    (
                        row_id,
                        HOME if entity_id == "rel_home" else OTHER,
                        left,
                        relation,
                        right,
                        back,
                        NOW,
                        NOW,
                    ),
                )

    body = family_client.get("/api/v1/family-graph", params={"scope_id": HOME}).json()

    assert body["scope_id"] == HOME
    assert body["scope_ids"] == [HOME, OTHER]
    assert {item["id"] for item in body["people"]} == {"mom", "kid", "neighbour"}
    # 关系也跨图——两条边各落两行，一共四行。
    assert len(body["relationships"]) == 4


# ---- 写侧（S7b1 · T306） ---------------------------------------------------------


def _patch_membership(client: TestClient, **body: object):
    return client.patch("/api/v1/family-graph/people/mom/membership", json=body)


def _put_relationship(client: TestClient, **body: object):
    payload = {
        "scope_id": HOME,
        "subject_entity_id": "mom",
        "predicate": "母亲",
        "object_entity_id": "kid",
        "inverse_predicate": "儿子",
    }
    payload.update(body)
    return client.put("/api/v1/family-graph/relationships", json=payload)


def _blocks(client: TestClient) -> list[dict]:
    with _db(client).connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM family_relationship_blocks")]


def test_a_user_membership_lands_locked_with_full_confidence(family_client: TestClient) -> None:
    """五个写路由**全部**写死 ``source="user_override"``、``confidence=1.0``。

    ``locked`` 由 source 决定——它是「模型从此不许覆盖这一行」的标记，所以这三个值
    是一组，改一个就动了整条规则。
    """
    body = _patch_membership(family_client, scope_id=HOME, membership="family").json()

    assert body["ok"] is True
    assert body["membership"]["membership"] == "family"
    assert body["membership"]["source"] == "user_override"
    assert body["membership"]["confidence"] == 1.0
    assert body["membership"]["locked"] is True


def test_a_membership_without_a_scope_is_a_400_with_the_literal_message(
    family_client: TestClient,
) -> None:
    """消息逐字照 1.0。写成 pydantic 必填会变成 422——同一个产品行为换一个码。"""
    response = _patch_membership(family_client, membership="family")

    assert response.status_code == 400
    assert response.json()["message"] == "scope_id and membership are required"


def test_the_repository_message_survives_the_400(family_client: TestClient) -> None:
    """仓储抛的 ``ValueError`` 翻成 400，但**消息不改写**。

    那几句话（``unsupported family membership`` 等）是调用方用来区分失败的，润色一遍
    就等于换了契约。定点变异：把 ``str(error)`` 换成固定文案，这条变红。
    """
    response = _patch_membership(family_client, scope_id=HOME, membership="邻居")

    assert response.status_code == 400
    assert response.json()["message"] == "unsupported family membership"


def test_a_user_relationship_lands_as_two_locked_rows(family_client: TestClient) -> None:
    body = _put_relationship(family_client).json()

    assert body["ok"] is True
    assert [
        (item["subject_entity_id"], item["predicate"], item["object_entity_id"])
        for item in body["relationships"]
    ] == [("mom", "母亲", "kid"), ("kid", "儿子", "mom")]
    assert all(item["locked"] for item in body["relationships"])
    assert {item["source"] for item in body["relationships"]} == {"user_override"}


def test_a_relationship_without_a_scope_is_a_400(family_client: TestClient) -> None:
    response = _put_relationship(family_client, scope_id=None)

    assert response.status_code == 400
    assert response.json()["message"] == "scope_id is required"


def test_a_label_outside_the_whitelist_is_a_400_with_the_whitelist_message(
    family_client: TestClient,
) -> None:
    """白名单校验在仓储里，路由只把它的话术带出去。"""
    response = _put_relationship(family_client, predicate="同事", inverse_predicate="同事")

    assert response.status_code == 400
    assert response.json()["message"] == "关系标签不匹配: 同事 -> 同事"


def test_retracting_writes_a_block_so_the_model_cannot_restore_it(family_client: TestClient) -> None:
    """撤回**同时**落 block——那是这个端点与「删除」的区别：它在库里留一道护栏。"""
    _put_relationship(family_client)

    body = family_client.request(
        "DELETE",
        "/api/v1/family-graph/relationships",
        json={"scope_id": HOME, "subject_entity_id": "mom", "object_entity_id": "kid"},
    ).json()

    assert body["ok"] is True
    assert {item["state"] for item in body["retracted"]} == {"retracted"}
    block = _blocks(family_client)[0]
    assert (block["left_entity_id"], block["right_entity_id"]) == ("kid", "mom")
    assert block["locked"] == 1


def test_a_retract_without_one_of_the_three_ids_is_a_400(family_client: TestClient) -> None:
    response = family_client.request(
        "DELETE",
        "/api/v1/family-graph/relationships",
        json={"scope_id": HOME, "subject_entity_id": "mom"},
    )

    assert response.status_code == 400
    assert response.json()["message"] == ("scope_id, subject_entity_id and object_entity_id are required")


def test_a_user_portrait_lands_locked(family_client: TestClient) -> None:
    body = family_client.patch(
        "/api/v1/family-graph/people/mom/portrait",
        json={"scope_id": HOME, "portrait_text": "妈妈是家里的主心骨"},
    ).json()

    assert body["ok"] is True
    assert body["portrait"]["portrait_text"] == "妈妈是家里的主心骨"
    assert body["portrait"]["source"] == "user_override"
    assert body["portrait"]["locked"] is True


def test_a_portrait_without_text_is_a_400(family_client: TestClient) -> None:
    response = family_client.patch("/api/v1/family-graph/people/mom/portrait", json={"scope_id": HOME})

    assert response.status_code == 400
    assert response.json()["message"] == "scope_id and portrait_text are required"


def test_merging_scopes_answers_without_an_ok_key(family_client: TestClient) -> None:
    """**这个端点的响应没有 ``ok`` 键**——1.0 直接返回仓储那个 dict。

    与另外四个写路由的 ``{"ok": True, ...}`` 不一致，而统一它会给这个端点加一个
    1.0 没有的键。定点变异：给它补上 ``ok``，这条变红。
    """
    body = family_client.post("/api/v1/family-graph/scopes/merge", json={"scope_ids": [HOME, OTHER]}).json()

    assert "ok" not in body
    assert body["scope_ids"] == [HOME, OTHER]
    assert body["graph_id"]


def test_merging_fewer_than_two_scopes_is_a_400(family_client: TestClient) -> None:
    response = family_client.post("/api/v1/family-graph/scopes/merge", json={"scope_ids": [HOME]})

    assert response.status_code == 400
    assert response.json()["message"] == "at least two scopes are required"


def test_a_relationship_built_over_http_shows_up_in_the_graph(family_client: TestClient) -> None:
    """闭环：``PUT`` 建边 → ``GET`` 读得到，而且两端的人都出现在 ``people`` 里。"""
    _put_relationship(family_client)

    body = family_client.get("/api/v1/family-graph", params={"scope_id": HOME}).json()

    assert {item["predicate"] for item in body["relationships"]} == {"母亲", "儿子"}
    assert {item["id"] for item in body["people"]} == {"mom", "kid"}


# ---- 推断运行（S7b2 · T313） ------------------------------------------------------


def test_the_graph_returns_a_null_run_until_one_has_been_created(family_client: TestClient) -> None:
    """没跑过就是 ``null``——**不是省略这个键**。

    1.0 的响应里这个键一直在（值是 ``latest_family_analysis_run`` 的结果）。
    b1 时 2.0 刻意不返回它（那时没有任何东西会创建 run），T313 补上。
    """
    body = family_client.get("/api/v1/family-graph", params={"scope_id": HOME}).json()

    assert "run" in body
    assert body["run"] is None


def test_running_the_inference_is_synchronous_and_answers_with_the_final_run(
    family_client: TestClient,
) -> None:
    """**同步**：POST 返回时那趟已经跑完——所以响应体是最终的 run，**没有 ``status`` 包装**。

    1.0 是 ``{"status": 202, "run": ...}``，因为它起线程后立刻返回。2.0 裁决成同步
    （与相邻的 ``_maybe_run_insights`` 同体例），再报 202「已受理」就是假话。

    **这里不断言 ``completed`` 还是 ``failed``**：契约测试的容器里那个 chat 端点不保证
    可达，而服务对模型失败的处理正是「把 run 标成 failed 而不抛」（T312 钉过）。
    要钉的是**形状与同步性**——响应返回时 run 已经不是 ``queued``。
    """
    response = family_client.post("/api/v1/family-graph/runs", json={"scope_id": HOME})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"run"}  # 1.0 那个 status 键在 2.0 没有
    assert body["run"]["status"] in {"completed", "failed"}
    assert body["run"]["scope_id"] == HOME
    # 跑完之后 GET 也看得到同一个 run。
    graph = family_client.get("/api/v1/family-graph", params={"scope_id": HOME}).json()
    assert graph["run"]["id"] == body["run"]["id"]


def test_running_without_a_scope_is_a_400(family_client: TestClient) -> None:
    response = family_client.post("/api/v1/family-graph/runs", json={})

    assert response.status_code == 400
    assert response.json()["message"] == "scope_id is required"


def test_a_run_already_in_progress_is_a_409(family_client: TestClient) -> None:
    """1.0 ``app.py:1714-1716`` 的 409。

    同步模式下 ``queued`` 不会停留，所以这条实际挡的是「上一次崩在半路、状态还写着
    ``running``」的行——夹具就直接造那样一行。
    """
    with _db(family_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_analysis_runs (id, scope_id, status, current_stage,"
            " created_at, updated_at) VALUES ('run_stuck', ?, 'running', 'infer_graph', ?, ?)",
            (HOME, NOW, NOW),
        )

    response = family_client.post("/api/v1/family-graph/runs", json={"scope_id": HOME})

    assert response.status_code == 409
    assert response.json()["message"] == "family analysis run already in progress"


def test_the_run_key_belongs_to_the_scope_in_the_request(family_client: TestClient) -> None:
    """``run`` 取的是**入参那个 scope** 的最近一次，**不是整张图的**。

    这条与 ``relationships`` 正好相反（那个跨整张图）——1.0 两处就是这么写的，
    看漏的话合并过 scope 之后会拿到别的相册的 run。
    """
    with _db(family_client).connect() as connection, connection:
        # **把两个 scope 并成一张图**——不并的话 ``scope_ids == [HOME]``，
        # 「取入参的 scope」与「取图里的第一个」给出同一个答案。
        for scope_id in (HOME, OTHER):
            connection.execute(
                "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
                " VALUES (?, 'graph_run_scope', ?, ?)",
                (scope_id, NOW, NOW),
            )
        for scope_id, run_id in ((HOME, "run_home"), (OTHER, "run_other")):
            connection.execute(
                "INSERT INTO family_analysis_runs (id, scope_id, status, current_stage,"
                " created_at, updated_at) VALUES (?, ?, 'completed', 'done', ?, ?)",
                (run_id, scope_id, NOW, NOW),
            )

    # **用 ``OTHER`` 发起请求**：合并后的 ``scope_ids`` 按 id 排序是 ``[album1, album2]``，
    # 所以「取入参那个 scope」与「取图里的第一个」在这一步才给出不同的答案。
    # 用 ``HOME`` 问的话两者都是 ``run_home``，这条用例就抓不到东西（第一版就是这样）。
    body = family_client.get("/api/v1/family-graph", params={"scope_id": OTHER}).json()

    assert body["scope_ids"] == [HOME, OTHER]
    assert body["run"]["id"] == "run_other"

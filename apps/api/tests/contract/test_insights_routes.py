"""人物画像端点的契约测试：读视图的分档与兜底、同步 run 的六个计数、画像与反馈。

三条链路各自的边界都在这里钉住：

1. **读视图**（``GET /person-insights``）按 ``tier`` 分档、空 ``scope_id`` 兜底成
   ``home-default``、还没命名的人显示成「未确认姓名人物」，并且**恰好**只返回 1.0 里还活着的
   那两块。``run`` / ``role_candidates`` / ``album_owner_candidates`` /
   ``relationship_hypotheses`` 是模块 docstring 明确不搬的四块（``infer_graph`` 整个不搬），
   断言的是**键不存在**而不是「值为空」——留一个恒空的占位键，读的人会以为那条链路是通的。
2. **同步 run**（``POST /person-insights/run``）空 ``scope_id`` 是 400、正常路径返回六个
   计数。读侧兜底而写侧不兜底是**故意的**：它是重活，替调用方猜一个空间去写会往那里灌
   一批真的画像行。
3. **画像与反馈**：没有当前版本一律 404；反馈写进**当前生效**那一版且不新建版本；纯空白
   反馈存成 ``NULL``。响应里的 ``feedback`` 对应的记录字段叫 ``user_feedback``——这层映射
   抄错一次，接口会照样 200，而前端读到的反馈永远是 ``null``。

只跑 HTTP。跑流水线的重活（每个核心人物至少一次模型调用）不在这里验：run 的计数链路用一个
假 ``RunInsights`` 从 ``_run_insights``（路由唯一的装配缝）注入，验的是**接线**——六个键、
原样透传、strip 之后才交给用例——不是流水线本身。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sentrix.api.routes import insights as insights_routes
from sentrix.application.identity.run_insights import InsightRun
from sentrix.domain.identity.models import DEFAULT_SCOPE_ID

NOW = "2026-09-25T10:00:00+00:00"
HOME = DEFAULT_SCOPE_ID  # 空 scope_id 的落点
OTHER = "album-other"

# 1.0 有三块（``run`` / ``role_candidates`` / ``album_owner_candidates`` /
# ``relationship_hypotheses``）在 2.0 没有了。四块一起在这里列一份，用例只引用它。
REMOVED_BLOCKS = ("run", "role_candidates", "album_owner_candidates", "relationship_hypotheses")

# 画像那一行的取值，插入与断言共用：它们要能一眼分辨「这一列映射到响应里的哪个键」。
PORTRAIT_ID = "portrait_mom_2"
PORTRAIT_TEXT = "妈妈常在周末带大家去公园，喜欢拍照。"
PORTRAIT_THEMES = [
    {"title": "周末", "summary": "常在公园", "evidence_refs": [{"kind": "moment", "id": "m_1"}]}
]
PORTRAIT_REFS = [{"kind": "moment", "id": "m_1"}]


def _db(client: TestClient):
    return client.app.state.container.database


def _execute(client: TestClient, sql: str, params: tuple[object, ...]) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(sql, params)


def _rows(client: TestClient, sql: str, params: tuple[object, ...] = ()):
    with _db(client).connect() as connection:
        return connection.execute(sql, params).fetchall()


def _ensure_scope(client: TestClient, scope_id: str) -> None:
    """一个空间行 + 一个资产行。观察与人脸实例都要靠它们满足外键，且幂等。"""
    _execute(
        client,
        "INSERT OR IGNORE INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (scope_id, scope_id, NOW, NOW),
    )
    _execute(
        client,
        "INSERT OR IGNORE INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
        " VALUES (?, ?, ?, 'image', ?, ?, ?)",
        (f"asset_{scope_id}", scope_id, f"{scope_id}.jpg", f"/tmp/{scope_id}.jpg", NOW, NOW),
    )


def _add_entity(
    client: TestClient,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = HOME,
    family_role: str | None = None,
) -> None:
    _ensure_scope(client, scope_id)
    _execute(
        client,
        "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, family_role,"
        " summary, confidence, created_at, updated_at)"
        " VALUES (?, ?, 'person', ?, ?, ?, '', 0.0, ?, ?)",
        (entity_id, scope_id, name, status, family_role, NOW, NOW),
    )


def _add_cluster(
    client: TestClient,
    cluster_id: str,
    *,
    scope_id: str = HOME,
    entity_id: str | None = None,
    status: str = "pending",
) -> None:
    _ensure_scope(client, scope_id)
    _execute(
        client,
        "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
        " member_count, revision, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '[]', 1, 1, ?, ?)",
        (cluster_id, scope_id, status, entity_id, NOW, NOW),
    )


def _add_observation(
    client: TestClient, observation_id: str, *, captured_at: str, scope_id: str = HOME
) -> None:
    _ensure_scope(client, scope_id)
    _execute(
        client,
        "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at, created_at, updated_at)"
        " VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
        (observation_id, scope_id, f"asset_{scope_id}", captured_at, NOW, NOW),
    )


def _add_instance(
    client: TestClient,
    instance_id: str,
    *,
    cluster_id: str,
    observation_id: str,
    scope_id: str = HOME,
) -> None:
    _execute(
        client,
        "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json,"
        " detection_confidence, quality, pose_json, pose_bucket, embedding_model, embedding_version,"
        " embedding_quality_signal, validity, area_ratio, sharpness, created_at)"
        " VALUES (?, ?, ?, ?, '[0,0,10,10]', '[]', 0.9, 0.8, '[]', 'frontal', 'buffalo_l', 'w600k_r50',"
        " 0.5, 'verified', 0.1, 0.0, ?)",
        (instance_id, f"asset_{scope_id}", observation_id, cluster_id, NOW),
    )


def _add_person(
    client: TestClient,
    cluster_id: str,
    *,
    name: str | None = None,
    confirmed: bool = False,
    dates: tuple[str, ...] = (),
    scope_id: str = HOME,
) -> str:
    """造一个人：一个簇 +（可选）实体 + 若干天的观察。返回**接口上的** ``person_id``。

    ``name=None`` 就是「还没命名」在数据层的样子：**不建实体行**。于是
    ``person_candidate_features`` 的 ``COALESCE(c.entity_id, c.id)`` 把簇 id 当 person_id，
    路由查实体查不到，落到「未确认姓名人物」那个兜底上（``FaceClusterRecord`` 说 ``entity_id``
    可空——簇建出来到实体建出来之间就有这个窗口）。

    ``dates`` 是观察的拍摄日期，决定 ``date_count``——而 ``date_count`` 是 ``tier`` 的
    判据之一（跨够两天才算 ``core``/``common``）。
    """
    entity_id = f"ent_{cluster_id}" if name is not None else None
    if entity_id is not None:
        _add_entity(
            client,
            entity_id,
            name=name or "",
            status="confirmed" if confirmed else "pending",
            scope_id=scope_id,
        )
    _add_cluster(
        client,
        cluster_id,
        scope_id=scope_id,
        entity_id=entity_id,
        status="confirmed" if confirmed else "pending",
    )
    for index, day in enumerate(dates):
        observation_id = f"obs_{cluster_id}_{index}"
        _add_observation(client, observation_id, captured_at=f"{day}T09:00:00+00:00", scope_id=scope_id)
        _add_instance(
            client, f"face_{cluster_id}_{index}", cluster_id=cluster_id, observation_id=observation_id
        )
    return entity_id or cluster_id


def _add_portrait(
    client: TestClient,
    portrait_id: str,
    *,
    person_id: str,
    revision: int,
    status: str = "active",
    feedback: str | None = None,
    scope_id: str = HOME,
) -> None:
    """直接插一行画像修订。

    不走 ``SqlitePortraitRepository.create``：那条路会把上一版标成 superseded、也算不出
    ``user_feedback``（``create`` 的签名里没有它）。这里要的是每一列都有一个**可辨认的**
    值，好让「哪一列映射到响应里的哪个键」有据可查。
    """
    _ensure_scope(client, scope_id)
    _execute(
        client,
        "INSERT INTO person_portrait_revisions (id, scope_id, person_id, revision, portrait_text,"
        " themes_json, evidence_refs_json, trigger_type, model_name, prompt_version, status,"
        " user_feedback, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 'fake-vlm', 'portrait-v1', ?, ?, ?, ?)",
        (
            portrait_id,
            scope_id,
            person_id,
            revision,
            PORTRAIT_TEXT,
            json.dumps(PORTRAIT_THEMES, ensure_ascii=False),
            json.dumps(PORTRAIT_REFS, ensure_ascii=False),
            status,
            feedback,
            NOW,
            NOW,
        ),
    )


def _error(response) -> tuple[str, str]:
    body = response.json()
    return body["code"], body["message"]


def _person_ids(body: dict, tier: str) -> list[str]:
    return [item["person_id"] for item in body["tiers"][tier]]


# ---- 读视图 ----------------------------------------------------------------------


def test_an_empty_scope_falls_back_to_the_default_space(client: TestClient) -> None:
    """空的 ``scope_id`` 读的是 ``home-default``，响应里回填的也是它。

    兜底必须同时体现在**查询**与**回填**上：只回填不用于查询的话，响应会自称
    ``home-default`` 却带着另一个空间的分档——所以这里两个空间各放一个人，看回来的是
    默认空间那一个。
    """
    _add_person(client, "c_default", name="默认空间的人")
    _add_person(client, "c_other", name="别的空间的人", scope_id=OTHER)

    body = client.get("/api/v1/person-insights").json()

    assert body["scope_id"] == "home-default"
    assert _person_ids(body, "incidental") == ["ent_c_default"]


def test_the_read_view_buckets_people_into_the_three_tiers(client: TestClient) -> None:
    """分档由 ``rank_core_people`` 判，路由只负责搬运记录字段。

    前 ``CORE_PEOPLE_LIMIT``(10) 名里「已确认或跨≥2 天」的是 ``core``，此外跨≥2 天的是
    ``common``，剩下的 ``incidental``。要造出一个 ``common`` 就必须先有 10 个人排在前面
    ——这条用例因此顺带钉住「排序」与「分档」是两件事，而不是「前 N 个人就是 core」。
    """
    for index in range(10):
        _add_person(client, f"c_core_{index}", name=f"核心{index}", confirmed=True)
    _add_person(client, "c_common", dates=("2026-09-01", "2026-09-02"))
    _add_person(client, "c_incidental", dates=("2026-09-03",))

    body = client.get("/api/v1/person-insights").json()

    assert len(body["tiers"]["core"]) == 10
    assert _person_ids(body, "common") == ["c_common"]
    assert _person_ids(body, "incidental") == ["c_incidental"]
    # 每一项都带 display_name；命了名的显示本名，不是 id。
    assert {item["display_name"] for item in body["tiers"]["core"]} == {f"核心{i}" for i in range(10)}
    for items in body["tiers"].values():
        assert all("display_name" in item for item in items)


def test_an_unnamed_person_is_shown_as_an_anonymous_member(client: TestClient) -> None:
    """还没命名的人显示成「未确认姓名人物」，**不是他的 id**。

    数据层的样子是「簇有、实体还没有」（``entity_id`` 可空）：``person_id`` 落成簇 id。
    断言的是显示名而不是 id，才钉得住 1.0 ``app.py:1583`` 那个兜底——把它删掉，这里变成
    空串；换成 ``str(item["person_id"])``，这里变成 ``person_ab12cd34``。
    """
    person_id = _add_person(client, "person_ab12cd34")

    body = client.get("/api/v1/person-insights").json()

    item = body["tiers"]["incidental"][0]
    assert item["person_id"] == person_id == "person_ab12cd34"
    assert item["display_name"] == "未确认姓名人物"


def test_the_read_view_does_not_carry_the_four_blocks_1_0_had(client: TestClient) -> None:
    """读视图比 1.0 少四块，且**不留恒空的占位键**。

    判据是「键不存在」而不是「值为空」：留一个永远 ``[]`` / ``None`` 的键，读的人会以为
    那条链路是通的（模块 docstring 与 ``s5-deviations.md`` 第 123 条）。
    """
    _add_person(client, "c_mom", name="妈妈", confirmed=True)

    body = client.get("/api/v1/person-insights").json()

    for key in REMOVED_BLOCKS:
        assert key not in body
        for items in body["tiers"].values():
            for item in items:
                assert key not in item


# ---- 同步跑一趟 ------------------------------------------------------------------


@pytest.mark.parametrize("scope_id", ["", "   ", "\t\n "])
def test_run_with_a_blank_scope_is_400(client: TestClient, scope_id: str) -> None:
    """写侧**不给兜底**：这是重活，替调用方猜一个空间去写会往那里灌一批真的画像行。

    空串与纯空白都是 400（领域判据），不是 422——把它写成 pydantic 的 ``min_length``
    会让状态码悄悄换一个，调用方的分支就落空了。
    """
    response = client.post("/api/v1/person-insights/run", json={"scope_id": scope_id})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "scope_id is required")


def test_run_returns_the_six_counts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径是 200 + 六个计数键，值与用例返回的一致。

    注入点在 ``_run_insights``——路由唯一的装配缝：真 ``RunInsights`` 会走模型，而契约测试
    验的是这条链路的**接线**（键名与条数），不是流水线。传进去的 scope 带空白，顺带钉住
    「校验之后、开工之前 strip 了一次」。
    """
    received: list[str] = []

    class _FakeRunInsights:
        def __call__(self, scope_id: str) -> InsightRun:
            received.append(scope_id)
            return InsightRun(ranked=7, core=3, selections=21, moments=5, moment_failures=2, portraits=3)

    monkeypatch.setattr(insights_routes, "_run_insights", lambda container: _FakeRunInsights())

    response = client.post("/api/v1/person-insights/run", json={"scope_id": "  album1  "})

    assert response.status_code == 200
    assert response.json() == {
        "ranked": 7,
        "core": 3,
        "selections": 21,
        "moments": 5,
        "moment_failures": 2,
        "portraits": 3,
    }
    assert received == ["album1"]


# ---- 单个人的画像 ----------------------------------------------------------------


def test_a_person_without_a_portrait_is_404(client: TestClient) -> None:
    """没有当前版本就是 404，不是 200 + 空对象——「这个人没画像」与「画像没内容」是两件事。"""
    _add_entity(client, "ent_mom", name="妈妈", status="confirmed")

    response = client.get("/api/v1/people/ent_mom/portrait")

    assert response.status_code == 404
    assert _error(response) == ("not_found", "portrait not found")


def test_a_portrait_carries_every_field_and_maps_user_feedback_to_feedback(client: TestClient) -> None:
    """字段齐全，且 ``feedback`` 的来源是记录里的 ``user_feedback``。

    两个名字在记录侧与响应侧不一样（``user_feedback`` / ``feedback``）：抄错一次，接口照样
    200，而前端读到的反馈永远是 ``null``。所以这里既钉那个键的**值**，也钉那个**不该出现**
    的键名。
    """
    _add_entity(client, "ent_mom", name="妈妈", status="confirmed")
    _add_portrait(client, PORTRAIT_ID, person_id="ent_mom", revision=2, feedback="写得挺准")

    response = client.get("/api/v1/people/ent_mom/portrait")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == PORTRAIT_ID
    assert body["person_id"] == "ent_mom"
    assert body["revision"] == 2
    assert body["portrait_text"] == PORTRAIT_TEXT
    assert body["themes"] == PORTRAIT_THEMES
    assert body["evidence_refs"] == PORTRAIT_REFS
    assert body["status"] == "active"
    assert body["trigger_type"] == "manual"
    assert body["model_name"] == "fake-vlm"
    assert body["prompt_version"] == "portrait-v1"
    assert body["feedback"] == "写得挺准"
    assert "user_feedback" not in body


# ---- 反馈 ------------------------------------------------------------------------


def test_feedback_without_a_current_version_is_404(client: TestClient) -> None:
    """找不到当前生效的那一版就 404——先取再写，取不到就不写。"""
    _add_entity(client, "ent_mom", name="妈妈", status="confirmed")

    response = client.post("/api/v1/people/ent_mom/portrait-feedback", json={"feedback": "很准"})

    assert response.status_code == 404
    assert _error(response) == ("not_found", "portrait not found")


def test_feedback_lands_on_the_active_version_without_creating_one(client: TestClient) -> None:
    """反馈写进**当前生效**那一版，且不新建版本——修订数不变。

    库里两版（1 被顶掉、2 生效）：写错版本的话第 1 版的 ``user_feedback`` 会变；顺手
    ``create`` 一版的话行数会变。两处都钉在这一条上。
    """
    _add_entity(client, "ent_mom", name="妈妈", status="confirmed")
    _add_portrait(client, "portrait_mom_1", person_id="ent_mom", revision=1, status="superseded")
    _add_portrait(client, PORTRAIT_ID, person_id="ent_mom", revision=2, status="active")

    response = client.post("/api/v1/people/ent_mom/portrait-feedback", json={"feedback": "  很准  "})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == PORTRAIT_ID
    assert body["revision"] == 2
    # 前后空白被 strip 掉了，不是原样落库。
    assert body["feedback"] == "很准"
    rows = _rows(
        client,
        "SELECT revision, status, user_feedback FROM person_portrait_revisions"
        " WHERE person_id = ? ORDER BY revision",
        ("ent_mom",),
    )
    assert [(row["revision"], row["status"], row["user_feedback"]) for row in rows] == [
        (1, "superseded", None),
        (2, "active", "很准"),
    ]


def test_a_whitespace_feedback_is_stored_as_null(client: TestClient) -> None:
    """纯空白 = 清空反馈，落库是 ``NULL`` 而不是空串。

    「清空」与「写过一串空白」在库里必须是同一件事（``PortraitRepository.set_feedback``
    的签名也是 ``str | None``）：只 ``strip()`` 不 ``or None`` 就会存成一个空串，于是
    「没写过」的 NULL 与「清空过」的 '' 分叉，读侧要判两次。
    """
    _add_entity(client, "ent_mom", name="妈妈", status="confirmed")
    _add_portrait(client, PORTRAIT_ID, person_id="ent_mom", revision=1, feedback="旧反馈")

    response = client.post("/api/v1/people/ent_mom/portrait-feedback", json={"feedback": "   \t "})

    assert response.status_code == 200
    assert response.json()["feedback"] is None
    rows = _rows(
        client,
        "SELECT user_feedback FROM person_portrait_revisions WHERE id = ?",
        (PORTRAIT_ID,),
    )
    assert rows[0]["user_feedback"] is None

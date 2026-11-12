"""身份端点的契约测试：状态码、响应形状、以及**响应里那几个字面量的内容**。

只跑 HTTP，不碰模型：容器里的检测器与嵌入器是 ``conftest`` 的确定性假件，簇与实例由
用例自己插进真 SQLite。这里验的是传输层——四审阅端点的 400 / 404 / 409、裁剪端点的
``thumbnail`` 尺寸与缓存头、以及 ``pending`` 实体在**接口上**叫「未确认姓名人物」。

**裁决③之后的形状**：领域错误一律走 ``{"code", "message", "detail"}``（``api/errors.py``
的唯一处理器），不再是 ``HTTPException`` 的 ``{"detail"}``。所以断言里同时钉 **码**与
**状态**——只钉状态的话，「400 是从哪来的」这件事仍然没被验到。
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentrix.application.identity.person_directory import EntityPersonDirectory
from sentrix.domain.identity.models import PENDING_CLUSTER_NAME

NOW = "2026-09-23T10:00:00+00:00"

HOME = "album1"
HIDDEN = "album-eval"
FRESH = "album-fresh"


def _db(client: TestClient):
    return client.app.state.container.database


def _seed(client: TestClient, *, hidden_scope: bool = False) -> None:
    """一份最小的身份数据集：两个空间、两个资产、三条观察。

    ``HIDDEN`` 空间 ``include_in_people = 0``——它钉住的是「评测空间聚出来的人脸不进
    人物视图」这条空间规则在两个列表端点上都成立。
    """
    with _db(client).connect() as connection, connection:
        for scope_id, include in ((HOME, 1), (HIDDEN, 0)):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (scope_id, scope_id, include, NOW, NOW),
            )
        for asset_id, scope_id in (("asset_1", HOME), ("asset_hidden", HIDDEN)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for index in (1, 2):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, ?, ?, 'vlm', ?, ?)",
                (f"obs_{index}", HOME, "asset_1", NOW, NOW),
            )


def add_entity(
    client: TestClient,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = HOME,
    family_role: str | None = None,
    summary: str = "",
    entity_type: str = "person",
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, family_role, summary,"
            "  confidence, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?)",
            (entity_id, scope_id, entity_type, name, status, family_role, summary, NOW, NOW),
        )


def add_cluster(
    client: TestClient,
    cluster_id: str,
    *,
    scope_id: str = HOME,
    status: str = "pending",
    entity_id: str | None = None,
    member_count: int = 0,
    revision: int = 1,
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?)",
            (cluster_id, scope_id, status, entity_id, member_count, revision, NOW, NOW),
        )


def add_instance(
    client: TestClient,
    instance_id: str,
    *,
    cluster_id: str | None,
    asset_id: str = "asset_1",
    observation_id: str = "obs_1",
    bbox: tuple[float, ...] = (0.0, 0.0, 10.0, 10.0),
    detection_confidence: float = 0.9,
    quality: float = 0.8,
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json,"
            "  detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            "  embedding_version, embedding_quality_signal, validity, area_ratio, sharpness,"
            "  created_at)"
            " VALUES (?, ?, ?, ?, ?, '[]', ?, ?, '[]', 'frontal', 'buffalo_l', 'w600k_r50',"
            "  0.5, 'verified', 0.1, 0.0, ?)",
            (
                instance_id,
                asset_id,
                observation_id,
                cluster_id,
                json.dumps(list(bbox)),
                detection_confidence,
                quality,
                NOW,
            ),
        )


@pytest.fixture
def identity_client(client: TestClient) -> TestClient:
    _seed(client)
    return client


def _error(response) -> tuple[str, str]:
    body = response.json()
    return body["code"], body["message"]


# ---- 簇列表 ----------------------------------------------------------------------


def test_an_empty_library_lists_no_clusters(client: TestClient):
    body = client.get("/api/v1/face-clusters").json()

    assert body == {"clusters": []}


def test_a_pending_cluster_is_shown_as_an_unnamed_member(client: TestClient):
    """未命名簇的名字是**接口上的常量**，不是落库的那个「待确认人物簇 · cluster-x」。"""
    _seed(client)
    add_entity(client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(client, "cluster_1", entity_id="ent_1", member_count=1)
    add_instance(client, "face_1", cluster_id="cluster_1")

    cluster = client.get("/api/v1/face-clusters").json()["clusters"][0]

    assert cluster["canonical_name"] == PENDING_CLUSTER_NAME == "未确认姓名人物"
    assert cluster["family_role"] is None
    assert cluster["entity_status"] == "pending"
    assert cluster["reviewable"] is True
    assert cluster["single_sample"] is True
    # 样本脸的整串地址由服务端给全，前端不拼路径。
    assert cluster["samples"][0]["crop_url"] == "/api/v1/face-instances/face_1/crop"
    assert cluster["samples"][0]["bbox"] == [0.0, 0.0, 10.0, 10.0]


def test_a_rejected_cluster_is_not_listed_unless_asked_for(client: TestClient):
    _seed(client)
    add_cluster(client, "cluster_rejected", status="rejected", member_count=0)

    assert client.get("/api/v1/face-clusters").json()["clusters"] == []
    rejected = client.get("/api/v1/face-clusters", params={"status": "rejected"}).json()
    assert [item["id"] for item in rejected["clusters"]] == ["cluster_rejected"]


def test_clusters_from_a_hidden_space_never_enter_the_people_view(client: TestClient):
    """``include_in_people = 0`` 的空间里的簇一律不出现——这是端口的契约，不是调用方的过滤。"""
    _seed(client)
    add_cluster(client, "cluster_hidden", scope_id=HIDDEN, member_count=3)

    assert client.get("/api/v1/face-clusters").json()["clusters"] == []


def test_scope_filter_narrows_the_list(client: TestClient):
    _seed(client)
    add_cluster(client, "cluster_1", member_count=1)
    add_cluster(client, "cluster_2", scope_id="album2", member_count=1)
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album2', 'album2', 1, ?, ?)",
            (NOW, NOW),
        )

    body = client.get("/api/v1/face-clusters", params={"scope_id": "album2"}).json()

    assert [item["id"] for item in body["clusters"]] == ["cluster_2"]


def test_single_sample_is_false_once_the_cluster_has_two_faces(client: TestClient):
    _seed(client)
    add_entity(client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(client, "cluster_1", entity_id="ent_1", member_count=2)
    add_instance(client, "face_1", cluster_id="cluster_1")
    add_instance(client, "face_2", cluster_id="cluster_1")

    cluster = client.get("/api/v1/face-clusters").json()["clusters"][0]

    assert cluster["single_sample"] is False
    assert len(cluster["samples"]) == 2


# ---- 确认 ------------------------------------------------------------------------


def test_confirm_names_the_person_and_flips_the_cluster(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=1)
    add_instance(identity_client, "face_1", cluster_id="cluster_1")

    response = identity_client.post(
        "/api/v1/face-clusters/cluster_1/confirm", json={"name": "妈妈", "family_role": "母亲"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entity"]["canonical_name"] == "妈妈"
    assert body["entity"]["confirmed"] is True
    assert body["entity"]["family_role"] == "母亲"
    assert body["merged_into"] is None
    cluster = identity_client.get("/api/v1/face-clusters").json()["clusters"][0]
    assert cluster["status"] == "confirmed"
    assert cluster["revision"] == 2


def test_a_blank_name_is_400_not_422(identity_client: TestClient):
    """空名字是**用例的判据**（400），不是请求格式错（422）。

    1.0 的路由先 strip 再判空并抛 400；把它写成 pydantic 的 ``min_length`` 会让状态码
    悄悄从 400 变成 422——同一个产品行为换了一个码，调用方的分支就落空了。
    """
    add_cluster(identity_client, "cluster_1", member_count=1)

    response = identity_client.post("/api/v1/face-clusters/cluster_1/confirm", json={"name": "   "})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "entity name is required")


def test_confirming_an_unknown_cluster_is_404(identity_client: TestClient):
    response = identity_client.post("/api/v1/face-clusters/cluster_nope/confirm", json={"name": "妈妈"})

    assert response.status_code == 404
    assert _error(response)[0] == "not_found"


def test_confirming_with_an_existing_name_merges_into_that_person(identity_client: TestClient):
    add_entity(identity_client, "ent_known", name="妈妈", status="confirmed", family_role="母亲")
    add_cluster(identity_client, "cluster_known", status="confirmed", entity_id="ent_known", member_count=1)
    add_instance(identity_client, "face_known", cluster_id="cluster_known")
    add_entity(identity_client, "ent_new", name="待确认人物簇 · cluster_new")
    add_cluster(identity_client, "cluster_new", entity_id="ent_new", member_count=1)
    add_instance(identity_client, "face_new", cluster_id="cluster_new")

    response = identity_client.post("/api/v1/face-clusters/cluster_new/confirm", json={"name": "妈妈"})

    assert response.status_code == 200
    assert response.json()["merged_into"] == "ent_known"
    assert response.json()["entity"]["id"] == "ent_known"


# ---- 拒绝 / 合并 / 拆分 -----------------------------------------------------------


def test_rejecting_a_confirmed_person_is_409(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")
    add_cluster(identity_client, "cluster_1", status="confirmed", entity_id="ent_1", member_count=1)

    response = identity_client.post("/api/v1/face-clusters/cluster_1/reject")

    assert response.status_code == 409
    assert _error(response) == ("conflict", "confirmed person clusters cannot be rejected")


def test_rejecting_a_pending_cluster_reports_the_deleted_candidate(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=1)

    response = identity_client.post("/api/v1/face-clusters/cluster_1/reject")

    assert response.status_code == 200
    assert response.json() == {"cluster_id": "cluster_1", "rejected_entity_id": "ent_1"}


def test_merge_without_both_ids_is_400(identity_client: TestClient):
    response = identity_client.post(
        "/api/v1/face-clusters/merge", json={"target_cluster_id": "cluster_1", "source_cluster_id": ""}
    )

    assert response.status_code == 400
    assert _error(response)[0] == "invalid_request"


def test_merging_two_confirmed_people_is_409(identity_client: TestClient):
    add_entity(identity_client, "ent_a", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_b", name="爸爸", status="confirmed")
    add_cluster(identity_client, "cluster_a", status="confirmed", entity_id="ent_a", member_count=1)
    add_cluster(identity_client, "cluster_b", status="confirmed", entity_id="ent_b", member_count=1)

    response = identity_client.post(
        "/api/v1/face-clusters/merge",
        json={"target_cluster_id": "cluster_a", "source_cluster_id": "cluster_b"},
    )

    assert response.status_code == 409
    assert _error(response)[0] == "conflict"


def test_merge_moves_the_faces_and_retires_the_source(identity_client: TestClient):
    add_entity(identity_client, "ent_a", name="待确认人物簇 · cluster_a")
    add_entity(identity_client, "ent_b", name="待确认人物簇 · cluster_b")
    add_cluster(identity_client, "cluster_a", entity_id="ent_a", member_count=1)
    add_cluster(identity_client, "cluster_b", entity_id="ent_b", member_count=1)
    add_instance(identity_client, "face_a", cluster_id="cluster_a")
    add_instance(identity_client, "face_b", cluster_id="cluster_b")

    response = identity_client.post(
        "/api/v1/face-clusters/merge",
        json={"target_cluster_id": "cluster_a", "source_cluster_id": "cluster_b"},
    )

    assert response.status_code == 200
    assert response.json()["member_count"] == 2
    with _db(identity_client).connect() as connection:
        row = connection.execute(
            "SELECT status, member_count FROM face_clusters WHERE id = 'cluster_b'"
        ).fetchone()
    assert (row["status"], row["member_count"]) == ("rejected", 0)


def test_merging_two_confirmed_people_keeps_the_target_name(identity_client: TestClient):
    """人物级合并：簇级合并拒绝的「两个已确认的人」，在这里由用户决定。"""
    add_entity(identity_client, "ent_a", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_b", name="母亲", status="confirmed")
    add_cluster(identity_client, "cluster_a", status="confirmed", entity_id="ent_a", member_count=1)
    add_cluster(identity_client, "cluster_b", status="confirmed", entity_id="ent_b", member_count=1)
    add_instance(identity_client, "face_a", cluster_id="cluster_a")
    add_instance(identity_client, "face_b", cluster_id="cluster_b")

    response = identity_client.post(
        "/api/v1/people/merge", json={"target_person_id": "ent_a", "source_person_ids": ["ent_b"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entity"]["id"] == "ent_a"
    assert body["entity"]["canonical_name"] == "妈妈"
    assert body["entity"]["aliases"] == ["母亲"]
    assert body["merged_person_ids"] == ["ent_b"]
    assert body["moved_face_count"] == 1
    # 来源不再出现在人物列表里。
    people = identity_client.get("/api/v1/people", params={"scope_id": HOME}).json()["people"]
    assert [item["id"] for item in people] == ["ent_a"]


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"target_person_id": "ent_a", "source_person_ids": []}, 400, "invalid_request"),
        ({"target_person_id": "ent_a", "source_person_ids": ["ent_a"]}, 400, "invalid_request"),
        ({"target_person_id": "ent_a", "source_person_ids": ["ent_missing"]}, 404, "not_found"),
        ({"target_person_id": "ent_a", "source_person_ids": ["ent_hidden"]}, 409, "conflict"),
    ],
)
def test_merge_people_error_codes(identity_client: TestClient, payload: dict, status: int, code: str):
    add_entity(identity_client, "ent_a", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_hidden", name="别处的人", status="confirmed", scope_id=HIDDEN)

    response = identity_client.post("/api/v1/people/merge", json=payload)

    assert response.status_code == status
    assert _error(response)[0] == code


def test_splitting_a_foreign_face_is_404(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=1)
    add_cluster(identity_client, "cluster_2", member_count=1)
    add_instance(identity_client, "face_2", cluster_id="cluster_2")

    response = identity_client.post(
        "/api/v1/face-clusters/cluster_1/split", json={"face_instance_id": "face_2"}
    )

    assert response.status_code == 404
    assert _error(response) == ("not_found", "face instance not found in cluster")


def test_split_returns_the_new_cluster_with_its_own_pending_person(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=2)
    add_instance(identity_client, "face_1", cluster_id="cluster_1")
    add_instance(identity_client, "face_2", cluster_id="cluster_1")

    response = identity_client.post(
        "/api/v1/face-clusters/cluster_1/split", json={"face_instance_id": "face_2"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] != "cluster_1"
    assert body["member_count"] == 1
    assert body["canonical_name"] == PENDING_CLUSTER_NAME
    assert body["samples"][0]["face_instance_id"] == "face_2"


def test_split_without_a_face_id_is_400(identity_client: TestClient):
    response = identity_client.post("/api/v1/face-clusters/cluster_1/split", json={"face_instance_id": ""})

    assert response.status_code == 400
    assert _error(response)[0] == "invalid_request"


# ---- 人物 ------------------------------------------------------------------------


def test_people_list_shows_the_internal_name_of_a_pending_candidate(identity_client: TestClient):
    add_entity(identity_client, "ent_pending", name="待确认人物簇 · cluster_1", summary="由 buffalo_l 生成")
    add_entity(identity_client, "ent_confirmed", name="妈妈", status="confirmed", family_role="母亲")

    people = identity_client.get("/api/v1/people").json()["people"]

    by_id = {item["id"]: item for item in people}
    assert by_id["ent_pending"]["canonical_name"] == PENDING_CLUSTER_NAME
    assert by_id["ent_pending"]["confirmed"] is False
    assert by_id["ent_confirmed"]["canonical_name"] == "妈妈"
    assert by_id["ent_confirmed"]["family_role"] == "母亲"
    assert by_id["ent_confirmed"]["confirmed"] is True


def test_people_list_hides_a_hidden_space(identity_client: TestClient):
    add_entity(identity_client, "ent_hidden", name="评测人物", scope_id=HIDDEN)

    assert identity_client.get("/api/v1/people").json()["people"] == []


def test_person_detail_carries_aliases_avatar_and_clusters(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed", family_role="母亲")
    add_cluster(identity_client, "cluster_1", status="confirmed", entity_id="ent_1", member_count=1)
    add_instance(identity_client, "face_1", cluster_id="cluster_1", detection_confidence=0.7)
    add_instance(identity_client, "face_2", cluster_id="cluster_1", detection_confidence=0.95)
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
            " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
            " VALUES ('prop_1', 'ent_1', 'aliases', '[\"老妈\"]', 'user', 1.0, 'active', '[]', 1, ?, ?)",
            (NOW, NOW),
        )

    body = identity_client.get("/api/v1/people/ent_1").json()

    assert body["entity"]["aliases"] == ["老妈"]
    assert body["avatar_face_instance_id"] == "face_2"
    assert [item["id"] for item in body["clusters"]] == ["cluster_1"]


def test_person_detail_of_an_unknown_id_is_404(identity_client: TestClient):
    response = identity_client.get("/api/v1/people/ent_nope")

    assert response.status_code == 404
    assert _error(response) == ("not_found", "person not found")


def test_rename_folds_the_old_name_into_aliases(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.patch("/api/v1/people/ent_1/name", json={"name": "母亲"})

    assert response.status_code == 200
    assert response.json()["entity"]["canonical_name"] == "母亲"
    assert response.json()["aliases"] == ["妈妈"]


def test_rename_with_a_blank_name_is_400(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.patch("/api/v1/people/ent_1/name", json={"name": "  "})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "person name is required")


def test_renaming_a_pending_candidate_shows_its_name_without_confirming_identity(identity_client: TestClient):
    """用户命名后显示新名字，身份仍保持未确认。"""
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")

    response = identity_client.patch("/api/v1/people/ent_1/name", json={"name": "妈妈"})

    assert response.status_code == 200
    assert response.json()["entity"]["canonical_name"] == "妈妈"
    assert response.json()["entity"]["confirmed"] is False
    with _db(identity_client).connect() as connection:
        row = connection.execute(
            "SELECT canonical_name, name_state, status FROM entities WHERE id = 'ent_1'"
        ).fetchone()
    assert (row["canonical_name"], row["name_state"], row["status"]) == ("妈妈", "confirmed", "pending")


def test_rename_of_an_unknown_person_is_404(identity_client: TestClient):
    response = identity_client.patch("/api/v1/people/ent_nope/name", json={"name": "妈妈"})

    assert response.status_code == 404
    assert _error(response)[0] == "not_found"


def test_batch_confirm_reports_each_item_and_keeps_going(identity_client: TestClient):
    """一件失败不影响其余：批量确认的输入来自用户逐个点选，整批回滚会丢掉其余选择。"""
    add_entity(identity_client, "ent_ok", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_ok", entity_id="ent_ok", member_count=1)

    response = identity_client.post(
        "/api/v1/people/batch-confirm",
        json={
            "items": [
                {"person_id": "ent_ok", "name": "妈妈", "family_role": "母亲"},
                {"person_id": "ent_missing", "name": "爸爸"},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["confirmed"] == [
        {
            "person_id": "ent_ok",
            "entity_id": "ent_ok",
            "name": "妈妈",
            "family_role": "母亲",
            "merged": False,
        }
    ]
    assert body["failed"] == [{"person_id": "ent_missing", "name": "爸爸", "error": "person not found"}]


def test_batch_confirm_with_no_items_is_422(identity_client: TestClient):
    """空 items 是**请求格式**错（pydantic 的 min_length），不是领域判据。"""
    response = identity_client.post("/api/v1/people/batch-confirm", json={"items": []})

    assert response.status_code == 422


# ---- 人物目录（把容器里那个字段当作被测对象） --------------------------------------


def test_the_container_directory_finds_a_confirmed_person(identity_client: TestClient):
    """S3 的 ``NoIdentityDirectory`` 常驻 ``None``；S5 换上真实实现后这里必须查得到。

    **S7c 起 ``family_role`` 不在档案里了**：它是 2.0 在 S3 的占位，1.0 的 digest
    根本不返回这个键——1.0 返回的是 ``membership``（家庭图谱那条轴）。两套口径在
    1.0 的 298 对配对里一致率 0，不是一回事。``entities.family_role`` 列本身还在，
    ``GET /api/v1/people/{id}`` 照旧返回它。
    """
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed", family_role="母亲")
    add_entity(identity_client, "ent_pending", name="待确认人物簇 · cluster_1")

    directory = identity_client.app.state.container.person_directory

    profile = directory.lookup("妈妈", HOME)
    assert profile is not None
    assert (profile.name, profile.confirmed) == ("妈妈", True)
    # 没有家庭图谱行 → 归属按 1.0 ``db.py:2875`` 的 ``or "unknown"`` 报 unknown，
    # **不是**空串、也不是「查不到这个人」。
    assert profile.membership == "unknown"
    assert profile.membership_source == ""
    assert profile.relationships == ()
    assert directory.lookup("待确认人物簇 · cluster_1", HOME) is None
    assert directory.lookup("", HOME) is None


def test_the_directory_reports_membership_portrait_and_relationships(identity_client: TestClient):
    """S7c 的正题：家庭图谱里那三张表要能经人物目录答出来。

    语料直接走容器里那个仓储的**写方法**（不是手插 SQL）——那样夹具与生产同一个口径，
    仓储的写语义变了这里也跟着变，不会造出一个生产里不存在的形状。
    """
    add_entity(identity_client, "ent_mom", name="妈妈", status="confirmed")
    family = identity_client.app.state.container.family_graph_repository
    family.set_membership(HOME, "ent_mom", "family", source="user_override")
    family.write_portrait(HOME, "ent_mom", "家庭归属：family；近期记忆：一起在公园野餐。", source="model")

    profile = identity_client.app.state.container.person_directory.lookup("妈妈", HOME)

    assert profile is not None
    assert profile.membership == "family"
    assert profile.membership_source == "user_override"
    assert profile.summary == "家庭归属：family；近期记忆：一起在公园野餐。"


def test_a_relationship_appears_on_both_ends_with_its_own_predicate(identity_client: TestClient):
    """**一段关系在库里是两行，而两个人各自该看到自己那一面。**

    ``set_relationship`` 一次写正向 + 反向（``母亲`` 与 ``儿子``）。所以：
    - 查妈妈 → ``母亲``（她是对方的母亲），恰好**一条**，不是两条；
    - 查小明 → ``儿子``（他是对方的儿子），也恰好一条。

    **这一条同时钉住 1.0 的一个缺陷在 2.0 不成立。** 1.0 ``db.py:2852-2858`` 只收
    ``subject == person`` 的出边，而那边一段关系只按一个方向存——于是查「儿子」的
    档案拿不到母亲。2.0 双向存储，同一个过滤拿到的是完整的关系集。上面第二条断言
    就是那个反例：把双向存储改成单向，它会红。

    定点变异：去掉 ``_relationships`` 里那句 ``subject_entity_id != entity_id`` 的
    过滤 → 妈妈会拿到两条（``母亲`` + ``儿子``），第一条断言红。
    """
    add_entity(identity_client, "ent_mom", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_son", name="小明", status="confirmed")
    family = identity_client.app.state.container.family_graph_repository
    family.set_relationship(HOME, "ent_mom", "母亲", "ent_son", "儿子", source="model")

    directory = identity_client.app.state.container.person_directory

    mom = directory.lookup("妈妈", HOME)
    assert mom is not None
    assert [(item.predicate, item.other_name, item.source) for item in mom.relationships] == [
        ("母亲", "小明", "model")
    ]

    son = directory.lookup("小明", HOME)
    assert son is not None
    assert [(item.predicate, item.other_name) for item in son.relationships] == [("儿子", "妈妈")]


def test_the_directory_answers_by_alias(identity_client: TestClient):
    """按别名问到时，答案里的名字是**本名**——否则模型会把一个人当成两个人。"""
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed", family_role="母亲")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
            " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
            " VALUES ('prop_1', 'ent_1', 'aliases', '[\"老妈\"]', 'user', 1.0, 'active', '[]', 1, ?, ?)",
            (NOW, NOW),
        )

    profile = identity_client.app.state.container.person_directory.lookup("老妈", HOME)

    assert profile is not None
    assert profile.name == "妈妈"


def test_the_directory_is_the_entity_backed_implementation(client: TestClient):
    """装配点换过人了：S3 那个永远返回 ``None`` 的适配器已经不在了。"""
    assert isinstance(client.app.state.container.person_directory, EntityPersonDirectory)


# ---- 人脸裁剪 --------------------------------------------------------------------


def _write_jpeg(path: Path, size: tuple[int, int] = (640, 480)) -> None:
    from PIL import Image

    Image.new("RGB", size, (200, 120, 60)).save(path, format="JPEG")


def test_crop_returns_a_jpeg_with_private_cache_headers(identity_client: TestClient, tmp_path: Path):
    _write_jpeg(tmp_path / "asset_1.jpg")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET path = ? WHERE id = 'asset_1'", (str(tmp_path / "asset_1.jpg"),)
        )
    add_instance(identity_client, "face_1", cluster_id=None, bbox=(10.0, 20.0, 210.0, 220.0))

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"
    from PIL import Image

    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (200, 200)


def test_crop_of_an_unknown_instance_is_404(identity_client: TestClient):
    response = identity_client.get("/api/v1/face-instances/face_nope/crop")

    assert response.status_code == 404
    assert _error(response) == ("not_found", "face instance not found")


def test_crop_of_a_missing_file_is_422(identity_client: TestClient):
    """资产行在、磁盘上的文件不在：请求语法没问题，内容处理不了。"""
    add_instance(identity_client, "face_1", cluster_id=None)

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 422
    assert _error(response)[0] == "unprocessable"


def test_crop_of_a_degenerate_box_is_422(identity_client: TestClient, tmp_path: Path):
    _write_jpeg(tmp_path / "asset_1.jpg")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET path = ? WHERE id = 'asset_1'", (str(tmp_path / "asset_1.jpg"),)
        )
    # 夹到图内之后是空框：右 0 <= 左 0。
    add_instance(identity_client, "face_1", cluster_id=None, bbox=(0.0, 0.0, -50.0, -50.0))

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 422
    assert _error(response)[0] == "unprocessable"


def test_crop_with_a_short_bbox_is_422(identity_client: TestClient, tmp_path: Path):
    _write_jpeg(tmp_path / "asset_1.jpg")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET path = ? WHERE id = 'asset_1'", (str(tmp_path / "asset_1.jpg"),)
        )
    add_instance(identity_client, "face_1", cluster_id=None, bbox=(1.0, 2.0))

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 422
    assert _error(response) == ("unprocessable", "face crop is unavailable: invalid face bounding box")


# ---- 身份预置：按人脸实例 ----------------------------------------------------------
#
# 裁决二新增的入口。契约层的两件事：**不碰检测器**（所以这里能跑，而按照片那条路要
# insightface 权重）与**不存在的实例是 404 而不是 422**。


class NoFaceDetector:
    """图里没有人脸。上传那条路会调它，用真检测器要 insightface 权重。"""

    def detect(self, path: str):
        return []

    def detect_identity_photo(self, path: str):
        return []


class ExplodingDetector:
    """真被调上就说明实例那条路又去「挑照片里最好的脸」了。"""

    def detect(self, path: str):
        raise AssertionError(f"按实例预置不该调用检测器: {path}")

    def detect_identity_photo(self, path: str):
        raise AssertionError(f"按实例预置不该调用检测器: {path}")


def add_embedded_instance(
    client: TestClient, instance_id: str, *, embedding: list[float], scope_id: str = HOME
) -> None:
    """一条带**真向量**的实例——``add_instance`` 落的是空向量，那是「没有向量」的意思。"""
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json,"
            "  detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            "  embedding_version, embedding_quality_signal, validity, area_ratio, sharpness,"
            "  created_at)"
            " VALUES (?, 'asset_1', 'obs_1', NULL, '[10.0, 10.0, 110.0, 110.0]', ?,"
            "  0.95, 0.88, '[]', 'frontal', 'buffalo_l', 'buffalo_l', 0.4, 'verified',"
            "  0.1, 0.3, ?)",
            (instance_id, json.dumps(embedding), NOW),
        )


def test_seeding_by_a_stored_face_instance_never_calls_the_detector(identity_client: TestClient):
    """实例入口把库里那一行**原样**当作身份样本。

    把检测器换成「一调就炸」的替身，这条仍然是 201——这正是它与照片那条路的区别，
    也是它能在契约测试里跑起来的原因（那条路要 insightface 权重）。
    """
    import dataclasses

    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])
    identity_client.app.state.container = dataclasses.replace(
        identity_client.app.state.container, face_detector=ExplodingDetector()
    )

    response = identity_client.post(
        "/api/v1/people/seed",
        data={"name": "妈妈", "family_role": "母亲", "scope_id": HOME, "face_instance_ids": "fi_mom"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "妈妈"
    assert response.json()["face_count"] == 1
    assert response.json()["photos_without_faces"] == 0
    cluster = identity_client.get("/api/v1/face-clusters").json()["clusters"][0]
    assert cluster["status"] == "confirmed"
    assert cluster["member_count"] == 1
    assert cluster["canonical_name"] == "妈妈"


def test_seeding_the_same_photo_twice_does_not_collide(identity_client, tmp_path):
    """**同一张预置照片落两次不能炸。**（2026-10-10 修）

    这条路原来写的是 ``asset_id = f"asset_{sha256(content)[:12]}"``——用**内容哈希**当 id，
    而 ``assets.id`` 是**全局主键、不带 scope**。于是同一张预置照片只要在**任何一个** scope
    里落过一次，之后每一次预置都撞 ``UNIQUE constraint failed: assets.id``，路由回 500。

    实测的后果：`faceid_1.jpg` 早在 `PhotoBench-20261007-141043` 那一轮就落库了，
    **从那以后每一轮评测的 8 个人一个都没种进去**（`seeded_count: 0, failed_count: 8`）
    ——而「这一轮里的所有人都不存在」在读数上只表现为「人物类题答不对」。

    口径改成与导入路径一致（``import_assets.py:325``）：随机 id + 按 (scope, 哈希) 去重。
    """
    import dataclasses
    import hashlib

    from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation

    payload = "同一张脸".encode()
    digest = hashlib.sha256(payload).hexdigest()
    db = _db(identity_client)
    container = identity_client.app.state.container
    existing = container.asset_repository.add(
        Asset(
            id="asset_已经在库里",
            scope_id=HOME,
            file_name="faceid_1.jpg",
            media_type=MediaType.IMAGE,
            path=str(tmp_path / "faceid_1.jpg"),
            mime_type="image/jpeg",
            size_bytes=len(payload),
            status=AssetStatus.PROCESSED,
            content_sha256=digest,
            captured_at=None,
            captured_location=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    del existing
    container.observation_repository.add(
        Observation(
            id="obs_已经在库里",
            scope_id=HOME,
            asset_id="asset_已经在库里",
            source_type="seed_identity",
            captured_at=None,
            confidence=0.0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    # 上传路径**会**过检测器（与按实例那条路不同）。这里给一个「图里没有人脸」的桩：
    # 本用例要验的是**资产那一格不撞**，不是人脸，检测结果返回空不影响它——
    # 而 ``_store_seed_photo`` 落在检测**之前**，正是它抛的那个 IntegrityError。
    identity_client.app.state.container = dataclasses.replace(container, face_detector=NoFaceDetector())

    response = identity_client.post(
        "/api/v1/people/seed",
        data={"name": "妈妈", "scope_id": HOME},
        files={"files": ("faceid_1.jpg", payload, "image/jpeg")},
    )

    assert response.status_code != 500, response.text
    # **没有落第二份**：同一个 scope 里同样的字节就是同一个资产。
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM assets WHERE scope_id = ? AND content_sha256 = ?", (HOME, digest)
        ).fetchall()
    assert [row["id"] for row in rows] == ["asset_已经在库里"]


def test_an_unknown_face_instance_is_a_404_not_a_422(identity_client: TestClient):
    """指向一个不存在的对象（404）与「图里没有人脸」（422）是两件事。

    合并成一个码，调用方就分不清该换 id 还是该换图。
    """
    response = identity_client.post(
        "/api/v1/people/seed", data={"name": "妈妈", "face_instance_ids": "fi_ghost"}
    )

    assert response.status_code == 404, response.text
    assert _error(response) == ("not_found", "face instance not found: fi_ghost")


def test_seeding_with_neither_files_nor_instances_is_a_400(identity_client: TestClient):
    """1.0 的 ``files`` 是必填，所以这条在 1.0 不存在；两条入口之后它是新的边界。"""
    response = identity_client.post("/api/v1/people/seed", data={"name": "妈妈"})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "files or face_instance_ids is required")


def test_an_empty_name_is_still_rejected_before_either_entrance(identity_client: TestClient):
    """1.0 ``app.py:2321`` 的 400，两条入口共用。"""
    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])

    response = identity_client.post(
        "/api/v1/people/seed", data={"name": "   ", "face_instance_ids": "fi_mom"}
    )

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "name is required")


# ---- 预置建空间行（第 104 条的裁决，2026-09-24）-----------------------------------


def add_bare_cluster(client: TestClient, cluster_id: str, *, scope_id: str) -> None:
    """一行**什么都没有**的簇，直接插——绕开预置路由，单独钉「可见性由空间行决定」。"""
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, member_count, confidence, created_at, updated_at)"
            " VALUES (?, ?, 'pending', 1, 1.0, ?, ?)",
            (cluster_id, scope_id, NOW, NOW),
        )


def test_seeding_into_a_fresh_scope_registers_the_space_so_the_cluster_is_visible(
    identity_client: TestClient,
):
    """预置也要建空间行——1.0 的预置走 ``create_asset``，那里面就有 ensure（``db.py:1462``）。

    第 104 条：2.0 的 ``_store_seed_photo`` 直接 ``asset_repository.add``，于是往一个全新
    scope 预置身份、又不导入任何照片时，那个簇在审阅页里看不见。1.0 是建的
    （``app.py:2337``），这里补成一致。

    **只走 HTTP。** 同构构造的用例证明不了生产装配（第 107 条 M2）：装配点漏了
    ``container.spaces``，自己传协作者的集成用例照样是绿的。
    """
    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])

    response = identity_client.post(
        "/api/v1/people/seed",
        data={"name": "妈妈", "scope_id": FRESH, "face_instance_ids": "fi_mom"},
    )

    assert response.status_code == 201, response.text
    space = identity_client.app.state.container.spaces.get(FRESH)
    assert space is not None
    assert (space.id, space.name, space.kind, space.status) == (FRESH, FRESH, "household", "active")
    body = identity_client.get("/api/v1/face-clusters", params={"scope_id": FRESH}).json()
    assert [item["id"] for item in body["clusters"]] == [response.json()["cluster_id"]]


def test_a_rejected_seed_leaves_no_space_row(identity_client: TestClient):
    """**一个什么都没建的请求不留痕**——ensure 排在所有校验之后（第 114 条）。

    这里钉 404（``face_instance_ids`` 指向不存在的实例，``SeedIdentity.execute`` 第一件事
    就把它判掉）。原实现把 ``ensure`` 放在路由开头（照 1.0 那行的**单行顺序**），于是一个
    被拒的请求留下一行空空间：这个 scope 从此「登记过」，而那次请求一件东西都没建。

    位置是按 1.0 的**流程位置**定的：1.0 的 ``create_asset`` 把「建空间 + 建资产」放在同一个
    流程里，所以一个什么都没建的请求根本走不到那一行；照着单行顺序抄才会把它挪到校验之前。

    **它红在位置、不在「有没有调过 ensure」**：把那一行挪回路由开头（M7），空间行就回来了，
    这条变红，而正向那条 ``test_seeding_into_a_fresh_scope_...`` 仍然绿——两条一起才把位置
    钉住（只留正向的话，挪回开头也全绿）。
    """
    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])

    response = identity_client.post(
        "/api/v1/people/seed",
        data={"name": "妈妈", "scope_id": FRESH, "face_instance_ids": "fi_ghost"},
    )

    assert response.status_code == 404, response.text
    assert identity_client.app.state.container.spaces.get(FRESH) is None
    with _db(identity_client).connect() as connection:
        clusters = connection.execute(
            "SELECT COUNT(*) FROM face_clusters WHERE scope_id = ?", (FRESH,)
        ).fetchone()
    assert clusters[0] == 0


def test_a_cluster_in_a_scope_without_a_space_row_is_invisible_on_the_review_page(
    client: TestClient,
):
    """反向那一半：同一个簇，只差一行空间行，``/face-clusters`` 就从看不见变成看得见。

    ``list_clusters`` 里的 ``memory_spaces`` 约束是**有意为之**（``faces.py:139``：内连 +
    ``ms.include_in_people = 1``），所以「少一行空间」在审阅页上的表现是「这个簇不存在」，
    「空」与「没有」从此被混成同一件事。``/people`` 不受影响，它用的是子查询（第 103 条）。

    定点变异量过这道门禁的边界：**把整条约束去掉**（M6b）这条立刻变红；只把 INNER JOIN
    换成 LEFT JOIN（M6）**不会**——``include_in_people = 1`` 那个谓词自己就够把没登记过的
    scope 挡掉（``NULL = 1`` 不成立）。两处合起来才是那条约束。

    它钉的是物理原因；预置路由自己那条反向门禁是定点变异（删掉 ``seed_person_identity``
    里的 ``container.spaces.ensure``（M5）→ 上面那条用例变红）。
    """
    add_bare_cluster(client, "cluster_orphan", scope_id=FRESH)

    assert client.get("/api/v1/face-clusters", params={"scope_id": FRESH}).json() == {"clusters": []}

    client.app.state.container.spaces.ensure(FRESH, FRESH)

    body = client.get("/api/v1/face-clusters", params={"scope_id": FRESH}).json()
    assert [item["id"] for item in body["clusters"]] == ["cluster_orphan"]


# ---- 实体属性 --------------------------------------------------------------------


def test_the_people_route_only_shows_people(identity_client: TestClient):
    """``/people`` 的类型过滤在**路由层**，不在仓储里——1.0 ``app.py:1514`` 也是这么分的。

    仓储给的现在是「全部实体」（``list_entities`` 没有 ``entity_type`` 参数）。这条钉的是
    路由把它收窄回人物。

    定点变异：删掉 ``routes/identity.py`` 里那行
    ``people = [item for item in values if item.entity_type == ENTITY_TYPE_PERSON]``，
    这条立刻变红——而 ``/people`` 的**其余用例全都不会**，因为它们的 fixture 里一个非人物
    实体都没有。这就是这条用例非存在不可的理由：过滤被搬了个位置，原有用例看不见。
    """
    add_entity(identity_client, "ent_person", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    people = identity_client.get("/api/v1/people").json()["people"]

    assert [item["id"] for item in people] == ["ent_person"]


def test_setting_a_property_returns_the_written_row(identity_client: TestClient):
    """用户写属性：新行 ``source='user'`` / ``confidence=1`` / ``status='active'`` / ``revision=1``。

    1.0 ``db.py:4762-4768`` 把这三个值**写死在 SQL 字面量里**，所以它们是契约不是默认值。
    响应体**没有外层包裹**（1.0 ``app.py:1962`` 直接返回 ``_property_row`` 那个 dict）。
    """
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.put(
        "/api/v1/entities/ent_1/properties/private_flag",
        json={"value": True, "evidence_ids": ["obs_1", "obs_1"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entity_id"] == "ent_1"
    assert body["property_key"] == "private_flag"
    assert body["value"] is True
    assert body["source"] == "user"
    assert body["confidence"] == 1
    assert body["status"] == "active"
    assert body["revision"] == 1
    assert body["supersedes_property_id"] is None
    # 1.0 ``db.py:4753`` 的 ``list(dict.fromkeys(evidence_ids or []))``：去重且保序。
    assert body["evidence_ids"] == ["obs_1"]


def test_setting_a_property_twice_supersedes_the_older_one(identity_client: TestClient):
    """第二次写**不覆盖**第一行，而是把旧的转 ``superseded`` 再插一行 ``revision+1``。

    定点变异：去掉 ``set_property`` 里那条
    ``UPDATE entity_properties SET status = 'superseded' ...``，
    ``revision`` 与 ``supersedes_property_id`` 两条断言**仍然会过**（它们只看新行，
    而新行的 revision 是照着旧行算的、与旧行状态无关）——只有「旧行变成 superseded」
    那一条会红。所以那条断言才是这条用例的主体，前面两条只是铺路。
    """
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")
    first = identity_client.put("/api/v1/entities/ent_1/properties/title", json={"value": "甲"}).json()

    second = identity_client.put("/api/v1/entities/ent_1/properties/title", json={"value": "乙"}).json()

    assert second["revision"] == 2
    assert second["supersedes_property_id"] == first["id"]
    assert second["value"] == "乙"
    with _db(identity_client).connect() as connection:
        old = connection.execute(
            "SELECT status FROM entity_properties WHERE id = ?", (first["id"],)
        ).fetchone()
    assert old["status"] == "superseded"


def test_setting_a_property_on_an_unknown_entity_is_404(identity_client: TestClient):
    """``KeyError`` → 404 是**路由翻译**的，仓储只管抛。

    定点变异：删掉路由里的 ``except KeyError``，这条会变成 500。
    """
    response = identity_client.put("/api/v1/entities/ent_nope/properties/title", json={"value": 1})

    assert response.status_code == 404
    assert _error(response) == ("not_found", "entity not found")


def test_an_empty_property_key_is_400(identity_client: TestClient):
    """键 trim 后为空是 **400 不是 422**：判据在仓储（``_clean_key``），不在路径声明。

    消息逐字是异常自己的话（1.0 ``app.py:1966`` 的 ``detail=str(error)``），
    所以路由不能改写它。

    定点变异：删掉路由里的 ``except ValueError``，这条会变成 500。
    """
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.put("/api/v1/entities/ent_1/properties/%20", json={"value": 1})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "property_key is required")


def test_a_body_without_a_value_is_accepted_and_writes_null(identity_client: TestClient):
    """``{}`` 是**合法请求**，写进去一个 ``null``。

    1.0 的路由收的是裸 ``dict``（``app.py:1960``），``payload.get("value")`` 对缺键返回
    ``None``，于是它写入**成功**。把 ``value`` 写成必填字段会让这个请求变成 422——
    同一个产品行为换了一个码。``evidence_ids: null`` 同理（1.0 是 ``... or []``，
    所以它等同于没给，而不是报错）。
    """
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.put("/api/v1/entities/ent_1/properties/note", json={"evidence_ids": None})

    assert response.status_code == 200
    assert response.json()["value"] is None
    assert response.json()["evidence_ids"] == []


# ---- 实体目录 --------------------------------------------------------------------


def _add_property(client: TestClient, entity_id: str, key: str, value: object) -> None:
    """直接落一行属性。走 ``PUT /entities/.../properties/...`` 也可以，但那样测的是
    另一个端点的写入路径；这里要的是「库里有这么一行时列表怎么读」。"""
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
            " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'user', 1.0, 'active', '[]', 1, ?, ?)",
            (f"prop_{entity_id}_{key}", entity_id, key, json.dumps(value), NOW, NOW),
        )


def test_the_entity_catalogue_hides_people_unless_asked(identity_client: TestClient):
    """``includePeople`` 默认 ``false``——这是 1.0 的 API 契约（``app.py:1861``）。

    1.0 自己的前端**永远传 ``true``**（``src/api.js:42`` 写死在请求里），所以这个默认值
    在生产里是死路径。**仍然照搬**——它是契约的一部分，但也不该为它编造消费场景。

    定点变异：把默认值改成 ``True``，这条的前半变红。
    """
    add_entity(identity_client, "ent_person", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    default = identity_client.get("/api/v1/entities").json()["entities"]
    with_people = identity_client.get("/api/v1/entities", params={"includePeople": "true"}).json()["entities"]

    assert [item["id"] for item in default] == ["ent_place"]
    assert {item["id"] for item in with_people} == {"ent_person", "ent_place"}


def test_a_pending_person_in_the_catalogue_is_an_unnamed_member(identity_client: TestClient):
    """三件事一起做，指 1.0 ``app.py:1863-1867``：换名字、去掉角色、**换说明**。

    说明那一格是本切片补上的：库里存的是「由 buffalo_l embedding 生成，等待用户确认」，
    接口上给的是「由人脸聚类生成，等待用户确认」——API 上不该出现模型代号。
    ``/people`` 走的是同一个块，所以断言保持一致。

    定点变异：把 ``summary`` 那一行去掉，这条变红。
    """
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1", summary="由 buffalo_l 生成")

    entity = identity_client.get("/api/v1/entities", params={"includePeople": "true"}).json()["entities"][0]

    assert entity["canonical_name"] == PENDING_CLUSTER_NAME
    assert entity["family_role"] is None
    assert entity["summary"] == "由人脸聚类生成，等待用户确认"


def test_the_people_route_shows_the_same_summary_as_the_catalogue(identity_client: TestClient):
    """两个端点对同一个 pending 人物必须给同一句话。

    这条单独存在是因为 ``summary`` 的改写发生在 ``_person_payload`` 与 ``_entity_summary``
    两个地方——**只改一处就能让它们分叉**，而两边各自的用例都不会红。
    """
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1", summary="由 buffalo_l 生成")

    person = identity_client.get("/api/v1/people").json()["people"][0]
    entity = identity_client.get("/api/v1/entities", params={"includePeople": "true"}).json()["entities"][0]

    assert person["summary"] == entity["summary"] == "由人脸聚类生成，等待用户确认"


def test_a_private_place_gives_its_alias_instead_of_its_name(identity_client: TestClient):
    """用户标了私密的地点，目录上给的是别名，并带 ``private: true``。

    定点变异：把 ``private_place`` 的调用去掉，这条变红（名字会原样透出去——
    这正是私密标记要挡的事）。
    """
    add_entity(identity_client, "ent_place", name="某某小区", status="confirmed", entity_type="place")
    _add_property(identity_client, "ent_place", "private_flag", True)
    _add_property(identity_client, "ent_place", "alias", "家")

    entity = identity_client.get("/api/v1/entities").json()["entities"][0]

    assert entity["canonical_name"] == "家"
    assert entity["private"] is True


def test_a_place_without_the_flag_carries_private_false(identity_client: TestClient):
    """非私密地点给 ``private: false`` 而不是省掉这个键——1.0 也是显式给 ``False``。"""
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    entity = identity_client.get("/api/v1/entities").json()["entities"][0]

    assert entity["canonical_name"] == "西湖"
    assert entity["private"] is False


def test_an_integer_one_does_not_make_a_place_private(identity_client: TestClient):
    """``private_flag`` 是 JSON 里的任意类型，1.0 判的是 ``is True``。

    这条走完整链路（库 → 仓储 → 纯函数 → 响应）再验一次那个边界：属性值是整数 ``1``
    时名字**不该**被换掉。真值判断会让它通过。
    """
    add_entity(identity_client, "ent_place", name="某某小区", status="confirmed", entity_type="place")
    _add_property(identity_client, "ent_place", "private_flag", 1)

    entity = identity_client.get("/api/v1/entities").json()["entities"][0]

    assert entity["canonical_name"] == "某某小区"
    assert entity["private"] is False


def test_the_catalogue_carries_the_derived_counts(identity_client: TestClient):
    """派生字段真的进了响应：簇数、照片数、提及数、证据数、封面。

    定点变异：把 ``decorate_entities`` 的调用去掉（全部用零值），这条变红。
    """
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")
    add_cluster(identity_client, "cluster_1", status="confirmed", entity_id="ent_1", member_count=2)
    add_instance(identity_client, "face_1", cluster_id="cluster_1", detection_confidence=0.8)
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
            " created_at) VALUES ('ent_1', 'obs_1', 0.5, 'observation_extraction', ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, confidence, created_at)"
            " VALUES ('mention_1', 'ent_1', 'obs_1', 0.5, ?)",
            (NOW,),
        )

    entity = identity_client.get("/api/v1/entities", params={"includePeople": "true"}).json()["entities"][0]

    assert entity["cluster_count"] == 1
    assert entity["photo_count"] == 1
    assert entity["mention_count"] == 1
    assert entity["evidence_count"] == 1
    assert entity["avatar_face_instance_id"] == "face_1"
    assert entity["preview_asset_id"] == "asset_1"
    assert entity["preview_media_type"] == "image"


def test_the_catalogue_carries_relationship_count(identity_client: TestClient):
    """``relationship_count`` **S7a · T202 加回来了**；S6 时这条钉的是反方向。

    旧断言的理由是「没有读路径，恒 0 会被读成『这个实体确实没有关系』」——T202 给了
    读路径，理由不再成立，断言随之反过来：**去掉这个键就变红**。
    算得对不对由 ``tests/integration/test_entity_decoration_repository.py`` 的
    ``test_relationship_count_counts_both_ends_and_skips_retracted`` 钉。
    """
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    entity = identity_client.get("/api/v1/entities").json()["entities"][0]

    assert entity["relationship_count"] == 0


def test_a_non_person_is_reviewable_by_evidence_count(identity_client: TestClient):
    """非人物「值不值得看」看证据条数，与簇无关——这条走完整链路再验一次。"""
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    without = identity_client.get("/api/v1/entities").json()["entities"][0]
    assert without["reviewable"] is False
    assert without["single_sample"] is None  # 只对人物有意义

    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
            " created_at) VALUES ('ent_place', 'obs_1', 0.5, 'observation_extraction', ?)",
            (NOW,),
        )

    after = identity_client.get("/api/v1/entities").json()["entities"][0]
    assert after["reviewable"] is True


# ---- 实体详情 --------------------------------------------------------------------


def test_the_entity_detail_carries_the_nine_blocks(identity_client: TestClient):
    """十二个键搬九个，这里钉「九个都在、且都是数组 / 对象而不是 null」。"""
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    body = identity_client.get("/api/v1/entities/ent_place").json()

    assert body["entity"]["canonical_name"] == "西湖"
    assert body["entity"]["entity_type"] == "place"
    assert body["properties"] == []
    assert body["property_history"] == []
    assert body["clusters"] == []
    assert body["facts"] == []
    assert body["events"] == []
    assert body["observations"] == []
    assert body["appearance_evidence"] == []
    assert body["evidence_files"] == []


def test_an_unknown_entity_detail_is_404(identity_client: TestClient):
    """1.0 ``app.py:1951-1952``：``if not value`` → 404 ``entity not found``。"""
    response = identity_client.get("/api/v1/entities/ent_nope")

    assert response.status_code == 404
    assert _error(response) == ("not_found", "entity not found")


def test_a_pending_person_in_the_detail_only_has_its_name_replaced(identity_client: TestClient):
    """1.0 这个端点**只换名字**——它**没有**同时改 ``family_role`` 与 ``summary``，
    而同仓库的 ``/api/people/{id}/profile`` 与 ``/evidence`` 两处都改了。

    **三处不一致是 1.0 的原样**（``app.py:1954-1955`` 对 ``:1545-1549``）。顺手「统一」
    会让这个端点的 ``summary`` 从落库原文变成展示文案。

    定点变异：在 ``_entity_detail_payload`` 里补上 ``family_role`` / ``summary`` 的改写，
    这条变红。
    """
    add_entity(
        identity_client,
        "ent_1",
        name="待确认人物簇 · cluster_1",
        family_role="母亲",
        summary="由 buffalo_l 生成",
    )

    entity = identity_client.get("/api/v1/entities/ent_1").json()["entity"]

    assert entity["canonical_name"] == PENDING_CLUSTER_NAME
    assert entity["family_role"] == "母亲"  # **照搬 1.0：这里不改**
    assert entity["summary"] == "由 buffalo_l 生成"  # **也不改**


def test_a_pending_person_in_the_people_list_does_change_its_role_and_summary(
    identity_client: TestClient,
):
    """对照：``/people`` 那一侧**三件都改**。两条用例一起才说明「不一致是照搬的」，
    而不是「有一边忘了改」。
    """
    add_entity(
        identity_client,
        "ent_1",
        name="待确认人物簇 · cluster_1",
        family_role="母亲",
        summary="由 buffalo_l 生成",
    )

    person = identity_client.get("/api/v1/people").json()["people"][0]

    assert person["canonical_name"] == PENDING_CLUSTER_NAME
    assert person["family_role"] is None
    assert person["summary"] == "由人脸聚类生成，等待用户确认"


def test_the_detail_carries_relationships(identity_client: TestClient):
    """``relationships`` **S7a · T201 补上了**；S6 时这条钉的是反方向。

    旧断言是「键不进响应」，理由是「恒空的占位键读起来像『这个实体确实没有关系』」——
    那条理由成立的前提是**没有读路径**。T201 给了读路径，所以这里是真的读出来的空，
    不是占位，断言随之反过来：**去掉这个键就变红**。
    库里有关系的那些由 ``tests/integration/test_read_entity_detail.py`` 的
    ``test_relationships_cover_both_ends_and_ignore_status`` 钉。
    """
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    body = identity_client.get("/api/v1/entities/ent_place").json()

    assert body["relationships"] == []


def test_the_detail_has_no_profile_or_claims_keys(identity_client: TestClient):
    """``profile`` / ``claims`` 归 T130（``GET /knowledge`` 就是这两张表 + spaces），
    本次不搬。同样不留占位键。
    """
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")

    body = identity_client.get("/api/v1/entities/ent_place").json()

    assert "profile" not in body
    assert "claims" not in body


def test_the_detail_observations_carry_their_asset(identity_client: TestClient):
    """观察块带 asset（1.0 是把 ``assets`` 整行挂上去）。**没有 ``transcript``**——
    那一列属于 S8，2.0 的 ``observations`` 表里根本没有。"""
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
            " created_at) VALUES ('ent_place', 'obs_1', 0.5, 'observation_extraction', ?)",
            (NOW,),
        )

    observation = identity_client.get("/api/v1/entities/ent_place").json()["observations"][0]

    assert observation["id"] == "obs_1"
    assert observation["asset"]["file_name"] == "asset_1.jpg"
    assert observation["asset"]["media_type"] == "image"
    assert "transcript" not in observation


def test_the_detail_properties_split_current_from_history(identity_client: TestClient):
    """``properties`` 排掉 superseded，``property_history`` 全要。"""
    add_entity(identity_client, "ent_place", name="西湖", status="confirmed", entity_type="place")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
            " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
            " VALUES ('prop_old', 'ent_place', 'scene_type', '\"旧\"', 'observation_extraction',"
            " 0.5, 'superseded', '[]', 1, ?, ?)",
            (NOW, NOW),
        )

    body = identity_client.get("/api/v1/entities/ent_place").json()

    assert body["properties"] == []
    assert [item["id"] for item in body["property_history"]] == ["prop_old"]

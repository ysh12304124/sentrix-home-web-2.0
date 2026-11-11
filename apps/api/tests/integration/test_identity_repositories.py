"""真 SQLite + 真迁移下的 S5a 身份仓储：人脸簇 / 实例 / 原型 + 实体 / 修订 / 属性 / 提及。

对应 1.0 ``backend/db.py`` 的 ``list_face_clusters`` :5133、``_refresh_face_prototypes``
:4922、``get_face_instance`` :4467、``list_face_prototypes`` :4547、
``find_confirmed_person_by_name`` :4407、``person_aliases`` / ``set_person_aliases`` /
``rename_person`` :4399-4466、实体属性层 :4666-4750、``_record_entity_revision`` :4644、
``entity_event_ids`` :4553。

这里的每一组用例钉的都是**一条会说谎的默认值**，不是「SQL 能跑通」：

- ``include_in_people`` 漏掉时，评测空间的人脸会被当成家庭成员展示给用户；
- ``pending`` 簇的名字漏掉时，界面上会出现「待确认人物簇 · cluster-3」这种内部 id；
- 属性覆盖漏掉时，用户手填的别名会被下一次派生写静默顶掉；
- 原型「先删后插」漏掉时，``UNIQUE(cluster_id, pose_bucket)`` 会直接抛错——1.0 那条
  删除语句一旦被当成冗余优化删掉，崩的是下一次刷新，而不是这次；
- 提及不校验 ``status='confirmed'`` 时，未确认的候选人物会被写进观察的人物快照，
  进而被 ``person_score`` 与词法投影当成真人。
"""

from __future__ import annotations

import json

import pytest

from sentrix.domain.identity.models import PENDING_CLUSTER_NAME
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository

NOW = "2026-09-23T10:00:00+00:00"


@pytest.fixture
def seeded_db(migrations_ready_db: Database) -> Database:
    """两行记忆空间（一个进人物视图、一个不进）+ 可被提及引用的父数据。

    外键是打开的（``Database.connect`` 每次都 ``PRAGMA foreign_keys = ON``），
    所以提及 / 实例都必须有真的 asset 与 observation 行才落得下去。
    """
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', '主相册', 1, ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album2', '评测空间', 0, ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES ('asset_1', 'album1', 'asset_1.jpg', 'image', '/tmp/asset_1.jpg', ?, ?)",
            (NOW, NOW),
        )
        for observation_id in ("obs_1", "obs_2"):
            connection.execute(
                "INSERT INTO observations"
                " (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, 'album1', 'asset_1', 'vlm', ?, ?)",
                (observation_id, NOW, NOW),
            )
        for event_id in ("evt_1", "evt_2", "evt_3"):
            connection.execute(
                "INSERT INTO events (id, scope_id, title, created_at, updated_at)"
                " VALUES (?, 'album1', '事件', ?, ?)",
                (event_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO event_observations (event_id, observation_id, created_at)"
            " VALUES ('evt_1', 'obs_1', ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO event_observations (event_id, observation_id, created_at)"
            " VALUES ('evt_3', 'obs_2', ?)",
            (NOW,),
        )
    return migrations_ready_db


@pytest.fixture
def entities(seeded_db: Database) -> SqliteEntityRepository:
    return SqliteEntityRepository(seeded_db)


@pytest.fixture
def faces(seeded_db: Database) -> SqliteFaceRepository:
    return SqliteFaceRepository(seeded_db)


def add_entity(
    db: Database,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = "album1",
    entity_type: str = "person",
    family_role: str | None = None,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, family_role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entity_id, scope_id, entity_type, name, status, family_role, NOW, NOW),
        )


def add_cluster(
    db: Database,
    cluster_id: str,
    *,
    scope_id: str = "album1",
    status: str = "pending",
    entity_id: str | None = None,
    member_count: int = 0,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, member_count, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cluster_id, scope_id, status, entity_id, member_count, NOW, NOW),
        )


def add_instance(
    db: Database,
    instance_id: str,
    *,
    cluster_id: str | None = None,
    quality: float = 0.5,
    pose_bucket: str = "frontal",
    created_at: str = NOW,
    bbox: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4),
    embedding: tuple[float, ...] = (1.0, 0.0),
    pose: tuple[float, ...] = (0.0, 0.0, 0.0),
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json, detection_confidence,"
            "  quality, pose_json, pose_bucket, embedding_model, embedding_version, created_at)"
            " VALUES (?, 'asset_1', 'obs_1', ?, ?, ?, 0.9, ?, ?, ?, 'buffalo_l', 'w600k_r50', ?)",
            (
                instance_id,
                cluster_id,
                json.dumps(list(bbox)),
                json.dumps(list(embedding)),
                quality,
                json.dumps(list(pose)),
                pose_bucket,
                created_at,
            ),
        )


# ---- 簇列表 ----------------------------------------------------------------------


def test_list_clusters_honours_include_in_people(faces: SqliteFaceRepository, seeded_db: Database):
    """``include_in_people = 0`` 的空间里的簇一条都不许出现在人物视图里。

    这条没有例外分支：评测空间（benchmark 的 ``PhotoBench%`` / ``*_e2b``）人脸聚类出的
    「人」不是家庭成员，把它们混进 ``/api/people`` 就是把 benchmark 数据当成了用户记忆。
    1.0 靠名字黑名单兜这件事（``db.py:1313``），机制本身是这一列。
    """
    add_cluster(seeded_db, "clu_in", scope_id="album1")
    add_cluster(seeded_db, "clu_out", scope_id="album2")

    assert [cluster.id for cluster in faces.list_clusters()] == ["clu_in"]
    assert [cluster.id for cluster in faces.list_clusters(status="pending")] == ["clu_in"]


def test_pending_cluster_is_displayed_as_an_unnamed_member(faces: SqliteFaceRepository, seeded_db: Database):
    """``pending`` 簇对外只叫「未确认姓名人物」，而且不许带家庭角色。

    1.0 在 HTTP 层做的这件事（``app.py:1976``）：库里那一行 ``canonical_name`` 是
    「待确认人物簇 · cluster-3」，直接展示出去用户看到的是内部 id。角色同理——
    没确认的人不该先挂上「妈妈」。
    """
    add_entity(seeded_db, "ent_pending", name="待确认人物簇 · clu_1", family_role="妈妈")
    add_entity(seeded_db, "ent_confirmed", name="妈妈", status="confirmed", family_role="妈妈")
    add_cluster(seeded_db, "clu_1", entity_id="ent_pending")
    add_cluster(seeded_db, "clu_2", entity_id="ent_confirmed")

    by_id = {cluster.id: cluster for cluster in faces.list_clusters()}

    assert PENDING_CLUSTER_NAME == "未确认姓名人物"
    assert by_id["clu_1"].canonical_name == PENDING_CLUSTER_NAME
    assert by_id["clu_1"].family_role is None
    assert by_id["clu_2"].canonical_name == "妈妈"
    assert by_id["clu_2"].family_role == "妈妈"


# ---- 实例与原型 ------------------------------------------------------------------


def test_get_instance_carries_the_asset_side_and_the_scope(faces: SqliteFaceRepository, seeded_db: Database):
    """裁剪端点（Task 75）只拿得到 instance_id，路径 / 文件名 / scope 都得由这一行带出来。"""
    add_instance(seeded_db, "ins_1", embedding=(0.5, 0.5), pose=(0.0, 10.0, 0.0))

    stored = faces.get_instance("ins_1")

    assert stored is not None
    assert stored.bbox == (0.1, 0.2, 0.3, 0.4)
    assert stored.embedding == (0.5, 0.5)
    assert stored.pose == (0.0, 10.0, 0.0)
    assert stored.cluster_id is None
    assert (stored.asset_path, stored.file_name, stored.scope_id) == (
        "/tmp/asset_1.jpg",
        "asset_1.jpg",
        "album1",
    )
    assert faces.get_instance("missing") is None


def test_refresh_prototypes_keeps_one_row_per_pose_bucket(faces: SqliteFaceRepository, seeded_db: Database):
    """每姿态桶一条，取 ``quality DESC, created_at ASC`` 的首个；``member_count`` 回写实例总数。

    同桶两条同质量时先创建的那条胜出——这一条就是 ``created_at ASC`` 存在的全部理由，
    去掉它桶内容会随插入顺序漂移。
    """
    add_cluster(seeded_db, "clu_1")
    add_instance(seeded_db, "ins_front_best", cluster_id="clu_1", quality=0.9, created_at=NOW)
    add_instance(seeded_db, "ins_front_early", cluster_id="clu_1", quality=0.8, created_at="2026-01-01")
    add_instance(seeded_db, "ins_front_late", cluster_id="clu_1", quality=0.8, created_at="2026-12-01")
    add_instance(
        seeded_db,
        "ins_profile",
        cluster_id="clu_1",
        quality=0.5,
        pose_bucket="profile_left",
        created_at="2027-01-01",
    )

    prototypes = faces.refresh_prototypes("clu_1")

    assert [(item.pose_bucket, item.face_instance_id) for item in prototypes] == [
        ("frontal", "ins_front_best"),
        ("profile_left", "ins_profile"),
    ]
    assert prototypes[0].model_name == "buffalo_l"
    assert prototypes[0].model_version == "w600k_r50"
    assert list(faces.list_prototypes("clu_1")) == list(prototypes)

    cluster = {item.id: item for item in faces.list_clusters()}["clu_1"]
    assert cluster.member_count == 4


def test_refresh_prototypes_replaces_instead_of_accumulating(
    faces: SqliteFaceRepository, seeded_db: Database
):
    """先删后插：来了更好的同桶样本，旧原型行必须先消失。

    不删的直接后果不是「多一行」——``face_prototypes`` 上有 ``UNIQUE(cluster_id,
    pose_bucket)``，第二次刷新会撞约束直接抛错。所以这条用例既验结果也验没有抛。
    """
    add_cluster(seeded_db, "clu_1")
    add_instance(seeded_db, "ins_1", cluster_id="clu_1", quality=0.9)
    faces.refresh_prototypes("clu_1")

    add_instance(seeded_db, "ins_better", cluster_id="clu_1", quality=0.95)
    prototypes = faces.refresh_prototypes("clu_1")

    assert [(item.face_instance_id, item.quality) for item in prototypes] == [("ins_better", 0.95)]
    with seeded_db.connect() as connection:
        rows = connection.execute(
            "SELECT count(*) AS n FROM face_prototypes WHERE cluster_id = 'clu_1'"
        ).fetchone()
    assert rows["n"] == 1


# ---- 实体属性层 ------------------------------------------------------------------


def test_user_property_value_is_never_overwritten_by_a_derived_value(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """``source='user'`` 的当前值永不被 derived 覆盖（``db.py:4695-4727``）。

    用户的输入是权威。派生写只能看到「当前是用户值」就原样返回，既不覆盖也不新起一条
    pending——新起一条同样会让界面上冒出第二个「别名」。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    written = entities.set_property("ent_1", "aliases", ["老妈"])

    assert (written.source, written.status, written.value) == ("user", "active", ["老妈"])

    kept = entities.maintain_property("ent_1", "aliases", ["妈妈"], confidence=0.9)

    assert (kept.value, kept.source, kept.status) == (["老妈"], "user", "active")
    assert [(item.value, item.source, item.status) for item in entities.list_properties("ent_1")] == [
        (["老妈"], "user", "active")
    ]


def test_derived_property_with_a_different_value_lands_pending(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """派生值与当前派生值不同 → 落 ``status='pending'`` 等确认，并记下被谁取代。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    first = entities.maintain_property("ent_1", "mood", "开心", confidence=0.6)
    assert (first.source, first.status, first.revision) == ("derived", "active", 1)

    second = entities.maintain_property("ent_1", "mood", "平静", confidence=0.7)

    assert (second.status, second.revision) == ("pending", 2)
    assert second.supersedes_property_id == first.id
    assert [item.value for item in entities.list_properties("ent_1")] == ["平静", "开心"]


def test_derived_property_with_the_same_value_only_reinforces(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """同值不新起行，只抬置信度并合并证据——否则每跑一次流水线就多一条历史。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    first = entities.maintain_property("ent_1", "mood", "开心", confidence=0.3, evidence_ids=("obs_1",))

    again = entities.maintain_property("ent_1", "mood", "开心", confidence=0.8, evidence_ids=("obs_2",))

    assert again.id == first.id
    assert again.confidence == pytest.approx(0.8)
    assert again.evidence_ids == ("obs_1", "obs_2")


def test_set_property_supersedes_the_previous_row(entities: SqliteEntityRepository, seeded_db: Database):
    """用户改一次值 = 旧行转 ``superseded`` + 新行 active，并留一条修订。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    entities.maintain_property("ent_1", "nickname", "妈妈")

    updated = entities.set_property("ent_1", "nickname", "老妈")

    assert (updated.value, updated.source, updated.status) == ("老妈", "user", "active")
    # 默认视图只有当前值：superseded 是历史，不该出现在「这个人现在叫什么」里。
    assert [(item.value, item.status) for item in entities.list_properties("ent_1")] == [("老妈", "active")]
    assert [item.value for item in entities.list_properties("ent_1", include_history=True)] == [
        "老妈",
        "妈妈",
    ]


# ---- 别名与改名 ------------------------------------------------------------------


def test_set_person_aliases_cleans_and_dedupes(entities: SqliteEntityRepository, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    assert entities.set_person_aliases("ent_1", [" 老妈 ", "老妈", "", "   ", "妈咪"]) == (
        "老妈",
        "妈咪",
    )
    assert entities.person_aliases("ent_1") == ("老妈", "妈咪")


def test_find_confirmed_person_prefers_the_exact_name_over_an_alias(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """先精确名，再遍历 aliases——顺序反过来会让「老妈」这种别名抢走同名的人。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_entity(seeded_db, "ent_2", name="老妈", status="confirmed")
    entities.set_person_aliases("ent_1", ["老妈", "妈咪"])

    exact = entities.find_confirmed_person_by_name("album1", "老妈")

    assert exact is not None
    assert (exact.entity_id, exact.via_alias) == ("ent_2", False)
    by_alias = entities.find_confirmed_person_by_name("album1", "妈咪")
    assert by_alias is not None
    assert (by_alias.entity_id, by_alias.via_alias) == ("ent_1", True)


def test_find_confirmed_person_ignores_pending_and_other_scopes(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """只认 ``entity_type='person' AND status='confirmed'``，且限本 scope。"""
    add_entity(seeded_db, "ent_pending", name="爸爸", status="pending")
    add_entity(seeded_db, "ent_place", name="湖边", status="confirmed", entity_type="place")
    add_entity(seeded_db, "ent_other", name="奶奶", status="confirmed", scope_id="album2")

    assert entities.find_confirmed_person_by_name("album1", "爸爸") is None
    assert entities.find_confirmed_person_by_name("album1", "湖边") is None
    assert entities.find_confirmed_person_by_name("album1", "奶奶") is None
    assert entities.find_confirmed_person_by_name("album1", "   ") is None


def test_record_revision_writes_one_row_per_call(entities: SqliteEntityRepository, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    entities.record_revision("ent_1", "canonical_name", "妈妈", "老妈", source="user_rename")
    second = entities.record_revision("ent_1", "family_role", None, "母亲", source="user")

    assert (second.field_name, second.old_value, second.new_value, second.source) == (
        "family_role",
        None,
        "母亲",
        "user",
    )
    with seeded_db.connect() as connection:
        rows = connection.execute(
            "SELECT field_name, old_value, new_value, source FROM entity_revisions"
            " WHERE entity_id = 'ent_1' ORDER BY rowid"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("canonical_name", "妈妈", "老妈", "user_rename"),
        ("family_role", None, "母亲", "user"),
    ]


def test_rename_person_folds_the_old_name_into_aliases(entities: SqliteEntityRepository, seeded_db: Database):
    """改名要把旧名折进 aliases，否则用户按旧名再叫一次就找不到这个人了。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    renamed = entities.rename_person("ent_1", "老妈")

    assert renamed is not None
    assert (renamed.canonical_name, renamed.name_state) == ("老妈", "confirmed")
    assert entities.person_aliases("ent_1") == ("妈妈",)
    assert entities.find_confirmed_person_by_name("album1", "妈妈") is not None
    with seeded_db.connect() as connection:
        # 改名会先写一条 aliases 的属性修订，再写本名这条——按 field_name 取，不靠顺序。
        row = connection.execute(
            "SELECT field_name, old_value, new_value, source FROM entity_revisions"
            " WHERE field_name = 'canonical_name'"
        ).fetchone()
    assert tuple(row) == ("canonical_name", "妈妈", "老妈", "user_rename")


def _people_snapshot(db: Database, observation_id: str) -> list[dict[str, str]]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT people_json FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
    return json.loads(row["people_json"] or "[]")


def test_renaming_a_confirmed_person_rewrites_the_name_in_photo_snapshots(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """快照存的是名字：检索人物条件、记忆图、照片预览都读它。改名不同步，
    这个人所有照片上记的还是旧名，按新名字一张都查不到。别人的条目不能被碰到。"""
    add_entity(seeded_db, "ent_1", name="王王", status="confirmed")
    add_entity(seeded_db, "ent_2", name="明明", status="confirmed")
    entities.link_confirmed_mention("ent_1", "obs_1")
    entities.link_confirmed_mention("ent_2", "obs_1")
    entities.link_confirmed_mention("ent_1", "obs_2")

    entities.rename_person("ent_1", "王小王")

    assert _people_snapshot(seeded_db, "obs_1") == [
        {"entity_id": "ent_1", "name": "王小王", "status": "confirmed"},
        {"entity_id": "ent_2", "name": "明明", "status": "confirmed"},
    ]
    assert _people_snapshot(seeded_db, "obs_2") == [
        {"entity_id": "ent_1", "name": "王小王", "status": "confirmed"},
    ]


def test_relinking_a_mention_refreshes_a_stale_snapshot_entry(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """老库里可能已经有改名前留下的旧条目：再写一次提及时按当前实体对齐，不追加第二条。"""
    add_entity(seeded_db, "ent_1", name="王王", status="confirmed")
    with seeded_db.connect() as connection, connection:
        connection.execute(
            "UPDATE observations SET people_json = ? WHERE id = 'obs_1'",
            (json.dumps([{"entity_id": "ent_1", "name": "旧名", "status": "pending"}], ensure_ascii=False),),
        )

    entities.link_confirmed_mention("ent_1", "obs_1")

    assert _people_snapshot(seeded_db, "obs_1") == [
        {"entity_id": "ent_1", "name": "王王", "status": "confirmed"},
    ]


def test_rename_person_refuses_a_non_person_entity(entities: SqliteEntityRepository, seeded_db: Database):
    add_entity(seeded_db, "ent_place", name="湖边", status="confirmed", entity_type="place")

    assert entities.rename_person("ent_place", "水边") is None
    assert entities.rename_person("missing", "水边") is None


def test_rename_person_rejects_an_empty_name(entities: SqliteEntityRepository, seeded_db: Database):
    """空名字不是「不改名」，是调用方出错——静默返回会让界面显示改成功。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    with pytest.raises(ValueError):
        entities.rename_person("ent_1", "   ")


# ---- 提及桥 ----------------------------------------------------------------------


def test_mention_is_only_written_for_a_confirmed_entity(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """未确认的候选人物不许写进观察的人物快照——那是 ``person_score`` 的输入。"""
    add_entity(seeded_db, "ent_pending", name="待确认人物簇 · clu_1")

    with pytest.raises(ValueError):
        entities.link_confirmed_mention("ent_pending", "obs_1")

    with seeded_db.connect() as connection:
        rows = connection.execute("SELECT count(*) AS n FROM entity_mentions").fetchone()
    assert rows["n"] == 0


def test_confirmed_mention_links_the_observation_and_its_people_snapshot(
    entities: SqliteEntityRepository, seeded_db: Database
):
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_instance(seeded_db, "ins_1")

    mention = entities.link_confirmed_mention("ent_1", "obs_1", face_instance_id="ins_1", confidence=0.9)
    again = entities.link_confirmed_mention("ent_1", "obs_1", face_instance_id="ins_1", confidence=0.9)

    assert mention.id == again.id
    assert (mention.entity_id, mention.observation_id, mention.face_instance_id) == (
        "ent_1",
        "obs_1",
        "ins_1",
    )
    with seeded_db.connect() as connection:
        row = connection.execute("SELECT people_json FROM observations WHERE id = 'obs_1'").fetchone()
        rows = connection.execute("SELECT count(*) AS n FROM entity_mentions").fetchone()
    assert rows["n"] == 1
    assert json.loads(row["people_json"]) == [{"entity_id": "ent_1", "name": "妈妈", "status": "confirmed"}]


def test_entity_event_ids_unions_mentions_participants_and_observations(
    entities: SqliteEntityRepository, seeded_db: Database
):
    """三条来源缺一条，人物相关的事件就会漏：1.0 的 UNION 三段各有各的写入者。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_instance(seeded_db, "ins_1")
    entities.link_confirmed_mention("ent_1", "obs_1", face_instance_id="ins_1")
    with seeded_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_observations (entity_id, observation_id, created_at)"
            " VALUES ('ent_1', 'obs_2', ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO event_participants"
            " (id, event_id, person_id, role, created_at, updated_at)"
            " VALUES ('par_1', 'evt_2', 'ent_1', 'visible_subject', ?, ?)",
            (NOW, NOW),
        )

    assert set(entities.event_ids("ent_1")) == {"evt_1", "evt_2", "evt_3"}
    assert list(entities.event_ids("ent_missing")) == []

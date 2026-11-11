"""命名用例：改名（旧名折进别名）与按实体确认（同名并入 / 落到本簇 / 无簇兜底）。

对应 1.0 ``backend/db.py`` 的 ``rename_person`` :4436、``confirm_person_entity`` :5247、
``find_confirmed_person_by_name`` :4407、``person_aliases`` :4399 / ``set_person_aliases``
:4423。

**这一层的两条路互不重复。** 簇入口（``ReviewCluster.confirm``）问的是「这一簇脸是谁」，
人入口（``NamePerson.confirm_entity``）问的是「这个人是谁」——后者要把实体反查成簇，并在
这个人一个簇都没有时仍能完成确认。确认本身的规则（同名并入、提及、家庭角色）只有一份，
在 ``ReviewCluster`` 里。
"""

from __future__ import annotations

import json

import pytest

from sentrix.application.identity.name_person import NamePerson
from sentrix.application.identity.review_cluster import (
    InvalidReviewRequestError,
    ReviewCluster,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository

NOW = "2026-09-23T10:00:00+00:00"

HOME = "album1"
OTHER = "album2"


@pytest.fixture
def seeded_db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        for scope_id, include in ((HOME, 1), (OTHER, 0)):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (scope_id, scope_id, include, NOW, NOW),
            )
        for index in (1, 2, 3):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (f"asset_{index}", HOME, f"asset_{index}.jpg", f"/tmp/asset_{index}.jpg", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, ?, ?, 'vlm', ?, ?)",
                (f"obs_{index}", HOME, f"asset_{index}", NOW, NOW),
            )
    return migrations_ready_db


@pytest.fixture
def name_person(seeded_db: Database, confirmed_person_events, observe_appearance) -> NamePerson:
    return NamePerson(
        faces=SqliteFaceRepository(seeded_db),
        entities=SqliteEntityRepository(seeded_db),
        review=ReviewCluster(
            faces=SqliteFaceRepository(seeded_db),
            entities=SqliteEntityRepository(seeded_db),
            facts=SqliteFactRepository(seeded_db),
            events=confirmed_person_events(seeded_db),
            appearance=observe_appearance(seeded_db),
        ),
    )


# ---- 造数 ------------------------------------------------------------------------


def add_entity(
    db: Database,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = HOME,
    entity_type: str = "person",
    family_role: str | None = None,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, family_role, summary,"
            "  confidence, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, '旧说明', 0.0, ?, ?)",
            (entity_id, scope_id, entity_type, name, status, family_role, NOW, NOW),
        )


def add_cluster(
    db: Database,
    cluster_id: str,
    *,
    scope_id: str = HOME,
    status: str = "pending",
    entity_id: str | None = None,
    member_count: int = 0,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '[]', ?, 1, ?, ?)",
            (cluster_id, scope_id, status, entity_id, member_count, NOW, NOW),
        )


def add_instance(
    db: Database,
    instance_id: str,
    *,
    cluster_id: str | None,
    observation_id: str = "obs_1",
    asset_id: str = "asset_1",
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, embedding_json, detection_confidence,"
            "  quality, pose_bucket, embedding_model, embedding_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, 0.9, 0.8, 'frontal', 'buffalo_l', 'w600k_r50', ?)",
            (instance_id, asset_id, observation_id, cluster_id, json.dumps([1.0, 0.0]), NOW),
        )


def query(db: Database, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def scalar(db: Database, sql: str, params: tuple[object, ...] = ()) -> object:
    with db.connect() as connection:
        row = connection.execute(sql, params).fetchone()
    return row[0] if row else None


# ---- 改名 ------------------------------------------------------------------------


def test_rename_folds_the_old_name_into_aliases(name_person: NamePerson, seeded_db: Database):
    """1.0 ``db.py:4452``：旧名进 aliases，本名换新。旧名是**可查的**，不是历史注释。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    result = name_person.rename("ent_1", "母亲")

    assert result is not None
    assert result.entity.canonical_name == "母亲"
    assert result.aliases == ("妈妈",)
    assert scalar(seeded_db, "SELECT canonical_name FROM entities WHERE id = 'ent_1'") == "母亲"


def test_rename_writes_a_user_rename_revision(name_person: NamePerson, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    name_person.rename("ent_1", "母亲")

    rows = query(
        seeded_db,
        "SELECT field_name, old_value, new_value, source FROM entity_revisions"
        " WHERE entity_id = 'ent_1' ORDER BY field_name",
    )
    # 两条，缺一不可：一条是改名的审计，一条是**别名属性写入自己带出来的审计**
    # （``set_property`` 会对每次属性变更留痕，不是改名顺手加的）。只断言前者会让
    # 「旧名进 aliases」这件事在审计上失去痕迹——而它正是别名能被查到的原因。
    assert rows == [
        {
            "field_name": "canonical_name",
            "old_value": "妈妈",
            "new_value": "母亲",
            "source": "user_rename",
        },
        {
            "field_name": "property:aliases",
            "old_value": None,
            "new_value": '["妈妈"]',
            "source": "user",
        },
    ]


def test_rename_turns_name_state_and_status_confirmed(name_person: NamePerson, seeded_db: Database):
    """用户在人物页给候选人填名字就是确认：name_state 与 status 都要转 confirmed。"""
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1", status="pending")
    assert scalar(seeded_db, "SELECT name_state FROM entities WHERE id = 'ent_1'") == "anonymous"

    name_person.rename("ent_1", "妈妈")

    assert scalar(seeded_db, "SELECT name_state FROM entities WHERE id = 'ent_1'") == "confirmed"
    assert scalar(seeded_db, "SELECT status FROM entities WHERE id = 'ent_1'") == "confirmed"


def test_rename_to_the_same_name_writes_no_revision(name_person: NamePerson, seeded_db: Database):
    """1.0 ``db.py:4451`` 的 ``if old_name != new_name``：同名改名是一个空操作。

    没有这一条，用户在界面上点两次保存就会在审计里留下两条「妈妈 → 妈妈」。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    result = name_person.rename("ent_1", "妈妈")

    assert result is not None
    assert result.aliases == ()
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_revisions") == 0
    assert scalar(seeded_db, "SELECT name_state FROM entities WHERE id = 'ent_1'") == "anonymous"


def test_rename_of_an_unknown_entity_is_none(name_person: NamePerson):
    assert name_person.rename("ent_missing", "妈妈") is None


def test_rename_of_a_non_person_entity_is_none(name_person: NamePerson, seeded_db: Database):
    """1.0 ``db.py:4441`` 拒的是非 person，不是「不存在」——两者都回 None，路由都报 404。"""
    add_entity(seeded_db, "ent_place", name="家", entity_type="place")

    assert name_person.rename("ent_place", "新家") is None
    assert scalar(seeded_db, "SELECT canonical_name FROM entities WHERE id = 'ent_place'") == "家"


def test_rename_with_an_empty_name_is_a_value_error(name_person: NamePerson, seeded_db: Database):
    """空名字不是「不改名」，是调用方出错——抛错而不是静默返回。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    with pytest.raises(ValueError, match="person name is required"):
        name_person.rename("ent_1", "   ")


def test_rename_twice_keeps_both_old_names_in_aliases(name_person: NamePerson, seeded_db: Database):
    """连改两次，两个旧名都留着。1.0 是「追加到当前 aliases」而不是「只留上一个」。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")

    name_person.rename("ent_1", "母亲")
    result = name_person.rename("ent_1", "老妈")

    assert result is not None
    assert result.aliases == ("妈妈", "母亲")


# ---- 按名查人 --------------------------------------------------------------------


def test_find_confirmed_person_by_name_prefers_the_exact_name(
    seeded_db: Database,
):
    """1.0 ``db.py:4412``：先精确名再扫别名。

    顺序反了的话，某人的**别名**会抢走名字真的叫这个的另一个人——两个人的脸会挂到
    同一个实体上，而且从审计里看不出来。这条把顺序钉死：ent_exact 的本名是「妈妈」，
    ent_alias 的别名也是「妈妈」，命中必须是 ent_exact。
    """
    entities = SqliteEntityRepository(seeded_db)
    add_entity(seeded_db, "ent_alias", name="母亲", status="confirmed")
    add_entity(seeded_db, "ent_exact", name="妈妈", status="confirmed")
    entities.set_person_aliases("ent_alias", ["妈妈"])

    match = entities.find_confirmed_person_by_name(HOME, "妈妈")

    assert match is not None
    assert match.entity_id == "ent_exact"
    assert match.via_alias is False


def test_find_confirmed_person_by_name_falls_back_to_aliases_when_no_exact(
    seeded_db: Database,
):
    entities = SqliteEntityRepository(seeded_db)
    add_entity(seeded_db, "ent_1", name="母亲", status="confirmed")
    entities.set_person_aliases("ent_1", ["妈妈"])

    match = entities.find_confirmed_person_by_name(HOME, "妈妈")

    assert match is not None
    assert match.entity_id == "ent_1"
    assert match.via_alias is True


def test_a_pending_person_is_never_found_by_name(seeded_db: Database):
    """查的是 ``status='confirmed'``。「还没认过的人」不能被同名并入吸走。"""
    entities = SqliteEntityRepository(seeded_db)
    add_entity(seeded_db, "ent_1", name="妈妈", status="pending")

    assert entities.find_confirmed_person_by_name(HOME, "妈妈") is None


# ---- 按实体确认 ------------------------------------------------------------------


def test_confirm_entity_uses_the_cluster_and_confirms_it(name_person: NamePerson, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1", status="pending")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1", member_count=1)
    add_instance(seeded_db, "fi_1", cluster_id="clu_1")

    result = name_person.confirm_entity("ent_1", name="妈妈", family_role="母亲")

    assert result is not None
    assert result.entity.canonical_name == "妈妈"
    assert result.entity.family_role == "母亲"
    assert scalar(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_1'") == "confirmed"
    assert scalar(seeded_db, "SELECT entity_id FROM face_clusters WHERE id = 'clu_1'") == "ent_1"
    # 确认写提及：事件 person_score 与 person_bridge 的输入。
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_mentions WHERE entity_id = 'ent_1'") == 1


def test_confirm_entity_prefers_the_pending_cluster_over_a_confirmed_one(
    name_person: NamePerson, seeded_db: Database
):
    """1.0 ``db.py:5258`` 的 ``ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END``。

    用户按下确认时想认的是那个刚聚出来、还没归属的簇；挑到旧的已确认簇会把一张脸
    挂到错的人身上（两个簇都属于这个实体，只有一个是用户正在看的那张脸）。
    """
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_old", status="confirmed")
    add_cluster(seeded_db, "clu_old", status="confirmed", entity_id="ent_1", member_count=5)
    add_cluster(seeded_db, "clu_new", status="pending", entity_id="ent_1", member_count=1)

    name_person.confirm_entity("ent_1", name="妈妈")

    assert scalar(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_new'") == "confirmed"
    assert scalar(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_old'") == "confirmed"


def test_confirm_entity_merges_into_an_existing_same_name_person(
    name_person: NamePerson, seeded_db: Database
):
    """同名并入走的是 ``ReviewCluster.confirm`` 那一路，不在这里重写一遍。"""
    add_entity(seeded_db, "ent_old", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_old", status="confirmed", entity_id="ent_old", member_count=3)
    add_entity(seeded_db, "ent_new", name="待确认人物簇 · clu_new", status="pending")
    add_cluster(seeded_db, "clu_new", entity_id="ent_new", member_count=1)
    add_instance(seeded_db, "fi_1", cluster_id="clu_new")

    result = name_person.confirm_entity("ent_new", name="妈妈")

    assert result is not None
    assert result.merged_into == "ent_old"
    # 源簇退休且归属改成并进的那个人；实例改挂到对方的已确认簇上。
    assert query(
        seeded_db, "SELECT status, entity_id, member_count FROM face_clusters WHERE id = 'clu_new'"
    ) == [{"status": "rejected", "entity_id": "ent_old", "member_count": 0}]
    assert scalar(seeded_db, "SELECT cluster_id FROM face_instances WHERE id = 'fi_1'") == "clu_old"


def test_confirm_entity_without_any_cluster_still_confirms_the_person(
    name_person: NamePerson, seeded_db: Database
):
    """1.0 ``db.py:5270`` 的兜底分支：这个人一个簇都没有（脸还没检测出来，名字先定了）。

    这一支只有一条 ``UPDATE``，**不写修订**——名字是**首次**给出来的，不是改出来的。
    """
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1", status="pending")

    result = name_person.confirm_entity("ent_1", name="妈妈", family_role="母亲")

    assert result is not None
    assert result.entity.canonical_name == "妈妈"
    assert result.entity.family_role == "母亲"
    assert result.entity.status == "confirmed"
    assert result.entity.summary == "旧说明"  # 兜底分支不动 summary
    assert scalar(seeded_db, "SELECT confidence FROM entities WHERE id = 'ent_1'") == 1.0
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_revisions") == 0


def test_confirm_entity_of_an_unknown_person_is_none(name_person: NamePerson):
    assert name_person.confirm_entity("ent_missing", name="妈妈") is None


def test_confirm_entity_of_a_non_person_is_none(name_person: NamePerson, seeded_db: Database):
    add_entity(seeded_db, "ent_place", name="家", entity_type="place")

    assert name_person.confirm_entity("ent_place", name="妈妈") is None


def test_confirm_entity_without_a_name_is_a_400(name_person: NamePerson, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1", status="pending")

    for bad_name in ("", "   "):
        with pytest.raises(InvalidReviewRequestError):
            name_person.confirm_entity("ent_1", name=bad_name)


def test_confirm_entity_strips_the_family_role(name_person: NamePerson, seeded_db: Database):
    """空白的角色折成 ``None``——与 ``ReviewCluster.confirm`` 同一条规则。"""
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1", status="pending")

    result = name_person.confirm_entity("ent_1", name="妈妈", family_role="   ")

    assert result is not None
    assert result.entity.family_role is None


# ---- 同名只在同 scope 内合并 ------------------------------------------------------


def test_same_name_in_another_scope_does_not_merge(name_person: NamePerson, seeded_db: Database):
    """1.0 的 ``test_cross_scope_same_name_does_not_merge``。

    评测空间（``OTHER``）里的「妈妈」与家里的「妈妈」是**两个人**：名字一样不代表同一个
    人，而跨空间合并会把评测集的人脸挂进家庭相册。1.0 靠 ``find_confirmed_person_by_name``
    的 ``scope_id`` 过滤实现这一条，2.0 照搬。
    """
    add_entity(seeded_db, "ent_home", name="妈妈", status="confirmed", scope_id=HOME)
    add_entity(seeded_db, "ent_other", name="待确认人物簇 · clu_2", status="pending", scope_id=OTHER)
    add_cluster(seeded_db, "clu_2", scope_id=OTHER, entity_id="ent_other", member_count=1)

    result = name_person.confirm_entity("ent_other", name="妈妈")

    assert result is not None
    assert result.merged_into is None
    assert scalar(seeded_db, "SELECT canonical_name FROM entities WHERE id = 'ent_other'") == "妈妈"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entities WHERE canonical_name = '妈妈'") == 2
    assert scalar(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_2'") == "confirmed"


def test_find_confirmed_person_by_name_with_empty_scope_searches_all_albums(
    seeded_db: Database,
):
    """「全部相册」传到用户 API 的是空 scope；不能按空串精确匹配而查不到人。"""
    entities = SqliteEntityRepository(seeded_db)
    add_entity(seeded_db, "ent_1", name="明明", status="confirmed")

    match = entities.find_confirmed_person_by_name("", "明明")

    assert match is not None
    assert match.entity_id == "ent_1"

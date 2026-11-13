"""人物级合并：用户在名录里把同一个人的几个人物并成一个。

验的是 ``MergePeople`` + ``SqlitePersonMergeRepository`` 在真库上留下的痕迹：人脸、提及、
观察快照、两套关系、家庭归属、实体状态与审计，以及幂等重放与几条拒绝路径。

事件夹具是空的，所以 ``refresh_participants`` / ``resegment`` 在这里是空操作——它们的
行为在 ``test_event_participants.py`` 里单独钉，与 ``test_review_cluster.py`` 同一约定。
"""

from __future__ import annotations

import json

import pytest

from sentrix.application.identity.merge_people import MergePeople
from sentrix.application.identity.review_cluster import (
    ClusterNotFoundError,
    InvalidReviewRequestError,
    ReviewConflictError,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.person_merge import SqlitePersonMergeRepository

NOW = "2026-11-11T10:00:00+00:00"
HOME = "album1"
OTHER = "album2"


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        for scope_id in (HOME, OTHER):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                (scope_id, scope_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES ('asset_1', ?, 'a.jpg', 'image', '/tmp/a.jpg', ?, ?)",
            (HOME, NOW, NOW),
        )
        for index in range(1, 6):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, ?, 'asset_1', 'vlm', ?, ?)",
                (f"obs_{index}", HOME, NOW, NOW),
            )
    return migrations_ready_db


@pytest.fixture
def merge(db: Database, confirmed_person_events, observe_appearance) -> MergePeople:
    return MergePeople(
        entities=SqliteEntityRepository(db),
        faces=SqliteFaceRepository(db),
        merges=SqlitePersonMergeRepository(db),
        events=confirmed_person_events(db),
        appearance=observe_appearance(db),
    )


# ---- 造数 ------------------------------------------------------------------------


def person(
    db: Database,
    entity_id: str,
    name: str,
    *,
    status: str = "confirmed",
    scope_id: str = HOME,
    family_role: str | None = None,
    faces: tuple[str, ...] = (),
) -> str:
    """一个人 + 一个簇 + 若干张脸（每张脸一条观察）。已确认的人顺带写好提及与快照。"""
    cluster_id = f"cluster_{entity_id}"
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, family_role,"
            " summary, confidence, name_state, created_at, updated_at)"
            " VALUES (?, ?, 'person', ?, ?, ?, '', 0.5, ?, ?, ?)",
            (
                entity_id,
                scope_id,
                name,
                status,
                family_role,
                "anonymous" if name.startswith("待确认人物簇") else "confirmed",
                NOW,
                NOW,
            ),
        )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id, representative_embedding_json,"
            " member_count, revision, created_at, updated_at) VALUES (?, ?, ?, ?, '[]', ?, 1, ?, ?)",
            (cluster_id, scope_id, status, entity_id, len(faces), NOW, NOW),
        )
        for face in faces:
            observation = f"obs_{face[-1]}"
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, embedding_json,"
                " detection_confidence, quality, pose_bucket, embedding_model, embedding_version,"
                " created_at) VALUES (?, 'asset_1', ?, ?, '[1.0, 0.0]', 0.9, 0.8, 'frontal',"
                " 'buffalo_l', 'w600k_r50', ?)",
                (face, observation, cluster_id, NOW),
            )
            if status == "confirmed":
                connection.execute(
                    "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id,"
                    " confidence, created_at) VALUES (?, ?, ?, ?, 0.9, ?)",
                    (f"mention_{face}", entity_id, observation, face, NOW),
                )
                connection.execute(
                    "UPDATE observations SET people_json = ? WHERE id = ?",
                    (
                        json.dumps([{"entity_id": entity_id, "name": name, "status": "confirmed"}]),
                        observation,
                    ),
                )
    return cluster_id


def family_edge(
    db: Database,
    subject: str,
    predicate: str,
    obj: str,
    inverse: str,
    *,
    locked: bool,
    source: str = "user_override",
) -> None:
    with db.connect() as connection, connection:
        for edge_id, s, p, o, inv in (
            (f"fr_{subject}_{obj}", subject, predicate, obj, inverse),
            (f"fr_{obj}_{subject}", obj, inverse, subject, predicate),
        ):
            connection.execute(
                "INSERT INTO family_relationships (id, scope_id, subject_entity_id, predicate,"
                " object_entity_id, inverse_predicate, source, confidence, state, locked, revision,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 'effective', ?, 1, ?, ?)",
                (edge_id, HOME, s, p, o, inv, source, int(locked), NOW, NOW),
            )


def membership(db: Database, entity_id: str, value: str, *, locked: bool) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_person_memberships (id, scope_id, entity_id, membership, source,"
            " confidence, state, locked, revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 1.0, 'effective', ?, 1, ?, ?)",
            (
                f"m_{entity_id}",
                HOME,
                entity_id,
                value,
                "user_override" if locked else "model",
                int(locked),
                NOW,
                NOW,
            ),
        )


def rows(db: Database, sql: str, *params: object) -> list[dict]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


# ---- 主路径 ----------------------------------------------------------------------


def test_two_confirmed_people_become_one_and_keep_every_photo(db: Database, merge: MergePeople) -> None:
    target_cluster = person(db, "mom", "妈妈", family_role="母亲", faces=("face_1", "face_2"))
    source_cluster = person(db, "mom_young", "年轻的妈妈", faces=("face_3",))
    SqliteEntityRepository(db).set_person_aliases("mom_young", ["小芳"])

    result = merge(target_person_id="mom", source_person_ids=["mom_young"])

    assert result.entity.id == "mom"
    assert result.entity.canonical_name == "妈妈"
    assert result.entity.status == "confirmed"
    assert result.entity.family_role == "母亲"
    assert result.merged_person_ids == ("mom_young",)
    assert result.moved_face_count == 1
    # 所有脸都在目标的簇里，来源簇退休但归属改成目标。
    faces = rows(db, "SELECT id, cluster_id FROM face_instances ORDER BY id")
    assert {item["cluster_id"] for item in faces} == {target_cluster}
    source = rows(
        db, "SELECT status, entity_id, member_count FROM face_clusters WHERE id = ?", source_cluster
    )
    assert source == [{"status": "rejected", "entity_id": "mom", "member_count": 0}]
    target = rows(db, "SELECT member_count FROM face_clusters WHERE id = ?", target_cluster)
    assert target == [{"member_count": 3}]
    # 提及全部挂在目标名下：按人找照片的桥。
    mentions = rows(db, "SELECT entity_id, face_instance_id FROM entity_mentions ORDER BY face_instance_id")
    assert [(item["entity_id"], item["face_instance_id"]) for item in mentions] == [
        ("mom", "face_1"),
        ("mom", "face_2"),
        ("mom", "face_3"),
    ]
    # 来源旧名与别名留成目标的别名；来源实体退休，不物理删除。
    assert set(SqliteEntityRepository(db).person_aliases("mom")) == {"年轻的妈妈", "小芳"}
    assert rows(db, "SELECT status FROM entities WHERE id = 'mom_young'") == [{"status": "superseded"}]
    # 观察快照里的旧名字被换成目标。
    snapshot = json.loads(
        rows(db, "SELECT people_json FROM observations WHERE id = 'obs_3'")[0]["people_json"]
    )
    assert snapshot == [{"entity_id": "mom", "name": "妈妈", "status": "confirmed"}]
    # 双边审计。
    revisions = rows(
        db, "SELECT entity_id, field_name, new_value, source FROM entity_revisions ORDER BY entity_id"
    )
    merge_rows = [item for item in revisions if item["source"] == "user_person_merge"]
    assert {(item["entity_id"], item["field_name"], item["new_value"]) for item in merge_rows} == {
        ("mom", "absorbed_person", "mom_young"),
        ("mom_young", "merged_into", "mom"),
    }


def test_an_unnamed_target_takes_the_sources_name_and_becomes_confirmed(
    db: Database, merge: MergePeople
) -> None:
    """用户选了一个还没命名的人当「保留」：名字取来源的，已确认状态也不能丢。"""
    target_cluster = person(db, "anon", "待确认人物簇 · c1", status="pending", faces=("face_1",))
    person(db, "wang", "王王", faces=("face_2",))

    result = merge(target_person_id="anon", source_person_ids=["wang"])

    assert result.entity.canonical_name == "王王"
    assert result.entity.status == "confirmed"
    assert rows(db, "SELECT status FROM face_clusters WHERE id = ?", target_cluster) == [
        {"status": "confirmed"}
    ]
    # 目标原来那张脸没有提及；确认之后必须补上。
    mentioned = {
        item["face_instance_id"] for item in rows(db, "SELECT face_instance_id FROM entity_mentions")
    }
    assert mentioned == {"face_1", "face_2"}


def test_an_explicit_name_wins_and_the_other_names_become_aliases(db: Database, merge: MergePeople) -> None:
    person(db, "a", "阿明", faces=("face_1",))
    person(db, "b", "明明", faces=("face_2",))

    result = merge(target_person_id="a", source_person_ids=["b"], name="小明")

    assert result.entity.canonical_name == "小明"
    assert set(SqliteEntityRepository(db).person_aliases("a")) == {"阿明", "明明"}


def test_two_pending_people_stay_pending_and_get_no_mentions(db: Database, merge: MergePeople) -> None:
    person(db, "p1", "待确认人物簇 · c1", status="pending", faces=("face_1",))
    person(db, "p2", "待确认人物簇 · c2", status="pending", faces=("face_2",))

    result = merge(target_person_id="p1", source_person_ids=["p2"])

    assert result.entity.status == "pending"
    assert rows(db, "SELECT COUNT(*) AS n FROM entity_mentions") == [{"n": 0}]
    assert SqliteEntityRepository(db).person_aliases("p1") == ()


# ---- 关系与归属 ------------------------------------------------------------------


def test_family_edges_follow_the_merge_and_a_locked_edge_wins(db: Database, merge: MergePeople) -> None:
    person(db, "mom", "妈妈", faces=("face_1",))
    person(db, "mom2", "母亲", faces=("face_2",))
    person(db, "kid", "孩子", faces=("face_3",))
    person(db, "dad", "爸爸", faces=("face_4",))
    # 同一个对方：目标那组是模型推断的，来源那组是用户锁定的 → 来源那组胜出并改挂。
    family_edge(db, "mom", "母亲", "kid", "女儿", locked=False, source="model")
    family_edge(db, "mom2", "母亲", "kid", "儿子", locked=True)
    # 只有来源有的边直接改挂；两个参与者之间的边是自环，作废。
    family_edge(db, "mom2", "妻子", "dad", "丈夫", locked=True)
    family_edge(db, "mom", "其他亲属", "mom2", "其他亲属", locked=True)

    result = merge(target_person_id="mom", source_person_ids=["mom2"])

    effective = rows(
        db,
        "SELECT subject_entity_id AS s, predicate AS p, object_entity_id AS o FROM family_relationships"
        " WHERE state = 'effective' ORDER BY s, o",
    )
    assert effective == [
        {"s": "dad", "p": "丈夫", "o": "mom"},
        {"s": "kid", "p": "儿子", "o": "mom"},
        {"s": "mom", "p": "妻子", "o": "dad"},
        {"s": "mom", "p": "母亲", "o": "kid"},
    ]
    assert result.moved_relationship_count == 2
    assert result.dropped_relationship_count == 2


def test_a_locked_membership_from_the_source_replaces_a_model_guess_on_the_target(
    db: Database, merge: MergePeople
) -> None:
    person(db, "a", "妈妈", faces=("face_1",))
    person(db, "b", "母亲", faces=("face_2",))
    membership(db, "a", "friend", locked=False)
    membership(db, "b", "family", locked=True)

    merge(target_person_id="a", source_person_ids=["b"])

    effective = rows(
        db, "SELECT entity_id, membership FROM family_person_memberships WHERE state = 'effective'"
    )
    assert effective == [{"entity_id": "a", "membership": "family"}]


def test_generic_relationships_are_repointed_and_self_loops_retracted(
    db: Database, merge: MergePeople
) -> None:
    person(db, "a", "妈妈", faces=("face_1",))
    person(db, "b", "母亲", faces=("face_2",))
    person(db, "c", "孩子", faces=("face_3",))
    with db.connect() as connection, connection:
        for rel_id, subject, obj in (("r1", "b", "c"), ("r2", "a", "b"), ("r3", "a", "c")):
            connection.execute(
                "INSERT INTO relationships (id, scope_id, subject_entity_id, predicate, object_entity_id,"
                " status, confidence, created_at, updated_at) "
                "VALUES (?, ?, ?, '同框', ?, 'pending', 0.5, ?, ?)",
                (rel_id, HOME, subject, obj, NOW, NOW),
            )

    merge(target_person_id="a", source_person_ids=["b"])

    states = {
        item["id"]: (item["subject_entity_id"], item["object_entity_id"], item["status"])
        for item in rows(db, "SELECT id, subject_entity_id, object_entity_id, status FROM relationships")
    }
    # r1 改挂后与 r3 重复 → 作废；r2 是自环 → 作废；r3 原样保留。
    assert states["r1"][2] == "retracted"
    assert states["r2"][2] == "retracted"
    assert states["r3"] == ("a", "c", "pending")


# ---- 幂等与拒绝 ------------------------------------------------------------------


def test_replaying_a_finished_merge_is_a_no_op(db: Database, merge: MergePeople) -> None:
    person(db, "a", "妈妈", faces=("face_1",))
    person(db, "b", "母亲", faces=("face_2",))
    merge(target_person_id="a", source_person_ids=["b"])
    revisions_before = rows(db, "SELECT COUNT(*) AS n FROM entity_revisions")

    again = merge(target_person_id="a", source_person_ids=["b"])

    assert again.entity.id == "a"
    assert again.merged_person_ids == ("b",)
    assert again.moved_face_count == 0
    assert rows(db, "SELECT COUNT(*) AS n FROM entity_revisions") == revisions_before


def test_a_merged_person_cannot_be_merged_into_someone_else(db: Database, merge: MergePeople) -> None:
    person(db, "a", "妈妈", faces=("face_1",))
    person(db, "b", "母亲", faces=("face_2",))
    person(db, "c", "外婆", faces=("face_3",))
    merge(target_person_id="a", source_person_ids=["b"])

    with pytest.raises(ReviewConflictError):
        merge(target_person_id="c", source_person_ids=["b"])
    with pytest.raises(ReviewConflictError):
        merge(target_person_id="b", source_person_ids=["c"])


def test_people_from_different_spaces_cannot_be_merged(db: Database, merge: MergePeople) -> None:
    person(db, "a", "妈妈", faces=("face_1",))
    person(db, "b", "母亲", scope_id=OTHER)

    with pytest.raises(ReviewConflictError):
        merge(target_person_id="a", source_person_ids=["b"])
    assert rows(db, "SELECT status FROM entities WHERE id = 'b'") == [{"status": "confirmed"}]


def test_the_merged_name_must_not_collide_with_another_confirmed_person(
    db: Database, merge: MergePeople
) -> None:
    person(db, "a", "待确认人物簇 · c1", status="pending", faces=("face_1",))
    person(db, "b", "待确认人物簇 · c2", status="pending", faces=("face_2",))
    person(db, "c", "妈妈", faces=("face_3",))
    person(db, "d", "母亲", faces=("face_4",))

    with pytest.raises(ReviewConflictError):
        merge(target_person_id="d", source_person_ids=["b"], name="妈妈")


@pytest.mark.parametrize(
    ("target", "sources", "error"),
    [
        ("", ["b"], InvalidReviewRequestError),
        ("a", [], InvalidReviewRequestError),
        ("a", ["a"], InvalidReviewRequestError),
        ("a", ["nobody"], ClusterNotFoundError),
        ("nobody", ["a"], ClusterNotFoundError),
    ],
)
def test_requests_that_cannot_stand_are_rejected_before_any_write(
    db: Database, merge: MergePeople, target: str, sources: list[str], error: type[Exception]
) -> None:
    person(db, "a", "妈妈", faces=("face_1",))
    person(db, "b", "母亲", faces=("face_2",))

    with pytest.raises(error):
        merge(target_person_id=target, source_person_ids=sources)
    assert rows(db, "SELECT COUNT(*) AS n FROM entities WHERE status = 'superseded'") == [{"n": 0}]

"""在线匹配用例：一张人脸进来之后，它落到哪个簇、留没留下证据。

对应 1.0 ``backend/db.py:4815-4911`` 的 ``add_face_instance`` 全文。这里每一组用例
钉的都是它九条分支里的一条，而不是「函数能跑通」：

1. ``scope_id`` 由 asset 决定——调用方连传都传不进来。传得进来就会有一个「把这张脸
   算进别人家簇里」的入口，而它不会有任何报错；
2. 资格闸门不合格时**不建簇**，只落一条 ``cluster_id = NULL`` 的实例。合格与否由
   ``identity_eligible`` 与 ``validity == "verified"`` 的合取决定；
3. ``quality >= 0.30`` 才进候选循环。低质量人脸是最容易同时像好几个人的桥，让它参与
   匹配就是把两个身份连成一片；
4. 候选只取同 scope 且未废弃的簇；
5. 原型签名 ``(model_name, model_version)`` 不匹配就跳过该簇——不同模型的向量不可比；
6. 分数取与**所有**原型余弦的最大值；一个原型都没有时退回簇的代表向量；
7. 都不像就新建簇，**并同时建一个 pending person 实体**（没有实体就没有地方挂名字）；
8. 实例落库 → 刷原型 → 写 ``memory_vectors``；
9. 只有 ``status='confirmed'`` 的实体才写提及——写错这一条，一次聚类猜测就会变成
   观察的人物快照，进而变成 ``person_score`` 与词法投影的事实。

**最后两条对照用例钉的是两个不同的 0.30。** ``db.py:4815`` 的 ``threshold=0.30`` 是
余弦阈值，``db.py:4868`` 的 ``quality >= 0.30`` 是质量门槛。数值相同、语义无关，
改一个不影响另一个——所以实现里必须是两个独立的常量，测试里各钉一条。
"""

from __future__ import annotations

import inspect
import json

import pytest

from sentrix.application.identity.enroll_face import (
    MATCH_THRESHOLD,
    MINIMUM_MATCH_QUALITY,
    EnrollFace,
)
from sentrix.domain.identity.faces import compute_face_quality, pose_bucket
from sentrix.domain.identity.models import FaceDetection
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

NOW = "2026-09-23T10:00:00+00:00"

# 两张图各一张脸：asset_1 在主相册 album1，asset_2 在评测空间 album2。
# 第二个 scope 不是装饰——「scope 由 asset 决定」这条只有跨 scope 才验得出来。
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
        for asset_id, scope_id in (("asset_1", HOME), ("asset_2", OTHER)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for observation_id, asset_id, scope_id in (
            ("obs_1", "asset_1", HOME),
            ("obs_2", "asset_2", OTHER),
        ):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, ?, ?, 'vlm', ?, ?)",
                (observation_id, scope_id, asset_id, NOW, NOW),
            )
    return migrations_ready_db


@pytest.fixture
def enroll(seeded_db: Database) -> EnrollFace:
    return EnrollFace(
        faces=SqliteFaceRepository(seeded_db),
        entities=SqliteEntityRepository(seeded_db),
        vectors=SqliteVectorRepository(seeded_db),
        assets=SqliteAssetRepository(seeded_db),
    )


# ---- 造数 ------------------------------------------------------------------------


def add_entity(
    db: Database,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = HOME,
    confidence: float = 0.0,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, confidence, created_at, updated_at)"
            " VALUES (?, ?, 'person', ?, ?, ?, ?, ?)",
            (entity_id, scope_id, name, status, confidence, NOW, NOW),
        )


def add_cluster(
    db: Database,
    cluster_id: str,
    *,
    scope_id: str = HOME,
    status: str = "pending",
    entity_id: str | None = None,
    representative: tuple[float, ...] = (),
    member_count: int = 0,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cluster_id,
                scope_id,
                status,
                entity_id,
                json.dumps(list(representative)),
                member_count,
                NOW,
                NOW,
            ),
        )


def add_instance(
    db: Database,
    instance_id: str,
    *,
    cluster_id: str | None = None,
    asset_id: str = "asset_1",
    observation_id: str = "obs_1",
    embedding: tuple[float, ...] = (1.0, 0.0),
    quality: float = 0.9,
    pose_bucket_value: str = "frontal",
    model_name: str = "buffalo_l",
    model_version: str = "w600k_r50",
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, embedding_json, detection_confidence,"
            "  quality, pose_bucket, embedding_model, embedding_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, 0.9, ?, ?, ?, ?, ?)",
            (
                instance_id,
                asset_id,
                observation_id,
                cluster_id,
                json.dumps(list(embedding)),
                quality,
                pose_bucket_value,
                model_name,
                model_version,
                NOW,
            ),
        )


def add_prototype(
    db: Database,
    cluster_id: str,
    *,
    face_instance_id: str,
    embedding: tuple[float, ...],
    pose_bucket_value: str = "frontal",
    model_name: str = "buffalo_l",
    model_version: str = "w600k_r50",
    quality: float = 0.9,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_prototypes"
            " (id, cluster_id, face_instance_id, pose_bucket, embedding_json, quality,"
            "  model_name, model_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"proto_{face_instance_id}",
                cluster_id,
                face_instance_id,
                pose_bucket_value,
                json.dumps(list(embedding)),
                quality,
                model_name,
                model_version,
                NOW,
                NOW,
            ),
        )


def query(db: Database, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def scalar(db: Database, sql: str, params: tuple[object, ...] = ()) -> object:
    with db.connect() as connection:
        row = connection.execute(sql, params).fetchone()
    return row[0] if row else None


def face(**overrides: object) -> FaceDetection:
    """一张默认「合格、高质量、正脸」的检测结果，逐条用例只改它关心的字段。"""
    fields: dict[str, object] = {
        "embedding": (1.0, 0.0),
        "bbox": (0.1, 0.2, 0.3, 0.4),
        "confidence": 0.9,
        "quality": 0.9,
        "pose": (0.0, 0.0, 0.0),
    }
    fields.update(overrides)
    return FaceDetection(**fields)  # type: ignore[arg-type]


def instance_row(db: Database, instance_id: str) -> dict[str, object]:
    rows = query(db, "SELECT * FROM face_instances WHERE id = ?", (instance_id,))
    assert len(rows) == 1
    return rows[0]


# ---- 1. scope 由 asset 决定 ------------------------------------------------------


def test_scope_id_is_derived_from_the_asset_and_cannot_be_passed_in():
    """调用方没有 ``scope_id`` 这个入参——它由 asset 行决定。

    这不是风格问题：能传 scope 就等于多了一条「把这张脸算进别人家簇里」的入口，
    而且它不会报错，只会让两个空间的人脸互相吸附。
    """
    assert "scope_id" not in inspect.signature(EnrollFace.enroll).parameters


def test_candidates_from_another_scope_are_never_matched(enroll: EnrollFace, seeded_db: Database):
    """同样的向量、同样的合格人脸，只要 asset 在 album2，就不能并进 album1 的簇。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_2", "obs_2", face())

    assert result.cluster_id != "clu_home"
    cluster = query(seeded_db, "SELECT scope_id FROM face_clusters WHERE id = ?", (result.cluster_id,))
    assert cluster == [{"scope_id": OTHER}]


# ---- 2. 资格闸门 -----------------------------------------------------------------


def test_a_face_that_is_not_identity_eligible_is_stored_without_a_cluster(
    enroll: EnrollFace, seeded_db: Database
):
    """不合格 → 实例照落（它还是证据），但 ``cluster_id`` 是 NULL，且一个簇都不建。

    「不建簇」这一半比 ``cluster_id = NULL`` 更重要：建了簇就会有原型、有代表向量，
    下一张同角度的人脸会来吸附它。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face(validity="uncertain"))

    assert (result.cluster_id, result.score) == (None, 0.0)
    assert instance_row(seeded_db, result.instance_id)["cluster_id"] is None
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 1
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entities") == 1


def test_identity_eligible_false_wins_over_a_verified_validity(enroll: EnrollFace, seeded_db: Database):
    """资格是 ``identity_eligible AND validity == "verified"`` 的合取，两个都能否掉。

    只判 validity 的实现会在「模型明确说这张脸不参与身份」时照常匹配。
    """
    result = enroll.enroll("asset_1", "obs_1", face(identity_eligible=False))

    assert result.cluster_id is None
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 0


def test_identity_eligible_defaults_to_identity_ready(enroll: EnrollFace, seeded_db: Database):
    """``identity_eligible`` 缺席时取 ``identity_ready``（1.0 的 ``dict.get`` 默认值）。"""
    assert enroll.enroll("asset_1", "obs_1", face(identity_ready=False)).cluster_id is None
    assert enroll.enroll("asset_1", "obs_1", face(identity_ready=True)).cluster_id is not None


def test_an_empty_embedding_is_never_eligible_and_writes_no_vector(enroll: EnrollFace, seeded_db: Database):
    """空向量是「没有可用向量」，不是「和谁都相似度为 0」。

    1.0 在这一支里调 ``upsert_vector``，而 ``upsert_vector`` 自己会把空向量挡掉；
    2.0 的向量仓储直接抛——所以挡的位置必须在这里，而且挡的是**写**。
    """
    result = enroll.enroll("asset_1", "obs_1", face(embedding=()))

    assert result.cluster_id is None
    assert scalar(seeded_db, "SELECT COUNT(*) FROM memory_vectors") == 0


# ---- 3 / 6 / 7. 质量门与余弦阈值 -------------------------------------------------


def test_the_quality_gate_is_not_the_cosine_threshold(enroll: EnrollFace, seeded_db: Database):
    """两个 0.30 的第一个：``quality >= 0.30``。

    阈值压到 0.0（余弦门等于不存在），质量 0.0 的人脸只留 local_only，不新建 pending 簇。
    旧路径「质量门挡住匹配 → 仍建新簇」正是人物伪簇膨胀的来源，v3 S2 已修。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face(quality=0.0), threshold=0.0)

    assert result.cluster_id is None
    assert result.identity_state == "local_only"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 1


def test_the_cosine_threshold_is_not_the_quality_gate(enroll: EnrollFace, seeded_db: Database):
    """两个 0.30 的第二个：``threshold=0.30`` 与 ``best_score < threshold``。

    质量拉满（0.99）保证质量门不是原因，余弦只有 0.9 < 0.99 就另起一簇。
    把 ``MATCH_THRESHOLD`` 改掉这条会红，改 ``MINIMUM_MATCH_QUALITY`` 不会。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll(
        "asset_1", "obs_1", face(quality=0.99, embedding=(0.9, 0.4358898943540674)), threshold=0.99
    )

    assert result.cluster_id != "clu_home"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 2


def test_the_two_thresholds_have_the_same_value_and_stay_independent():
    """数值相同、语义无关。实现里必须是两个名字，不能合并成一个常量。"""
    assert MATCH_THRESHOLD == 0.30
    assert MINIMUM_MATCH_QUALITY == 0.30


def test_quality_exactly_at_the_gate_still_matches(enroll: EnrollFace, seeded_db: Database):
    """边界：``quality >= 0.30`` 是**闭**区间——恰好 0.30 的人脸要进候选循环。

    写成 ``> 0.30`` 的话这条会红：那张脸会另起一簇，而用户看到的是同一个人的两张照片
    变成两个人。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face(quality=MINIMUM_MATCH_QUALITY))

    assert result.cluster_id == "clu_home"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 1


def test_a_rejected_cluster_is_not_a_candidate(enroll: EnrollFace, seeded_db: Database):
    """候选 = 同 scope 且 ``status IN ('pending','confirmed')``。

    ``rejected`` 的簇在界面上已经被用户否掉（「这不是一个人」），再往里塞脸等于把
    被否的结论悄悄复活。
    """
    add_cluster(seeded_db, "clu_rejected", status="rejected", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face())

    assert result.cluster_id != "clu_rejected"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 2


# ---- 5. 原型签名 -----------------------------------------------------------------


def test_a_cluster_whose_prototypes_carry_another_signature_is_skipped(
    enroll: EnrollFace, seeded_db: Database
):
    """原型签名 ``(model_name, model_version)`` 与新向量不一致 → 整簇跳过。

    不同模型的向量不可比：拿 512 维的 buffalo_l 去和 adaface 的余弦比，比出来的是
    两个坐标系的夹角。跳过之后这张脸另起一簇，而不是并进一个错的簇。
    """
    add_entity(seeded_db, "ent_1", name="妈妈")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))
    add_instance(seeded_db, "ins_old", cluster_id="clu_home", model_name="adaface", model_version="m1")
    add_prototype(
        seeded_db,
        "clu_home",
        face_instance_id="ins_old",
        embedding=(1.0, 0.0),
        model_name="adaface",
        model_version="m1",
    )

    result = enroll.enroll("asset_1", "obs_1", face())

    assert result.cluster_id != "clu_home"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 2


def test_a_cluster_without_prototypes_falls_back_to_its_representative(
    enroll: EnrollFace, seeded_db: Database
):
    """一个原型都没有的簇用 ``representative_embedding_json`` 参与比对，签名检查随之失效。

    这条同时是「签名不匹配就跳过」的边界：**没有签名**时不跳过。刚建出来还没落实例的
    簇正处在这个状态，把它跳过等于每次匹配都另起一簇。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face())

    assert result.cluster_id == "clu_home"
    assert result.score == pytest.approx(1.0)


def test_the_score_is_the_maximum_over_all_prototypes(enroll: EnrollFace, seeded_db: Database):
    """分数取与**所有**原型余弦的最大值：像其中某一个视角就够。

    用一个 dissimilar 原型 + 一个 identical 原型：取 max 会并入，取 min / mean 会另起。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1")
    add_instance(seeded_db, "ins_a", cluster_id="clu_home", embedding=(0.0, 1.0))
    add_instance(
        seeded_db,
        "ins_b",
        cluster_id="clu_home",
        embedding=(1.0, 0.0),
        pose_bucket_value="profile_right",
    )
    # 两个原型的签名必须与待匹配人脸解析出来的签名一致（``face()`` 不带版本 → legacy），
    # 否则这条用例验的就不是 max 而是「签名不匹配就跳过」。
    add_prototype(
        seeded_db, "clu_home", face_instance_id="ins_a", embedding=(0.0, 1.0), model_version="legacy"
    )
    add_prototype(
        seeded_db,
        "clu_home",
        face_instance_id="ins_b",
        embedding=(1.0, 0.0),
        pose_bucket_value="profile_right",
        model_version="legacy",
    )

    result = enroll.enroll("asset_1", "obs_1", face(embedding=(1.0, 0.0)))

    assert result.cluster_id == "clu_home"
    assert result.score == pytest.approx(1.0)


# ---- 7. 新建簇（含 pending 实体） ------------------------------------------------


def test_a_new_cluster_comes_with_a_pending_person_entity(enroll: EnrollFace, seeded_db: Database):
    """建簇必须同时建实体，名字与 summary 是 1.0 原文。

    没有实体就没有地方挂名字——消息文案一字不差是有后果的：用户在界面上看到的那句
    「由 buffalo_l embedding 生成，等待用户确认」正是这里写下去的。
    """
    result = enroll.enroll("asset_1", "obs_1", face(quality=0.75))
    assert result.cluster_id is not None

    cluster = query(seeded_db, "SELECT * FROM face_clusters WHERE id = ?", (result.cluster_id,))
    assert len(cluster) == 1
    entity = query(seeded_db, "SELECT * FROM entities WHERE id = ?", (cluster[0]["entity_id"],))
    assert len(entity) == 1
    assert entity[0]["canonical_name"] == f"待确认人物簇 · {result.cluster_id}"
    assert entity[0]["status"] == "pending"
    assert entity[0]["entity_type"] == "person"
    assert entity[0]["summary"] == "由 buffalo_l embedding 生成，等待用户确认"
    assert entity[0]["confidence"] == pytest.approx(0.75)
    assert entity[0]["scope_id"] == HOME


# ---- 8. 实例、原型、向量 ---------------------------------------------------------


def test_the_instance_is_bound_and_the_prototypes_are_refreshed(enroll: EnrollFace, seeded_db: Database):
    """第二次匹配进同一个簇时，实例落库、``member_count`` 与原型一起刷新。"""
    first = enroll.enroll("asset_1", "obs_1", face(quality=0.5, pose=(0.0, 40.0, 0.0)))
    second = enroll.enroll("asset_1", "obs_1", face(quality=0.9, pose=(0.0, 40.0, 0.0)))

    assert second.cluster_id == first.cluster_id
    assert instance_row(seeded_db, second.instance_id)["cluster_id"] == first.cluster_id
    cluster = query(seeded_db, "SELECT member_count FROM face_clusters WHERE id = ?", (first.cluster_id,))
    assert cluster == [{"member_count": 2}]
    # 两张脸同一个姿态桶（profile_right）→ 只留质量最高的那一张做原型。
    prototypes = query(
        seeded_db, "SELECT face_instance_id FROM face_prototypes WHERE cluster_id = ?", (first.cluster_id,)
    )
    assert prototypes == [{"face_instance_id": second.instance_id}]


def test_the_instance_carries_the_resolved_model_identity_and_pose_bucket(
    enroll: EnrollFace, seeded_db: Database
):
    """签名两列落的是**解析后**的值：模型名缺省取 ``model_name`` 入参，版本缺省 ``legacy``。

    落库的值与原型签名是同一对——它们就是下一次匹配时的签名比对依据，在写入端漂开一次，
    匹配端就永远匹配不上同一个模型的簇。
    """
    result = enroll.enroll("asset_1", "obs_1", face(pose=(0.0, 30.0, 0.0)))
    row = instance_row(seeded_db, result.instance_id)

    assert (row["embedding_model"], row["embedding_version"]) == ("buffalo_l", "legacy")
    assert row["pose_bucket"] == "profile_right"
    assert row["pose_bucket"] == pose_bucket((0.0, 30.0, 0.0))


def test_quality_and_pose_bucket_are_computed_when_the_detector_leaves_them_out(
    enroll: EnrollFace, seeded_db: Database
):
    """检测器不给 quality / pose_bucket 时按同一套纯规则算出来，而不是落 0。"""
    detection = face(quality=None, pose=(0.0, 30.0, 0.0), confidence=0.8, area_ratio=0.04, sharpness=0.6)

    result = enroll.enroll("asset_1", "obs_1", detection)
    row = instance_row(seeded_db, result.instance_id)

    assert row["quality"] == pytest.approx(compute_face_quality(0.8, 0.04, 0.6, (0.0, 30.0, 0.0)))
    assert row["pose_bucket"] == "profile_right"


def test_the_visual_vector_points_back_at_the_instance(enroll: EnrollFace, seeded_db: Database):
    """``memory_vectors(space='visual', source_type='face_instance')`` 是这条实例的索引行。"""
    result = enroll.enroll("asset_1", "obs_1", face(quality=0.6))

    vectors = query(
        seeded_db,
        "SELECT * FROM memory_vectors WHERE space = 'visual' AND source_type = 'face_instance'",
    )
    assert len(vectors) == 1
    vector = vectors[0]
    assert vector["source_id"] == result.instance_id
    assert vector["model_name"] == "buffalo_l"
    assert vector["scope_id"] == HOME
    metadata = json.loads(str(vector["metadata_json"]))
    assert metadata["cluster_id"] == result.cluster_id
    assert metadata["asset_id"] == "asset_1"
    assert metadata["observation_id"] == "obs_1"
    assert metadata["model_version"] == "legacy"
    assert metadata["quality"] == pytest.approx(0.6)


def test_an_ineligible_face_still_writes_its_vector_without_a_cluster(
    enroll: EnrollFace, seeded_db: Database
):
    """不合格的人脸也是证据：向量照写，但元数据里没有 ``cluster_id``、资格是假。

    「不合格就没向量」会让这些脸在检索里彻底消失；1.0 写它，2.0 也写。
    """
    result = enroll.enroll("asset_1", "obs_1", face(validity="uncertain"))

    vectors = query(
        seeded_db,
        "SELECT * FROM memory_vectors WHERE space = 'visual' AND source_type = 'face_instance'",
    )
    assert [item["source_id"] for item in vectors] == [result.instance_id]
    metadata = json.loads(str(vectors[0]["metadata_json"]))
    assert "cluster_id" not in metadata
    assert metadata["identity_eligible"] is False


# ---- 9. 提及 ---------------------------------------------------------------------


def test_only_a_confirmed_entity_gets_a_mention(enroll: EnrollFace, seeded_db: Database):
    """并进一个**已确认**的簇 → 写提及，带上实例 id 与检测置信度。

    这条提及就是 ``person_score`` 与 ``person_bridge`` 的输入：把「这张照片里的妈妈
    被认出来了」变成可检索的事实，起点是这一行。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face())

    mentions = query(seeded_db, "SELECT * FROM entity_mentions WHERE entity_id = 'ent_1'")
    assert len(mentions) == 1
    assert mentions[0]["face_instance_id"] == result.instance_id
    assert mentions[0]["observation_id"] == "obs_1"
    assert mentions[0]["confidence"] == pytest.approx(0.9)


def test_a_pending_entity_gets_no_mention(enroll: EnrollFace, seeded_db: Database):
    """并进一个**未确认**的簇 → 什么都没有。

    「未确认」意味着这个名字是聚类猜的；把它写进观察的人物快照，一次猜测就变成了
    检索事实。1.0 用调用点上的 ``if entity.status == "confirmed"`` 挡这件事。
    """
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_home")
    add_cluster(seeded_db, "clu_home", entity_id="ent_1", representative=(1.0, 0.0))

    result = enroll.enroll("asset_1", "obs_1", face())

    assert result.cluster_id == "clu_home"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_mentions") == 0
    assert scalar(seeded_db, "SELECT people_json FROM observations WHERE id = 'obs_1'") == "[]"

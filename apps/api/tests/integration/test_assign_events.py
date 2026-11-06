"""事件归属用例：真 SQLite + 真打分，含「视觉向量必须先写」这条硬约束。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sentrix.application.memories.assign_events import AssignEvents, EventVisualSimilarity
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.memories.events import WEIGHTS
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

BASE = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)
MODEL = scheme.IMAGE_MODEL


@pytest.fixture
def repository_set(migrations_ready_db: Database):
    assets = SqliteAssetRepository(migrations_ready_db)
    observations = SqliteObservationRepository(migrations_ready_db)
    vectors = SqliteVectorRepository(migrations_ready_db)
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    events = SqliteEventRepository(migrations_ready_db)
    entities = SqliteEntityRepository(migrations_ready_db)
    assign = AssignEvents(
        events=events,
        observations=observations,
        assets=assets,
        places=places,
        visual_similarity=EventVisualSimilarity(vectors=vectors, observations=observations, assets=assets),
        # Task 80 起这两个来源是**必填**的：装配点少接一个就是 TypeError，而不是
        # person_score 静默恒为 0。真装配点见 ``api/routes/media.py`` 的 build_import_assets。
        confirmed_people=entities.confirmed_people_for_observation,
        confirmed_event_people=entities.confirmed_people_for_event,
        clock=lambda: BASE,
    )
    return {
        "assets": assets,
        "observations": observations,
        "vectors": vectors,
        "places": places,
        "events": events,
        "entities": entities,
        "db": migrations_ready_db,
        "assign": assign,
    }


def seed(
    repos,
    *,
    asset_id: str,
    observation_id: str,
    captured_at: datetime = BASE,
    gps: GpsPoint | None = XIAOSHAN,
    caption: str = "一家人在公园里拍照",
    place: str = "公园",
    activity: str = "拍照",
    event_type: str = "户外活动",
    objects: tuple[str, ...] = ("相机",),
    district: str = "萧山区",
) -> Observation:
    repos["assets"].add(
        Asset(
            id=asset_id,
            scope_id="album1",
            file_name=f"{asset_id}.jpg",
            media_type=MediaType.IMAGE,
            path=f"/tmp/{asset_id}.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256=f"sha-{asset_id}",
            captured_at=captured_at,
            captured_location=gps,
            created_at=captured_at,
            updated_at=captured_at,
        )
    )
    observation = Observation(
        id=observation_id,
        scope_id="album1",
        asset_id=asset_id,
        source_type="import",
        captured_at=captured_at,
        confidence=0.65,
        created_at=captured_at,
        updated_at=captured_at,
        caption=caption,
        place=place,
        activity=activity,
        event_type=event_type,
        objects=objects,
    )
    repos["observations"].add(observation)
    repos["places"].save(
        asset_id=asset_id,
        scope_id="album1",
        gps=gps,
        place=Place(
            admin=AdminArea("浙江省", "杭州市", district),
            nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
            precision=PlacePrecision.DISTRICT,
            source=PlaceSource.PYGEOCN,
            gps=gps,
            confidence=0.9,
        ),
    )
    return observation


def write_visual(repos, asset_id: str, vector: tuple[float, ...]) -> None:
    repos["vectors"].upsert(
        VectorRecord(
            scope_id="album1",
            space="visual",
            source_type="asset",
            source_id=asset_id,
            model_name=MODEL,
            vector=vector,
        )
    )


def test_first_observation_creates_an_event(repository_set):
    observation = seed(repository_set, asset_id="asset_1", observation_id="obs_1")

    result = repository_set["assign"].execute(observation)

    assert result.created is True
    assert result.score is None
    assert result.event.time_start == BASE
    assert result.event.admin_district == "萧山区"
    assert result.event.place == "浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近"
    assert repository_set["events"].observation_ids(result.event.id) == ["obs_1"]


def test_same_time_and_place_folds_into_the_existing_event(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    second = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(minutes=5),
    )
    created = repository_set["assign"].execute(first)
    write_visual(repository_set, "asset_1", (1.0, 0.0, 0.0))

    result = repository_set["assign"].execute(second)

    assert result.created is False
    assert result.event.id == created.event.id
    assert set(repository_set["events"].observation_ids(created.event.id)) == {"obs_1", "obs_2"}


def test_far_apart_in_time_creates_a_second_event(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    late = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(hours=9),
        gps=GpsPoint(31.2304, 121.4737),
    )
    repository_set["assign"].execute(first)

    result = repository_set["assign"].execute(late)

    assert result.created is True
    assert repository_set["events"].list_active("album1", limit=10).__len__() == 2


def _fold_pair(repos, *, write_vectors: bool):
    """两张同时间同地点的照片，第二张归并时返回它的打分。"""
    first = seed(repos, asset_id="asset_1", observation_id="obs_1")
    second = seed(repos, asset_id="asset_2", observation_id="obs_2", captured_at=BASE + timedelta(minutes=5))
    repos["assign"].execute(first)
    if write_vectors:
        write_visual(repos, "asset_1", (1.0, 0.0, 0.0))
        write_visual(repos, "asset_2", (1.0, 0.0, 0.0))
    return repos["assign"].execute(second)


def test_without_a_visual_vector_the_boost_is_zero_and_flagged_unavailable(repository_set):
    """向量没写 → ``available`` 为假。**不报错、不拆事件**，只是少 0.05 分。

    这正是 Task 56 那条顺序约束难被发现的原因：写反了整条链路照样绿。
    """
    result = _fold_pair(repository_set, write_vectors=False)

    assert result.created is False
    assert result.score is not None
    assert result.score.visual_available is False
    assert result.score.visual_boost == pytest.approx(0.0)


def test_visual_vector_written_first_gives_the_full_boost(repository_set):
    """向量先写 → ``visual_boost`` 满值，总分高于没写向量的那次。"""
    result = _fold_pair(repository_set, write_vectors=True)

    assert result.score is not None
    assert result.score.visual_available is True
    assert result.score.visual_boost == pytest.approx(1.0)
    # 5% 权重那一项真的进了总分，而不是只写在 breakdown 里：按展开式重算一遍总分。
    score = result.score
    expected = (
        WEIGHTS["time"] * score.time
        + WEIGHTS["location"] * score.location
        + WEIGHTS["visual_place"] * score.visual_place
        + WEIGHTS["event_type"] * score.event_type
        + WEIGHTS["activity"] * score.activity
        + WEIGHTS["objects"] * score.objects
        + WEIGHTS["person"] * score.person
        + WEIGHTS["visual_boost"] * score.visual_boost
        + score.spatio_temporal_bonus
    )
    assert score.total == pytest.approx(min(1.0, expected))


def test_semantic_conflict_without_bridge_creates_a_separate_event(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    conflicting = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(minutes=10),
        caption="一家人在室内聚餐",
        place="餐厅",
        activity="室内聚餐",
        event_type="室内聚餐",
        objects=(),
    )
    repository_set["assign"].execute(first)

    result = repository_set["assign"].execute(conflicting)

    assert result.created is True


def test_shared_object_bridges_a_semantic_conflict(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    bridging = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(minutes=10),
        caption="同一台相机拍的室内聚餐",
        place="餐厅",
        activity="室内聚餐",
        event_type="室内聚餐",
        objects=("相机",),
    )
    created = repository_set["assign"].execute(first)

    result = repository_set["assign"].execute(bridging)

    assert result.created is False
    assert result.event.id == created.event.id


def test_candidate_count_reports_ambiguity(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    second = seed(
        repository_set, asset_id="asset_2", observation_id="obs_2", captured_at=BASE + timedelta(minutes=2)
    )
    late = seed(
        repository_set, asset_id="asset_3", observation_id="obs_3", captured_at=BASE + timedelta(minutes=4)
    )
    repository_set["assign"].execute(first)
    repository_set["assign"].execute(second)

    result = repository_set["assign"].execute(late)

    assert result.candidate_count >= 1


def test_observation_without_asset_raises(repository_set):
    from datetime import UTC as _UTC

    orphan = Observation(
        id="obs_x",
        scope_id="album1",
        asset_id="missing",
        source_type="import",
        captured_at=BASE,
        confidence=0.0,
        created_at=BASE,
        updated_at=BASE,
    )
    with pytest.raises(KeyError):
        repository_set["assign"].execute(orphan)
    assert _UTC is not None


# ---- person_score 的接线（Task 80） ------------------------------------------------
#
# 这一组用例是**对照**，不是单点断言：同一份数据，只改「有没有把这个人挂上去」。
# 1.0 的两条来源（``_confirmed_entity_ids_for_observation`` / ``_for_event``）在 S5
# 之前恒回空集，于是 person_score 恒为 0 而不报错——只断言「接了之后 > 0」抓不住这种
# 静默，必须同时钉住「没接时是 0」。

NOW = BASE.isoformat()


def add_person(db, entity_id: str, *, status: str = "confirmed", name: str = "妈妈") -> None:
    """直接插一行实体：用例要的是**打分**，不是 ``create_person`` 的建法。"""
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES (?, 'album1', 'person', ?, ?, '', 1.0, ?, ?)",
            (entity_id, name, status, NOW, NOW),
        )


def link_mention(db, entity_id: str, observation_id: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, confidence, created_at)"
            " VALUES (?, ?, ?, 0.9, ?)",
            (f"mention_{entity_id}_{observation_id}", entity_id, observation_id, NOW),
        )


def _score_second_observation(repos, *, person_status: str | None, tag: str = "a", minutes_later: int = 5):
    """两张同时间同地点的照片；第二张归并时返回它的打分。

    ``person_status`` 为 ``None`` 表示不挂任何人（对照臂）；给 ``"confirmed"`` 时把同一个
    人挂到**两张**观察上——观察侧读第二张，事件侧读第一张，正是两条来源各自的位置。

    ``tag`` 让**同一个测试**里跑两条臂成为可能：资产 id 有唯一约束，两条臂必须落在
    不同的资产上。两条臂共用同一个库是有意的——时间、地点、文本、向量全都一样，
    唯一的变量就是那个人。
    """
    first = seed(repos, asset_id=f"asset_1_{tag}", observation_id=f"obs_1_{tag}")
    second = seed(
        repos,
        asset_id=f"asset_2_{tag}",
        observation_id=f"obs_2_{tag}",
        captured_at=BASE + timedelta(minutes=minutes_later),
    )
    repos["assign"].execute(first)
    if person_status is not None:
        add_person(repos["db"], f"ent_1_{tag}", status=person_status)
        link_mention(repos["db"], f"ent_1_{tag}", f"obs_1_{tag}")
        link_mention(repos["db"], f"ent_1_{tag}", f"obs_2_{tag}")
    return repos["assign"].execute(second).score


def test_a_shared_confirmed_person_fills_the_person_component(repository_set):
    score = _score_second_observation(repository_set, person_status="confirmed")

    assert score is not None
    assert score.person == 0.8


def test_without_a_shared_person_the_component_stays_zero(repository_set):
    """对照臂：同一条数据，只是没挂人。没有这一条，「接了之后是 0.8」抓不住「没接也是 0.8」。"""
    score = _score_second_observation(repository_set, person_status=None)

    assert score is not None
    assert score.person == 0.0


def test_a_pending_entity_never_reaches_the_person_component(repository_set):
    """未确认的候选人物簇不算数：它是一次聚类猜测，不是「这个人在这里」。"""
    score = _score_second_observation(repository_set, person_status="pending")

    assert score is not None
    assert score.person == 0.0


def test_the_person_component_moves_the_total_by_exactly_its_weight(repository_set):
    """零成本不变量：两条臂的总分差 == ``WEIGHTS['person'] * 0.8``，一分不多一分不少。

    只比「大于」会让「权重写错但仍大于 0」混过去——而权重正是这件事唯一会被写错的地方。

    **两臂都刻意取 4 小时的时间差**（``minutes_later=240``，仍在 6 小时窗内）：5 分钟那对
    照片的原始总分超过 1.0，会被上限截断，两条臂的总分都等于 1.0——差值恒为 0，这条
    不变量就退化成恒真。快照本身没问题（截断是 1.0 就有的口径），是**这个测试**必须选一对
    不饱和的输入。
    """
    with_person = _score_second_observation(
        repository_set, person_status="confirmed", tag="with", minutes_later=240
    )
    assert with_person is not None
    without = _score_second_observation(repository_set, person_status=None, tag="without", minutes_later=240)
    assert without is not None

    assert without.total < 1.0, "这对输入被上限截断了，差值不再是权重——换一对不饱和的"
    assert with_person.total < 1.0, "这对输入被上限截断了，差值不再是权重——换一对不饱和的"
    assert round(with_person.total - without.total, 10) == round(WEIGHTS["person"] * 0.8, 10)


def test_the_event_side_is_read_from_the_events_own_observations(repository_set):
    """事件侧读的是**事件名下所有观察**：只挂第一张（事件成员）也够。

    这条钉的是两条来源不是冗余——事件侧那条 JOIN 的是 ``event_observations``，
    观察侧那条只看自己。少任何一条，交集都为空、person_score 都是 0。
    """
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    second = seed(
        repository_set, asset_id="asset_2", observation_id="obs_2", captured_at=BASE + timedelta(minutes=5)
    )
    repository_set["assign"].execute(first)
    add_person(repository_set["db"], "ent_1", status="confirmed")
    link_mention(repository_set["db"], "ent_1", "obs_1")
    link_mention(repository_set["db"], "ent_1", "obs_2")

    result = repository_set["assign"].execute(second)

    assert result.created is False
    assert result.score is not None
    assert result.score.person == 0.8


def _semantic_conflict_pair(repos, *, tag: str, with_person: bool):
    """两张同时间同地点的照片，第二张的**语义与第一张冲突**（活动与事件类型都不同）。

    这正是 ``split_guard`` 的触发条件：语义冲突 + 没有物件/人物做桥 + 视觉不可用。
    """
    first = seed(repos, asset_id=f"asset_A_{tag}", observation_id=f"obs_A_{tag}")
    second = seed(
        repos,
        asset_id=f"asset_B_{tag}",
        observation_id=f"obs_B_{tag}",
        captured_at=BASE + timedelta(minutes=5),
        caption="一家人在餐厅吃饭",
        # 地点**不动**（同一张 visual_place 才拉得住总分、落进 0.50 以上），
        # 冲突只出在活动与事件类型上——split_guard 判的正是这一种。
        activity="吃饭",
        event_type="聚餐",
        objects=("碗",),
    )
    repos["assign"].execute(first)
    if with_person:
        add_person(repos["db"], f"ent_{tag}", status="confirmed")
        link_mention(repos["db"], f"ent_{tag}", f"obs_A_{tag}")
        link_mention(repos["db"], f"ent_{tag}", f"obs_B_{tag}")
    return repos["assign"].execute(second)


def test_semantic_conflict_without_a_person_bridge_splits_the_event(repository_set):
    """对照臂 A：语义冲突、没有人物桥、视觉不可用 → **另起一个事件**。

    ``split_guard`` 一旦触发，``total`` 被直接置 0（``domain/memories/events.py:352``），
    所以这条观察连候选都算不上——产品上的表现是「同一场活动被拆成两个事件」。
    S4 实测的那次拆分（2017-11-05 拆成 4 个事件）走的正是这条路。
    """
    result = _semantic_conflict_pair(repository_set, tag="alone", with_person=False)

    assert result.created is True
    assert result.score is None
    guard = result.event.aggregation_breakdown["split_guard"]
    assert guard == "semantic_visual_conflict"
    assert result.event.aggregation_breakdown["candidates"][0]["confirmed_people"] == 0.0


def test_a_shared_confirmed_person_keeps_the_event_together(repository_set):
    """对照臂 B：**同一份数据**，只多了「两边都出现了同一位已确认的人」。

    这条比分数本身更接近产品行为：家人的同一场活动里，特写与合影本来就不像，视觉差异
    单独不足以拆事件；有一个已知的人做桥（``corroborated``）时就不该拆。S5 之前
    person_score 恒为 0，所以这条桥一直是断的——Task 80 接上之后，同一个人的两张照片
    归并到同一个事件里。
    """
    result = _semantic_conflict_pair(repository_set, tag="together", with_person=True)

    assert result.created is False
    assert result.score is not None
    assert result.score.person == 0.8
    assert result.score.split_guard is None


def test_importing_mixed_timezone_assets_preserves_separate_events(repository_set):
    aware = seed(repository_set, asset_id="aware", observation_id="obs-aware")
    naive = seed(
        repository_set, asset_id="naive", observation_id="obs-naive", captured_at=BASE.replace(tzinfo=None)
    )
    first = repository_set["assign"].execute(aware)
    second = repository_set["assign"].execute(naive)
    assert first.event.id != second.event.id
    assert repository_set["assets"].get("aware").captured_at == BASE
    assert repository_set["assets"].get("naive").captured_at == BASE.replace(tzinfo=None)

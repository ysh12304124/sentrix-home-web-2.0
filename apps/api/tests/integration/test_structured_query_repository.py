"""结构化聚合的 SQL：真 SQLite + 真迁移。

**这个文件主要防的是「两条路径给出两个数」。** 地点判定在 2.0 里有三个孪生
（``places/matching.py`` / ``conditions.py`` / 本模块的 ``place_clause``），
SQL 那一支只要与另外两支差一个空格、一个后缀、一次大小写折叠，表现就是
「走统计路径问同一个问题，结果少几张照片」——而没有任何东西会报错。

所以下面有三组**对照**用例，每组都拿同一份数据、让 Python 与 SQL 各算一遍：

    test_label_sql_is_byte_identical_to_python_place_label     规则 1 的拼接
    test_strip_admin_suffix_sql_follows_the_python_rule        规则 2 的去后缀
    test_place_filter_agrees_with_place_text_matches           整条 WHERE
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sentrix.domain.media.models import (
    Asset,
    AssetStatus,
    GpsPoint,
    MediaType,
    Observation,
)
from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.matching import place_admin_matches, place_label
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.domain.retrieval.query_contracts import Constraint, QuerySpec, Strictness
from sentrix.domain.retrieval.structured_query import GroupCount, derive_filter
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.structured import (
    SqliteStructuredQueryRepository,
    place_label_sql,
    strip_admin_suffix_sql,
)
from sentrix.infrastructure.places.aliases import load_place_aliases

NOW = datetime(2023, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)
HARD = Strictness.HARD


@pytest.fixture
def aliases() -> PlaceAliases:
    return load_place_aliases()


@pytest.fixture
def structured(migrations_ready_db: Database, aliases: PlaceAliases):
    return SqliteStructuredQueryRepository(migrations_ready_db, aliases)


@pytest.fixture
def assets(migrations_ready_db: Database) -> SqliteAssetRepository:
    return SqliteAssetRepository(migrations_ready_db)


@pytest.fixture
def observations(migrations_ready_db: Database) -> SqliteObservationRepository:
    return SqliteObservationRepository(migrations_ready_db)


@pytest.fixture
def places(migrations_ready_db: Database) -> SqliteAssetPlaceRepository:
    return SqliteAssetPlaceRepository(migrations_ready_db)


def add_asset(repo, asset_id: str, *, captured_at: datetime | None = NOW, scope: str = "album1", **kw):
    fields: dict[str, object] = {
        "id": asset_id,
        "scope_id": scope,
        "file_name": f"{asset_id}.jpg",
        "media_type": MediaType.IMAGE,
        "path": f"/tmp/{asset_id}.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 10,
        "status": AssetStatus.PROCESSED,
        "content_sha256": f"sha-{asset_id}",
        "captured_at": captured_at,
        "captured_location": XIAOSHAN,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(kw)
    repo.add(Asset(**fields))  # type: ignore[arg-type]


def add_observation(repo, observation_id: str, asset_id: str, *, place: str = "", **kw):
    fields: dict[str, object] = {
        "id": observation_id,
        "scope_id": "album1",
        "asset_id": asset_id,
        "source_type": "import",
        "captured_at": NOW,
        "confidence": 0.65,
        "created_at": NOW,
        "updated_at": NOW,
        "caption": "一张照片",
        "place": place,
        "activity": "",
        "event_type": "",
        "ocr_text": "",
        "objects": (),
        "clothing": (),
        "revision": 1,
    }
    fields.update(kw)
    repo.add(Observation(**fields))  # type: ignore[arg-type]


def add_place(places, asset_id: str, *, admin=("", "", ""), nearest=("", "", None), gps=XIAOSHAN):
    places.save(
        asset_id=asset_id,
        scope_id="album1",
        gps=gps,
        place=Place(
            admin=AdminArea(*admin),
            nearest=NearestPlace(*nearest),
            precision=PlacePrecision.DISTRICT,
            source=PlaceSource.PYGEOCN,
            gps=gps,
            confidence=0.9,
        ),
    )


def add_mention(database: Database, entity_id: str, observation_id: str, *, index: int) -> None:
    """写一条提及。``entities`` 那一行也得先有——外键是真的开着的。"""
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT OR IGNORE INTO entities (id, scope_id, entity_type, canonical_name,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (entity_id, "album1", "person", "奶奶", "confirmed", NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id,"
            " confidence, created_at) VALUES (?,?,?,?,?,?)",
            (f"em_{index}", entity_id, observation_id, None, 0.9, NOW.isoformat()),
        )


def spec_of(*constraints: Constraint, entity_ids: list[str] | None = None) -> QuerySpec:
    return QuerySpec(
        query_id="q",
        scope_mode="single",
        scope_ids=["album1"],
        viewer_id="owner",
        conversation_id="c",
        intent="answer",
        answer_target="general",
        constraints=list(constraints),
        entity_ids=entity_ids or [],
    )


# ---- 规则 1：label 的拼接，Python 与 SQL 必须逐字符相同 ----------------------------


@pytest.mark.parametrize(
    "admin, nearest",
    [
        (("浙江省", "杭州市", "萧山区"), ("赵州镇", "大石桥村", 270.0)),
        (("", "杭州市", "萧山区"), ("", "", None)),
        (("浙江省", "", ""), ("", "白狮系", None)),
        (("湖北省", "宜昌市", "夷陵区"), ("邓村", "", None)),
        (("", "", ""), ("", "Chiang Mai", None)),
    ],
)
def test_label_sql_is_byte_identical_to_python_place_label(
    migrations_ready_db: Database, places, assets, admin, nearest
):
    """**空槽位是这条用例的全部意义。** Python 是 ``" ".join(非空项)``，SQL 若直接
    ``a || ' ' || b || ' ' || c``，空槽位会在中间留下连续空格——"浙江省 杭州市" 与
    "浙江省  杭州市" 差一个字符，子串匹配的结果就差一批照片，而两边都不会报错。
    """
    add_asset(assets, "asset_1")
    add_place(places, "asset_1", admin=admin, nearest=nearest)
    expected = place_label(
        Place(
            admin=AdminArea(*admin),
            nearest=NearestPlace(*nearest),
            precision=PlacePrecision.DISTRICT,
            source=PlaceSource.PYGEOCN,
            gps=XIAOSHAN,
            confidence=0.9,
        )
    )
    with migrations_ready_db.connect() as connection:
        row = connection.execute(
            f"SELECT {place_label_sql()} AS label FROM asset_places ap WHERE ap.asset_id = ?",
            ("asset_1",),
        ).fetchone()
    assert row["label"] == expected


def test_label_sql_is_empty_when_there_is_no_place_row(migrations_ready_db: Database, assets):
    """**没解析过地点**的资产（``asset_places`` 里没有行）走的是 ``LEFT JOIN``
    的空行。此时五个槽位全是 NULL，而 ``NULL <> ''`` 是 NULL——每一个
    ``CASE`` 都会落到 ``ELSE ''``，拼出来是空串。

    这条不能省：写成 ``CASE WHEN col IS NOT NULL AND col <> '' …`` 之外的形式
    （比如 ``COALESCE(col, '') <> ''`` 漏掉一处），NULL 会顺着 ``||`` 把整个
    label 传染成 NULL，之后 ``instr(NULL, ?)`` 恒为 NULL——**所有按地点的筛选
    都会静默返回零行**，而这看起来与「确实没有匹配的照片」一模一样。
    """
    add_asset(assets, "asset_1")
    with migrations_ready_db.connect() as connection:
        row = connection.execute(
            f"SELECT {place_label_sql()} AS label FROM assets a"
            " LEFT JOIN asset_places ap ON ap.asset_id = a.id WHERE a.id = ?",
            ("asset_1",),
        ).fetchone()
    assert row["label"] == ""


# ---- 规则 2：去后缀，SQL 跟着 Python 的顺序走 ------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "秦皇岛市",
        "赵县",
        "内蒙古自治区",
        "新疆维吾尔自治区",
        "北京市",
        "阿里地区",
        "延边朝鲜族自治州",
        "香港特别行政区",
        "浙江省",
        "省",
        "",
        "鼓浪屿",
    ],
)
def test_strip_admin_suffix_sql_follows_the_python_rule(
    migrations_ready_db: Database, aliases: PlaceAliases, value: str
):
    """``strip_admin_suffix`` 是「碰到第一个命中的后缀就停」，两处必须同序。

    **顺带钉住一个既有缺陷**：``区`` 在 ``地区`` 前面，所以 ``阿里地区`` 去掉的是
    ``区`` 不是 ``地区``，得到 ``阿里地``——而 1.0 的 ``replace`` 连招给的是 ``阿里``。
    也就是说用户说「阿里」时，规则 2 在 2.0 里匹配不上。这个顺序问题在
    ``domain/places/aliases.py``，不在 SQL 这一侧；两条路径**一起**错才是这里要保证的，
    改它要连带改 ``place_text_matches``，不在 T121b 的范围里。
    """
    assert _sql_strip(migrations_ready_db, value, aliases) == aliases.strip_admin_suffix(value)


def _sql_strip(database: Database, value: str, aliases: PlaceAliases) -> str:
    expression = strip_admin_suffix_sql("t.v", aliases.admin_suffixes)
    sql = f"SELECT {expression} AS stripped FROM (SELECT ? AS v) t"
    with database.connect() as connection:
        row = connection.execute(sql, (value,)).fetchone()
    return row["stripped"]


def test_strip_with_no_suffixes_is_the_identity(migrations_ready_db: Database):
    """空后缀表是合法状态（数据文件缺失）。没有 WHEN 的 ``CASE ELSE … END``
    是语法错误——这条用例保证它退化成原值，而不是让整条查询在运行期炸掉。"""
    assert _sql_strip(migrations_ready_db, "杭州市", PlaceAliases(table={}, admin_suffixes=())) == "杭州市"


# ---- 整条 WHERE：与 place_text_matches 对照 --------------------------------------


PLACE_CASES = ("杭州", "浙江省", "萧山区", "宜昌", "Chiang Mai", "清迈", "赵州桥", "")


@pytest.mark.parametrize("value", PLACE_CASES)
def test_place_filter_agrees_with_place_text_matches(
    structured, places, observations, assets, aliases: PlaceAliases, value: str
):
    """同一份数据，SQL 的 WHERE 与 Python 的 ``place_text_matches`` 给出同一个数。

    观察的 ``place`` 一律留空：SQL 那一支比 Python 多一个 ``instr(o.place, …)``
    （1.0 的 ``_place_clause`` 就有，2.0 保留），这是**有意**的宽松，不是这里要
    对照的东西。留空之后两条路径应当逐条相同。

    ``value`` 为空串时 SQL 走 ``1=1``、Python 走 ``text 非空`` 判断——两条路径都在
    「没给地点」时不过滤，这是 1.0 的行为。

    用例用的是 HARD 档，但**严格度在这里不起作用**（1.0 的 ``_place`` 没看过它，
    而 1.0 的工具层发的正是 ``semantic_required``）——见
    ``test_structured_query.py::test_a_semantic_place_constraint_also_enters_the_where_clause``。
    """
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2")
    add_asset(assets, "asset_3")
    add_place(places, "asset_1", admin=("浙江省", "杭州市", "萧山区"), nearest=("赵州镇", "大石桥村", 270.0))
    add_place(places, "asset_2", admin=("湖北省", "宜昌市", "夷陵区"), nearest=("邓村", "白狮系", None))
    add_place(places, "asset_3", admin=("", "", ""), nearest=("", "Chiang Mai", None))
    for asset_id in ("asset_1", "asset_2", "asset_3"):
        add_observation(observations, f"obs_{asset_id}", asset_id, place="")

    expected = sum(
        1
        for asset_id in ("asset_1", "asset_2", "asset_3")
        if value == "" or place_admin_matches(value, places.get(asset_id), aliases)
    )
    filter = derive_filter(spec_of(Constraint("place", value, HARD, "asset_metadata")), scope_id="album1")

    assert structured.count(filter) == expected


@pytest.mark.parametrize("value", ["A_B", "A%B"])
def test_a_wildcard_in_the_value_is_not_a_wildcard(structured, places, assets, value: str):
    """``instr`` 不是 ``LIKE``。地名字符串里的 ``_`` / ``%`` 在 ``LIKE`` 下是通配符，
    一个下划线就能让这条子句变成「什么都匹配」——而结果只是数字偏大。

    **存储值必须是 ``AXB``（三字符）**，不是 ``AB``：第一次写这条用例时用了
    ``AB``，而模式 ``%A_B%`` 要三个字符才匹配得上，于是 ``LIKE`` 与 ``instr``
    给出同一个答案，用例对这两种实现完全没有区分力。定点变异把这个假绿抓了出来。
    """
    add_asset(assets, "asset_1")
    add_place(places, "asset_1", admin=("", "AXB", ""), nearest=("", "", None))

    filter = derive_filter(spec_of(Constraint("place", value, HARD, "asset_metadata")), scope_id="album1")

    assert structured.count(filter) == 0


# ---- 基础子句 --------------------------------------------------------------------


def test_scope_id_none_counts_every_album(structured, assets):
    """``None`` 是「全部相册」。**它必须原样传下去**：在这里兜底成空串会让
    ``scope_id = ''`` 一条都不匹配，而结果只是「一张都没有」。"""
    add_asset(assets, "asset_1", scope="album1")
    add_asset(assets, "asset_2", scope="album2")

    assert structured.count(derive_filter(spec_of(), scope_id=None)) == 2
    assert structured.count(derive_filter(spec_of(), scope_id="album1")) == 1


def test_an_empty_scope_id_matches_nothing(structured, assets):
    """空串与 ``None`` 不是一回事——上面那条的另一半。"""
    add_asset(assets, "asset_1", scope="album1")

    assert structured.count(derive_filter(spec_of(), scope_id="")) == 0


def test_time_bounds_are_left_closed_right_open(structured, assets):
    """``2023年10月`` 收 ``10-01T00:00:00`` 到 ``10-31T23:59:59``，**不收** 11-01 零点。

    **这里必须用 naive 的 ``datetime``。** 第一次写这条用例时用的是
    ``tzinfo=UTC``，于是 ``asset_4`` 落库成 ``2023-11-01T00:00:00+00:00``，比上界
    ``2023-11-01T00:00:00`` 多一个尾巴、字典序更大——把 ``<`` 改成 ``<=`` 之后它
    照样被排除，用例照样绿。**它绿的原因不是边界开闭，是那个时区后缀**，而真正
    要守的那件事（右开）根本没被约束到。定点变异把这个假绿抓了出来。

    真实库里的 ``captured_at`` 就是 naive 的（EXIF 直接读，``2017-11-05T15:05:13``），
    所以 naive 才是这份数据该有的样子。
    """
    add_asset(assets, "asset_1", captured_at=datetime(2023, 9, 30, 23, 59, 59))
    add_asset(assets, "asset_2", captured_at=datetime(2023, 10, 1, 0, 0, 0))
    add_asset(assets, "asset_3", captured_at=datetime(2023, 10, 31, 23, 59, 59))
    add_asset(assets, "asset_4", captured_at=datetime(2023, 11, 1, 0, 0, 0))

    filter = derive_filter(spec_of(Constraint("time", "2023年10月", HARD, "asset_metadata")))

    assert structured.count(filter) == 2


def test_time_bounds_compare_as_strings_so_offsets_are_a_hazard(structured, assets):
    """**钉住一个已知的坑，不是断言它是对的。**

    时间区间是拿**字符串**比大小的（``a.captured_at >= ?``）。同一列里的值只要
    全是同一个偏移量，字典序就等于时间序——今天成立：库里全是 EXIF 读出来的
    naive 值。但一条带非 UTC 偏移的记录进来，字典序就不再等于时间序：

        ``2023-10-31T20:00:00-05:00`` 是 UTC 的 ``2023-11-01T01:00:00``，
        却排在 ``2023-11-01T00:00:00`` **前面**，于是被算进了 10 月。

    修法不在这一层（把区间解析成 ``datetime`` 再比也没用，SQL 里比的是列），
    而在写入侧归一：``captured_at`` 落库前统一成一个偏移量。见 T121b 的收尾记录。
    """
    outside_in_real_time = datetime(2023, 10, 31, 20, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    add_asset(assets, "asset_1", captured_at=outside_in_real_time)

    filter = derive_filter(spec_of(Constraint("time", "2023年10月", HARD, "asset_metadata")))

    assert structured.count(filter) == 1


def test_an_asset_without_captured_at_is_not_in_a_time_range(structured, assets):
    """``captured_at`` 为 NULL 的资产不落在任何时间区间里——``>=`` 对 NULL 是 NULL，
    它会静默地既不在「这个月」也不在「上个月」，而不是报错。"""
    add_asset(assets, "asset_1", captured_at=None)

    filter = derive_filter(spec_of(Constraint("time", "2023年", HARD, "asset_metadata")))

    assert structured.count(filter) == 0


def test_media_include_and_exclude(structured, assets):
    add_asset(assets, "asset_1", media_type=MediaType.IMAGE)
    add_asset(assets, "asset_2", media_type=MediaType.VIDEO)

    include = derive_filter(spec_of(Constraint("media", "image", HARD, "asset_metadata")))
    exclude = derive_filter(spec_of(Constraint("media", "video", HARD, "asset_metadata", negated=True)))

    assert structured.count(include) == 1
    assert structured.count(exclude) == 1


def test_a_person_filter_counts_each_asset_once(structured, assets, observations, migrations_ready_db):
    """一个人在一张照片里有两条观察（两个提及），那张照片只算一次——
    ``COUNT(DISTINCT a.id)``。写成 ``COUNT(*)`` 会随观察条数翻倍。"""
    add_asset(assets, "asset_1")
    add_observation(observations, "obs_1", "asset_1")
    add_observation(observations, "obs_2", "asset_1")
    for index, observation_id in enumerate(("obs_1", "obs_2"), start=1):
        add_mention(migrations_ready_db, "entity_1", observation_id, index=index)

    filter = derive_filter(spec_of(entity_ids=["entity_1"]), scope_id="album1")

    assert structured.count(filter) == 1


def test_an_asset_without_observations_is_excluded_by_a_person_filter(
    structured, assets, observations, migrations_ready_db
):
    """有人物条件时是内连接：没有观察行的资产无从证明「这个人出现过」。
    写成左连接会让它留在结果里，而那个结果只是「多一张」。"""
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2")
    add_observation(observations, "obs_1", "asset_1")
    add_mention(migrations_ready_db, "entity_1", "obs_1", index=1)

    filter = derive_filter(spec_of(entity_ids=["entity_1"]), scope_id="album1")

    assert structured.count(filter) == 1


# ---- 极值与分组 ------------------------------------------------------------------


def test_captured_bound_picks_the_extremes(structured, assets):
    """取的是**落库原样的字符串**，不是解析回来的 ``datetime``。

    带 ``tzinfo`` 的值存进去会多一个 ``+00:00`` 尾巴，而真实库里的
    ``captured_at`` 是从 EXIF 直接读的 naive 值（``2017-11-05T15:05:13``）——
    **同一列里两种格式并存**。所以这里断言的是「原样取回」，没有做任何归一：
    真要做归一，得先决定「naive 的值算哪个时区」，那是另一件事。
    """
    add_asset(assets, "asset_1", captured_at=datetime(2019, 5, 1, tzinfo=UTC))
    add_asset(assets, "asset_2", captured_at=datetime(2023, 8, 6, 20, 58, 17, tzinfo=UTC))
    add_asset(assets, "asset_3", captured_at=datetime(2021, 1, 1, tzinfo=UTC))

    filter = derive_filter(spec_of())

    earliest = datetime(2019, 5, 1, tzinfo=UTC).isoformat()
    newest = datetime(2023, 8, 6, 20, 58, 17, tzinfo=UTC).isoformat()
    assert structured.captured_bound(filter, latest=False) == earliest
    assert structured.captured_bound(filter, latest=True) == newest


def test_captured_bound_on_an_empty_result_is_none(structured):
    assert structured.captured_bound(derive_filter(spec_of(), scope_id="album1"), latest=True) is None


def test_group_by_month_counts_and_orders_by_count(structured, assets):
    add_asset(assets, "asset_1", captured_at=datetime(2023, 10, 1, tzinfo=UTC))
    add_asset(assets, "asset_2", captured_at=datetime(2023, 10, 20, tzinfo=UTC))
    add_asset(assets, "asset_3", captured_at=datetime(2023, 8, 6, tzinfo=UTC))

    counts = structured.group_counts(derive_filter(spec_of()), "month")

    assert counts == (GroupCount("2023-10", 2), GroupCount("2023-08", 1))


def test_a_time_group_skips_assets_without_captured_at(structured, assets):
    """按时间分组时要求 ``captured_at`` 非空——否则会多出一个 ``空字符串`` 分组，
    而它看起来就像一个「不知道什么时候拍的」的正常月份。"""
    add_asset(assets, "asset_1", captured_at=datetime(2023, 10, 1, tzinfo=UTC))
    add_asset(assets, "asset_2", captured_at=None)

    groups = [item.group for item in structured.group_counts(derive_filter(spec_of()), "month")]

    assert groups == ["2023-10"]


def test_group_by_media(structured, assets):
    add_asset(assets, "asset_1", media_type=MediaType.IMAGE)
    add_asset(assets, "asset_2", media_type=MediaType.VIDEO)

    counts = structured.group_counts(derive_filter(spec_of()), "media")

    assert set(counts) == {GroupCount("image", 1), GroupCount("video", 1)}


def test_an_unknown_group_key_falls_back_to_month(structured, assets):
    add_asset(assets, "asset_1", captured_at=datetime(2023, 10, 1, tzinfo=UTC))

    assert [item.group for item in structured.group_counts(derive_filter(spec_of()), "季度")] == ["2023-10"]


@pytest.mark.parametrize(
    "admin, nearest, observation_place, gps, expected",
    [
        (("浙江省", "杭州市", ""), ("", "", None), "", XIAOSHAN, "杭州市"),
        (("", "", ""), ("", "白狮系", None), "", XIAOSHAN, "白狮系"),
        (("", "", ""), ("", "", None), "公园", XIAOSHAN, "公园"),
        (("", "", ""), ("", "", None), "", XIAOSHAN, "有GPS坐标"),
        (("", "", ""), ("", "", None), "", None, "未知"),
    ],
)
def test_group_by_place_reads_the_slots_in_order(
    structured, assets, places, observations, admin, nearest, observation_place, gps, expected
):
    """地点分组的取值顺序。行政面 > 最近邻点 > 观察里的场景词 > 有坐标 > 未知——
    1.0 的顺序是「反编码 city > o.place > captured_location > 未知」，
    2.0 把 ``captured_location`` 换成了它真实的含义（有坐标）。"""
    add_asset(assets, "asset_1", captured_location=gps)
    if admin != ("", "", "") or nearest != ("", "", None):
        add_place(places, "asset_1", admin=admin, nearest=nearest, gps=gps)
    add_observation(observations, "obs_1", "asset_1", place=observation_place)

    counts = structured.group_counts(derive_filter(spec_of()), "place")

    assert counts == (GroupCount(expected, 1),)


def test_group_by_place_is_deterministic_with_several_observations(structured, assets, observations):
    """一个资产有多条观察时，「取哪一条的 place」必须是个确定的答案。

    1.0 直接读 ``LEFT JOIN`` 上来的 ``o.place``，取到哪一行由 SQLite 的扫描顺序
    决定——同一个问题问两次可能给出不同的分组名。这里固定取**最近一条非空的**。
    """
    add_asset(assets, "asset_1", captured_location=None)
    add_observation(
        observations, "obs_1", "asset_1", place="公园", captured_at=datetime(2023, 1, 1, tzinfo=UTC)
    )
    add_observation(
        observations, "obs_2", "asset_1", place="观景台", captured_at=datetime(2023, 9, 1, tzinfo=UTC)
    )

    assert [item.group for item in structured.group_counts(derive_filter(spec_of()), "place")] == ["观景台"]

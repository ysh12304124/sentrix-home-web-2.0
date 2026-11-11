"""``person_bridge`` 词法投影跟着人物快照走，实体通道在「全部相册」下也能按人召回。

修的是同一个缺陷的两半（见 ``docs/refactor/2026-11-11-entity-channel-all-albums.md``）：

- **写侧**：投影原先只在导入时写，那时还没人被确认，``person_bridge`` 恒为空；确认人物
  只改 ``observations.people_json``，不刷投影。现在 ``link_confirmed_mention`` 在同一个
  事务里重写这条观察的 ``person_bridge``，存量由 ``rebuild_person_bridge`` 回填。
- **读侧**：实体通道查不到投影时退回扫观察快照，而那条兜底在 scope 为空时直接返回空集。
  投影修好之后，全部相册走 ``assets_for_person(name, scope_id=None)``，并且只覆盖
  ``include_in_people = 1`` 的空间（评测空间不进家庭问答）。

这里的用例**一律不手动调** ``refresh_from_observation``：手动刷一下再断言「投影里有人名」，
在修之前也是绿的——那正是这个缺陷能活这么久的原因。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex

HOME = "home-default"
SECOND_HOME = "album-grandparents"
BENCH = "PhotoBench-album3"
BASE = datetime(2024, 5, 1, 9, 0, tzinfo=UTC)
NAME = "王王"
REPO_ROOT = Path(__file__).resolve().parents[4]
REPOSITORIES = (
    REPO_ROOT / "apps" / "api" / "src" / "sentrix" / "infrastructure" / "persistence" / "repositories"
)


def add_space(db: Database, space_id: str, *, include_in_people: bool) -> None:
    now = BASE.isoformat()
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (space_id, space_id, 1 if include_in_people else 0, now, now),
        )


@pytest.fixture
def library(migrations_ready_db: Database, tmp_path: Path):
    """三个空间各两张图：两个家庭空间、一个评测空间（``include_in_people = 0``）。"""
    db = migrations_ready_db
    assets = SqliteAssetRepository(db)
    observations = SqliteObservationRepository(db)
    places = SqliteAssetPlaceRepository(db)
    index = SqliteLexicalIndex(db, observations, assets=assets, places=places)
    add_space(db, HOME, include_in_people=True)
    add_space(db, SECOND_HOME, include_in_people=True)
    add_space(db, BENCH, include_in_people=False)

    media = tmp_path / "media"
    media.mkdir()
    for scope in (HOME, SECOND_HOME, BENCH):
        for n in (1, 2):
            asset_id = f"{scope}-asset{n}"
            path = media / f"{asset_id}.jpg"
            path.write_bytes(asset_id.encode())
            assets.add(
                Asset(
                    id=asset_id,
                    scope_id=scope,
                    file_name=f"{asset_id}.jpg",
                    media_type=MediaType.IMAGE,
                    path=str(path),
                    mime_type="image/jpeg",
                    size_bytes=1,
                    status=AssetStatus.PROCESSED,
                    content_sha256=f"sha-{asset_id}",
                    captured_at=BASE,
                    captured_location=None,
                    created_at=BASE,
                    updated_at=BASE,
                )
            )
            observation = Observation(
                id=f"obs-{asset_id}",
                scope_id=scope,
                asset_id=asset_id,
                source_type="import",
                captured_at=BASE,
                confidence=0.7,
                created_at=BASE,
                updated_at=BASE,
                caption="客厅里",
            )
            observations.add(observation)
            # 导入路径的最后一步：此时还没有人被确认，person_bridge 写不出任何行。
            index.refresh_from_observation(observation)
    return {
        "db": db,
        "observations": observations,
        "entities": SqliteEntityRepository(db),
        "index": index,
    }


def confirm(library, scope: str, *observation_ids: str, name: str = NAME) -> str:
    entities = library["entities"]
    entity = entities.create_person(name, status="confirmed", scope_id=scope)
    for observation_id in observation_ids:
        entities.link_confirmed_mention(entity.id, observation_id)
    return entity.id


def bridge_rows(db: Database) -> list[tuple[str, str, str]]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT observation_id, scope_id, normalized_value FROM observation_search_terms"
            " WHERE field_type = 'person_bridge' ORDER BY observation_id"
        ).fetchall()
    return [(row["observation_id"], row["scope_id"], row["normalized_value"]) for row in rows]


def bridge_fts_rows(db: Database) -> list[tuple[str, str]]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT observation_id, tokens FROM observation_search_fts"
            " WHERE field_type = 'person_bridge' ORDER BY observation_id"
        ).fetchall()
    return [(row["observation_id"], row["tokens"]) for row in rows]


def retriever(library) -> RetrieveEntity:
    return RetrieveEntity(index=library["index"], observations=library["observations"])


# ---- 写侧：确认之后投影里就有人名 -------------------------------------------------


def test_confirming_a_person_writes_the_name_into_the_projection(library):
    """确认（写提及）之后，不用任何重建，``person_bridge`` 里就有这个名字。"""
    assert bridge_rows(library["db"]) == []

    confirm(library, HOME, f"obs-{HOME}-asset1")

    assert bridge_rows(library["db"]) == [(f"obs-{HOME}-asset1", HOME, NAME)]
    # FTS 那一份也要跟着写：词法通道按 token 查的正是它。
    assert bridge_fts_rows(library["db"]) == [(f"obs-{HOME}-asset1", NAME)]


def test_confirming_twice_does_not_duplicate_the_projection(library):
    """快照追加是幂等的，投影也必须是：重复确认不多出一行。"""
    entity_id = confirm(library, HOME, f"obs-{HOME}-asset1")
    library["entities"].link_confirmed_mention(entity_id, f"obs-{HOME}-asset1")

    assert bridge_rows(library["db"]) == [(f"obs-{HOME}-asset1", HOME, NAME)]
    assert len(bridge_fts_rows(library["db"])) == 1


def test_two_people_on_one_photo_both_land_in_the_projection(library):
    """第二个人确认到同一张图上时，第一个人的行不能被冲掉（按快照全量重写，不是覆盖成最后一个）。"""
    confirm(library, HOME, f"obs-{HOME}-asset1")
    confirm(library, HOME, f"obs-{HOME}-asset1", name="李李")

    assert [row[2] for row in bridge_rows(library["db"])] == [NAME, "李李"]


def test_the_sync_and_the_full_refresh_write_the_same_rows(library):
    """``sync_person_bridge`` 与 ``refresh_from_observation`` 是同一个口径：重建一次不改变结果。"""
    confirm(library, HOME, f"obs-{HOME}-asset1")
    before = bridge_rows(library["db"])

    observation = library["observations"].get(f"obs-{HOME}-asset1")
    assert observation is not None
    library["index"].refresh_from_observation(observation)

    assert bridge_rows(library["db"]) == before


def test_the_other_fields_survive_a_person_sync(library):
    """只重写 ``person_bridge``：同一条观察的 caption / 锚点行不能被顺手删掉。"""
    with library["db"].connect() as connection:
        before = connection.execute(
            "SELECT COUNT(*) AS n FROM observation_search_terms"
            " WHERE observation_id = ? AND field_type != 'person_bridge'",
            (f"obs-{HOME}-asset1",),
        ).fetchone()["n"]
    assert before > 0

    confirm(library, HOME, f"obs-{HOME}-asset1")

    with library["db"].connect() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS n FROM observation_search_terms"
            " WHERE observation_id = ? AND field_type != 'person_bridge'",
            (f"obs-{HOME}-asset1",),
        ).fetchone()["n"]
    assert after == before


# ---- 回填：存量库的快照里有人、投影里没有 ----------------------------------------


def write_snapshot_behind_the_projection(db: Database, observation_id: str, name: str) -> None:
    """模拟修之前确认过的人：快照里有，投影里没有。"""
    with db.connect() as connection, connection:
        connection.execute(
            "UPDATE observations SET people_json = ? WHERE id = ?",
            (f'[{{"entity_id": "ent_old", "name": "{name}", "status": "confirmed"}}]', observation_id),
        )


def test_rebuilding_the_person_bridge_backfills_old_confirmations(library):
    write_snapshot_behind_the_projection(library["db"], f"obs-{HOME}-asset2", NAME)
    write_snapshot_behind_the_projection(library["db"], f"obs-{BENCH}-asset2", NAME)
    assert bridge_rows(library["db"]) == []

    written = library["index"].rebuild_person_bridge()

    assert written == 2
    assert bridge_rows(library["db"]) == [
        (f"obs-{BENCH}-asset2", BENCH, NAME),
        (f"obs-{HOME}-asset2", HOME, NAME),
    ]
    # 幂等：再跑一次行数不变、不重复。
    assert library["index"].rebuild_person_bridge() == 2
    assert len(bridge_rows(library["db"])) == 2
    assert len(bridge_fts_rows(library["db"])) == 2


def test_rebuilding_one_scope_leaves_the_others_alone(library):
    write_snapshot_behind_the_projection(library["db"], f"obs-{HOME}-asset2", NAME)
    write_snapshot_behind_the_projection(library["db"], f"obs-{SECOND_HOME}-asset2", NAME)

    assert library["index"].rebuild_person_bridge(HOME) == 1

    assert bridge_rows(library["db"]) == [(f"obs-{HOME}-asset2", HOME, NAME)]


def test_rebuilding_drops_names_that_left_the_snapshot(library):
    """快照里的人没了（合并改名、取消确认），重建要把投影里的旧名删掉，不是只增不减。"""
    confirm(library, HOME, f"obs-{HOME}-asset1")
    with library["db"].connect() as connection, connection:
        connection.execute("UPDATE observations SET people_json = '[]' WHERE id = ?", (f"obs-{HOME}-asset1",))

    assert library["index"].rebuild_person_bridge() == 0
    assert bridge_rows(library["db"]) == []
    assert bridge_fts_rows(library["db"]) == []


# ---- 读侧：全部相册按人召回 ------------------------------------------------------


@pytest.mark.parametrize("all_albums", [None, ""])
def test_all_albums_recalls_the_person_across_household_spaces(library, all_albums):
    """``scope_id`` 为空（路由传 ``None``，助手工具传 ``""``）时跨家庭空间召回。

    修之前这里是空表：投影里没有人名，兜底在 scope 为空时不跑。
    """
    confirm(library, HOME, f"obs-{HOME}-asset1")
    confirm(library, SECOND_HOME, f"obs-{SECOND_HOME}-asset2")

    hits = retriever(library).execute(people=[NAME], scope_id=all_albums, limit=10)

    assert [hit.asset_id for hit in hits] == [f"{SECOND_HOME}-asset2", f"{HOME}-asset1"]


def test_all_albums_excludes_spaces_kept_out_of_people(library):
    """评测空间（``include_in_people = 0``）里同名的人不进全部相册——与 ``list_entities`` 同口径。"""
    confirm(library, HOME, f"obs-{HOME}-asset1")
    confirm(library, BENCH, f"obs-{BENCH}-asset1", f"obs-{BENCH}-asset2")

    hits = retriever(library).execute(people=[NAME], scope_id=None, limit=10)

    assert [hit.asset_id for hit in hits] == [f"{HOME}-asset1"]


def test_an_explicit_evaluation_scope_still_finds_its_people(library):
    """**对照臂**：评测按自己的 scope 查时必须查得到，排除只作用于全部相册。"""
    confirm(library, BENCH, f"obs-{BENCH}-asset1")

    hits = retriever(library).execute(people=[NAME], scope_id=BENCH, limit=10)

    assert [hit.asset_id for hit in hits] == [f"{BENCH}-asset1"]


def test_all_albums_finds_backfilled_people(library):
    """存量库回填之后，全部相册就能按人召回（真实库上的那条路径）。"""
    write_snapshot_behind_the_projection(library["db"], f"obs-{HOME}-asset2", NAME)
    assert retriever(library).execute(people=[NAME], scope_id=None, limit=10) == []

    library["index"].rebuild_person_bridge()

    hits = retriever(library).execute(people=[NAME], scope_id=None, limit=10)
    assert [hit.asset_id for hit in hits] == [f"{HOME}-asset2"]


# ---- 守卫：每一条改人物快照的写入路径都要同步投影 ----------------------------------


def test_every_people_snapshot_write_also_syncs_the_projection():
    """仓储层里任何 ``UPDATE observations SET people_json`` 的模块都必须调 ``sync_person_bridge``。

    这是静态守卫，不是行为测试：确认之外，人物合并、取消确认等路径之后也会改快照，
    漏一处就是「快照里有、投影里没有」（或者反过来，合并之后旧名字还查得到）。
    """
    offenders = [
        path.name
        for path in sorted(REPOSITORIES.glob("*.py"))
        if "SET people_json" in (text := path.read_text(encoding="utf-8"))
        and "sync_person_bridge(" not in text
    ]
    assert offenders == []

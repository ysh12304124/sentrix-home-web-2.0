"""``observation_search_terms`` + ``observation_search_fts`` 仓储。SQL 只在这一层。

对应 1.0 ``backend/retrieval_indexes.py:105-345`` 的 ``RetrievalIndex``。

三处与 1.0 的差异，都在别处记了理由：

1. ``refresh_from_observation`` 的**调用点**是导入路径（1.0 有这个方法但生产零调用，
   见 ``s4-deviations.md`` §1 第 4 项）；
2. ``rebuild_scope`` 的删除范围按 scope 收敛，1.0 的 scoped rebuild 会先清空整张 FTS
   再只补一个 scope（``retrieval_indexes.py:271-295`` 的注释就是在记这个 bug）；
3. 不提供 LIKE 子串回退（``search``）——FTS5 缺席时的降级分支在 2.0 永远不会走；
4. ``person_bridge`` 跟着人物快照走（``sync_person_bridge``）：1.0 只在导入时写投影，
   确认人物之后投影里没有人名，实体通道全靠扫观察兜底；而兜底在「全部相册」下不跑，
   于是全部相册按人找照片召回为 0。见 ``docs/refactor/2026-11-11-entity-channel-all-albums.md``。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sentrix.domain.media.models import Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.places.ports import AssetPlaceRepository
from sentrix.domain.retrieval.lexical import (
    FIELD_SOURCES,
    AnchorTerm,
    anchor_terms,
    fts_content,
    normalize_text,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import people_names

# 1.0 ``rebuild_all`` 用的是 ``list_observations(limit=10_000)``（``:272``）。同一个上限：
# 词法投影是派生数据，一次重建漏掉第 10001 条之后的行会静默丢召回，所以重建范围
# 必须与 1.0 一样**有界且被写出来**，而不是一个看起来无穷大的默认值。
REBUILD_LIMIT = 10_000

# 1.0 ``:150-158`` 的 ``term_sources`` 七条。``person_bridge`` 在 S5b 之前是唯一缺席的
# 一条（身份层属于 S5，没有 ``entity_mentions`` 可桥），所以这一版**不再过滤**：
# 加过滤等于留一个「字段清单与 1.0 对齐、实际少一个」的假象。
_ACTIVE_FIELD_SOURCES = FIELD_SOURCES

PERSON_BRIDGE = "person_bridge"

# 「全部相册」（``scope_id`` 为空）按人找照片时要排除的空间。与
# ``EntityRepository.list_entities`` / ``FaceRepository.list_clusters`` 同一条口径：
# ``include_in_people = 0`` 的空间（评测空间）不进家庭的人物视图，也就不进家庭问答。
# 写成「NOT IN 排除集」而不是「IN 包含集」也与 ``list_entities`` 一致：没登记进
# ``memory_spaces`` 的老 scope 仍然算家庭数据。
_EXCLUDED_FROM_PEOPLE = "(SELECT id FROM memory_spaces WHERE include_in_people = 0)"


def _new_id() -> str:
    return f"term_{uuid.uuid4().hex[:16]}"


def _values(raw: object) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple, set)):
        items: Sequence[object] = list(raw)
    else:
        items = [raw]
    return tuple(value for value in (normalize_text(item) for item in items) if value)


def _insert_term(connection: sqlite3.Connection, row: Sequence[object]) -> None:
    """写一行 ``observation_search_terms``，并在有 token 时补一行 FTS。

    ``row`` 的列序与 INSERT 一致：``(id, observation_id, asset_id, scope_id, field_type,
    normalized_value, confidence, source_type, source_revision, created_at, updated_at)``。
    """
    connection.execute(
        "INSERT INTO observation_search_terms (id, observation_id, asset_id, scope_id,"
        " field_type, normalized_value, confidence, source_type, source_revision,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        tuple(row),
    )
    tokens = fts_content(row[5])
    if not tokens:
        return
    connection.execute(
        "INSERT INTO observation_search_fts (tokens, scope_id, field_type, asset_id,"
        " observation_id) VALUES (?,?,?,?,?)",
        (tokens, row[3], row[4], row[2], row[1]),
    )


def sync_person_bridge(connection: sqlite3.Connection, observation_ids: Iterable[str]) -> int:
    """按 ``observations.people_json`` 的**当前值**重写这些观察的 ``person_bridge`` 行。

    **必须在改 ``people_json`` 的同一个连接、同一个事务里调。** 人物快照是权威，
    ``person_bridge`` 是它的派生行；两者分两次提交，中间崩一次就是「快照里有这个人、
    按人名查不到」——那正是这次要修的缺陷（确认、合并都改快照，却没有一处刷投影）。
    所以它是一个吃 ``connection`` 的函数，而不是 ``SqliteLexicalIndex`` 上自己开连接的
    方法：写快照的仓储（``entities.py`` 的 ``_append_person_snapshot``，以及之后任何改
    ``people_json`` 的写入路径）在自己的事务里调它。

    只碰 ``person_bridge`` 这一个字段：其他字段（caption / objects / 锚点……）的值源
    不随人物变化，整条 ``refresh_from_observation`` 会多读 asset 与地点、多写几十行，
    而且要在事务外做。名字的解析与归一和 ``refresh_from_observation`` 是同一条路
    （``people_names`` → ``_values``），两条写入给出同样的行。

    观察不存在时只删不写（观察被删的那条路径自己清投影，这里不报错）。返回写了几行。
    """
    now = datetime.now(UTC).isoformat()
    written = 0
    for observation_id in dict.fromkeys(observation_ids):
        connection.execute(
            "DELETE FROM observation_search_terms WHERE observation_id = ? AND field_type = ?",
            (observation_id, PERSON_BRIDGE),
        )
        connection.execute(
            "DELETE FROM observation_search_fts WHERE observation_id = ? AND field_type = ?",
            (observation_id, PERSON_BRIDGE),
        )
        row = connection.execute(
            "SELECT id, scope_id, asset_id, confidence, revision, people_json FROM observations WHERE id = ?",
            (observation_id,),
        ).fetchone()
        if row is None:
            continue
        for value in _values(people_names(row["people_json"])):
            _insert_term(
                connection,
                (
                    _new_id(),
                    row["id"],
                    row["asset_id"],
                    row["scope_id"],
                    PERSON_BRIDGE,
                    value,
                    float(row["confidence"] or 0),
                    "observation",
                    int(row["revision"] or 0),
                    now,
                    now,
                ),
            )
            written += 1
    return written


class SqliteLexicalIndex:
    """实现 ``LexicalIndex`` 与 ``LexicalSearch`` 两个端口。"""

    def __init__(
        self,
        database: Database,
        observations: ObservationRepository,
        *,
        assets: AssetRepository,
        places: AssetPlaceRepository,
    ) -> None:
        self._database = database
        self._observations = observations
        # 锚点列（geo / time / file）的值来自 **asset** 而不是 observation，所以写侧必须
        # 拿得到 asset 与它解析出的地点。两个都是必填：缺了它们只会静默少写三列，
        # 而那正是这一版要修的缺陷（见 ``docs/refactor/s4-deviations.md`` §7）。
        self._assets = assets
        self._places = places

    # ---- 写入 ----------------------------------------------------------------

    def refresh_from_observation(self, observation: Observation) -> int:
        """删掉这条 Observation 的旧行，按当前 revision 重写词法投影。

        「先删后插」而不是 UPSERT：一条 Observation 的字段数会因为重新富集而**变少**
        （VLM 第二轮把 objects 收敛掉了），增量更新留不下这种删除。
        """
        now = datetime.now(UTC).isoformat()
        rows = self._term_rows(observation, now)
        with self._database.connect() as connection, connection:
            from sentrix.infrastructure.persistence.media_barrier import require_active

            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            require_active(connection, observation.asset_id)
            connection.execute(
                "DELETE FROM observation_search_terms WHERE observation_id = ?",
                (observation.id,),
            )
            connection.execute(
                "DELETE FROM observation_search_fts WHERE observation_id = ?", (observation.id,)
            )
            for row in rows:
                _insert_term(connection, row)
        return len(rows)

    def _term_rows(self, observation: Observation, now: str) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for field_type, value in _observation_terms(observation):
            rows.append(
                (
                    _new_id(),
                    observation.id,
                    observation.asset_id,
                    observation.scope_id,
                    field_type,
                    value,
                    observation.confidence,
                    "observation",
                    observation.revision,
                    now,
                    now,
                )
            )
        for term in self._anchor_terms(observation):
            rows.append(
                (
                    _new_id(),
                    observation.id,
                    observation.asset_id,
                    observation.scope_id,
                    term.field_type,
                    term.normalized_value,
                    term.confidence,
                    term.source_type,
                    observation.revision,
                    now,
                    now,
                )
            )
        return rows

    def _anchor_terms(self, observation: Observation) -> tuple[AnchorTerm, ...]:
        """1.0 ``retrieval_indexes.py:225-245``：从 asset 的 file_name / captured_at 与
        解析出的地点生成 geo / time / file 三列锚点。

        调用时点与 1.0 相同（写在 ``refresh_from_observation`` 里），而导入路径的
        「地点解析 → 观察 → 词法投影」顺序保证这里读得到 ``asset_places`` 那一行
        （``import_assets.py`` 的模块注释把顺序写成了硬约束）。
        """
        asset = self._assets.get(observation.asset_id)
        if asset is None:
            return ()
        place = self._places.get(observation.asset_id)
        return anchor_terms(
            file_name=asset.file_name,
            captured_at=asset.captured_at.isoformat() if asset.captured_at else "",
            place_text=place.searchable_text() if place is not None else "",
        )

    def rebuild_scope(self, scope_id: str) -> int:
        """按 scope 重建。**先按 scope 删**，不清整表。

        1.0 的 ``rebuild_all(scope_id)`` 无条件 ``DELETE FROM observation_search_fts``
        然后只补这一个 scope 的行，其他相册的词法通道被静默清空——1.0 自己的注释
        （``retrieval_indexes.py:278-284``）就是在记这个 bug 的修复。
        """
        rebuilt = 0
        for observation in self._observations.list_scope(scope_id, limit=REBUILD_LIMIT):
            rebuilt += self.refresh_from_observation(observation)
        return rebuilt

    def rebuild_person_bridge(self, scope_id: str | None = None) -> int:
        """只重建 ``person_bridge``：按每条观察当前的 ``people_json`` 重写（存量回填用）。

        ``scope_id`` 为空时覆盖**所有**观察——回填是在修数据，评测空间的投影同样该
        与快照一致（读侧的「全部相册」口径另由 ``assets_for_person`` 负责）。

        **不设上限，一个事务。** ``rebuild_scope`` 有 ``REBUILD_LIMIT`` 是因为整条重建
        要逐条读 asset 与地点；这里每条观察只是几句本地 SQL。设一个上限，超出的那部分
        就会静默地「快照里有人、投影里没有」——这一版要消灭的正是这件事。一个事务是为了
        回填中途失败时不留下一半新一半旧的投影。返回写了几行。
        """
        with self._database.connect() as connection, connection:
            if scope_id:
                rows = connection.execute(
                    "SELECT id FROM observations WHERE scope_id = ? ORDER BY id", (scope_id,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT id FROM observations ORDER BY id").fetchall()
            return sync_person_bridge(connection, [row["id"] for row in rows])

    def count(self, scope_id: str | None = None) -> int:
        with self._database.connect() as connection:
            if scope_id:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM readable_observation_search_terms WHERE scope_id = ?",
                    (scope_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM readable_observation_search_terms"
                ).fetchone()
        return int(row["n"]) if row else 0

    # ---- 读取 ----------------------------------------------------------------

    def search_fts(self, query: str, *, scope_id: str | None, limit: int) -> Sequence[tuple[str, int]]:
        """每个 query token 独立查一次，按 asset 累计命中数。

        返回 ``(asset_id, 命中 token 数)``。**不同 token 命中同一个 asset 才累加**——
        同一个 token 命中多条 observation（同一 asset 有多条观察）只算一次，
        与 1.0 ``retrieval_indexes.py:316-322`` 的 ``token_hits`` 语义一致。
        """
        from sentrix.domain.retrieval.lexical import query_tokens

        tokens = query_tokens(query)
        if not tokens:
            return []
        hits: dict[str, int] = {}
        with self._database.connect() as connection:
            for token in tokens:
                clause = (
                    "WHERE observation_search_fts MATCH ? AND asset_id IN (SELECT id FROM readable_assets)"
                )
                params: list[object] = [f'"{token}"']
                if scope_id:
                    clause += " AND scope_id = ?"
                    params.append(scope_id)
                try:
                    rows = connection.execute(
                        f"SELECT DISTINCT asset_id FROM observation_search_fts {clause}", params
                    ).fetchall()
                except Exception:
                    # FTS5 的 MATCH 语法对含引号/运算符的 token 会抛。1.0 同样吞掉这一条
                    # （``retrieval_indexes.py:311-315``），跳过该 token 继续。
                    continue
                for row in rows:
                    hits[row["asset_id"]] = hits.get(row["asset_id"], 0) + 1
        return sorted(hits.items(), key=lambda item: (-item[1], item[0]))[:limit]

    def assets_for_person(self, name: str, *, scope_id: str | None) -> frozenset[str]:
        """含这个人的 asset 集合（1.0 ``retrieval/entity.py:35-46`` 那条查询）。

        ``field_type`` 是硬条件：不加它，同名的一个地名或物品也会把 asset 带进来。
        名字走 ``normalize_text``（与写入侧同一个口径）——写成 ``name.lower()`` 会在
        全角空格或多空格的名字上悄悄漏掉。

        ``scope_id`` 为空是「全部相册」：只覆盖 ``include_in_people = 1`` 的空间，与
        ``EntityRepository.list_entities`` 同一条口径。评测空间里也有同名的人（PhotoBench
        的相册是真实家庭照片的副本），不排除的话全部相册下问「王王」会把评测副本里的
        同一批照片再交一遍。显式给了 ``scope_id`` 就只看那个空间，不再过滤——评测按
        自己的 scope 查，必须查得到。
        """
        value = normalize_text(name)
        if not value:
            return frozenset()
        clause = " WHERE field_type = ? AND normalized_value = ?"
        params: list[object] = [PERSON_BRIDGE, value]
        if scope_id:
            clause += " AND scope_id = ?"
            params.append(scope_id)
        else:
            clause += f" AND scope_id NOT IN {_EXCLUDED_FROM_PEOPLE}"
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT asset_id FROM readable_observation_search_terms" + clause, params
            ).fetchall()
        return frozenset(row["asset_id"] for row in rows)

    def exact_assets(self, whole_query: str, *, scope_id: str | None) -> frozenset[str]:
        """库里存在 ``normalized_value`` 与整串 query **完全相等**的行的 asset 集合。"""
        whole = normalize_text(whole_query)
        if not whole:
            return frozenset()
        clause = " WHERE normalized_value = ?"
        params: list[object] = [whole]
        if scope_id:
            clause += " AND scope_id = ?"
            params.append(scope_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT asset_id FROM readable_observation_search_terms" + clause, params
            ).fetchall()
        return frozenset(row["asset_id"] for row in rows)


def _observation_terms(observation: Observation) -> list[tuple[str, str]]:
    """把一条 Observation 展开成 ``(field_type, normalized_value)`` 列表。

    1.0 的 ``_terms_from_field`` + ``_terms_from_detail``（``retrieval_indexes.py:70-100``）。
    ``detail`` 那一路在 2.0 不建——S4 的 Observation 没有 ``detail`` 字段，
    ``visible_details/regions/text_blocks`` 属于 S6 的 OCR/VLM 精读。
    """
    terms: list[tuple[str, str]] = []
    for field_type, attribute in _ACTIVE_FIELD_SOURCES:
        raw = getattr(observation, attribute, None)
        for value in _values(raw):
            terms.append((field_type, value))
    return terms

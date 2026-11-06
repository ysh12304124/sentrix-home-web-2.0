"""结构化统计、极值和分组。时间与语义检索共用拍摄当地日历/显式时区规则；地点只统计已确定行政归属。附近点留给候选检索，不当作确定归属计数。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.matching import admin_text_matches
from sentrix.domain.retrieval.structured_query import (
    TIME_GROUP_KEYS,
    AssetSample,
    GroupCount,
    StructuredFilter,
    normalize_group_by,
)
from sentrix.infrastructure.persistence.database import Database

# ``place_label`` 参与拼接的槽位，顺序与 ``domain/places/matching.py:_LABEL`` 一致。
_LABEL_SLOTS = (
    "admin_province",
    "admin_city",
    "admin_district",
    "nearest_township",
    "nearest_locality",
)
# 1.0 ``_place_clause`` 做第 2 条规则时只看这三个（``country`` 不在其中：拿国名去
# 匹配会命中一切）。
_ADMIN_SLOTS = ("admin_province", "admin_city", "admin_district")


def _literal(value: str) -> str:
    """SQL 字符串字面量。后缀表来自 JSON 数据文件，**不是**代码里的常量。"""
    return "'" + str(value).replace("'", "''") + "'"


def place_label_sql(alias: str = "ap") -> str:
    """``place_label(place)`` 的 SQL 孪生。

    先判空再决定要不要跟一个空格，最后 ``TRIM`` 掉尾巴——等价于 Python 的
    ``" ".join(非空项)``。直接 ``a || ' ' || b || ' ' || c`` 在空槽位处会留下
    连续空格，于是 ``instr(label, '浙江省杭州市')`` 这类查询静默少算。
    """
    parts = [
        f"CASE WHEN {alias}.{slot} <> '' THEN {alias}.{slot} || ' ' ELSE '' END" for slot in _LABEL_SLOTS
    ]
    return "TRIM(" + " || ".join(parts) + ")"


def strip_admin_suffix_sql(column: str, suffixes: Sequence[str]) -> str:
    """``PlaceAliases.strip_admin_suffix`` 的 SQL 孪生：按顺序只砍**一个**后缀。

    砍两次会把 ``自治区`` 砍成 ``自治``（``aliases.py`` 的 docstring 已说明为什么
    不砍两次），所以这里也是碰到第一个命中的后缀就停——顺序跟着 ``suffixes`` 走。

    ``length()`` 与 ``substr()`` 在 SQLite 里按**字符**计数（TEXT 值），与 Python 的
    ``len()`` 一致；UTF-8 字节数在这里是个坑，所以不能用 ``length(CAST(x AS BLOB))``
    之类看起来更快的东西。
    """
    whens = []
    for suffix in suffixes:
        text = str(suffix)
        size = len(text)
        if not text:
            continue
        whens.append(
            f"WHEN length({column}) > {size} AND substr({column}, -{size}) = {_literal(text)}"
            f" THEN substr({column}, 1, length({column}) - {size})"
        )
    if not whens:
        # 空后缀表是合法状态（数据文件缺失时 alias 表会退化成空）。
        # 没有 WHEN 的 ``CASE ELSE … END`` 是语法错误，所以这里必须提前返回。
        return column
    return f"CASE {' '.join(whens)} ELSE {column} END"


def place_clause(value: str, aliases: PlaceAliases) -> tuple[str, list[object]]:
    """整条查询由已确定的行政层级组成才成立。"""
    text = value.strip()
    if not text:
        return "1=1", []
    return (
        "place_admin_supported(?, ap.admin_province, ap.admin_city, ap.admin_district)",
        [text],
    )


def build_base(filter: StructuredFilter, aliases: PlaceAliases) -> tuple[str, str, list[object]]:
    """``StructuredFilter`` → ``(joins, where, params)``。

    ``scope_id=None`` 表示**全部相册**，此时不拼 scope 子句——写成
    ``scope_id = ''`` 会一条都不匹配，而结果看起来与「这个相册真的没照片」一样。

    时间边界是**左闭右开**且用完整 ISO 串：``captured_at`` 列存的就是
    ``2017-11-05T15:05:13``，而 ``>= '2017-11-05'`` 与 ``>= '2017-11-05T00:00:00'``
    在字典序上不是一回事。
    """
    joins = []
    if filter.entity_ids:
        # 有人物条件时是内连接：没有观察行的资产本来就无从证明「这个人出现过」。
        joins.append("JOIN observations o ON o.asset_id = a.id")
        joins.append("JOIN entity_mentions em ON em.observation_id = o.id")
    else:
        joins.append("LEFT JOIN observations o ON o.asset_id = a.id")
    # 地点子句要读 ap，无论这次筛不筛地点都挂上——挂不挂由 SQL 计划决定代价，
    # 但「筛地点时忘了搭这张表」会变成一个 no such column，而不是一个错数。
    joins.append("LEFT JOIN asset_places ap ON ap.asset_id = a.id")

    clauses: list[str] = []
    params: list[object] = []
    if filter.scope_id is not None:
        clauses.append("a.scope_id = ?")
        params.append(filter.scope_id)
    if filter.media_include:
        clauses.append(f"a.media_type IN ({', '.join('?' * len(filter.media_include))})")
        params.extend(filter.media_include)
    for value in filter.media_exclude:
        clauses.append("a.media_type != ?")
        params.append(value)
    if filter.time_start or filter.time_end:
        clauses.append("capture_in_bounds(a.captured_at, ?, ?)")
        params.extend((filter.time_start, filter.time_end))
    if filter.place:
        clause, place_params = place_clause(filter.place, aliases)
        clauses.append(clause)
        params.extend(place_params)
    if filter.entity_ids:
        clauses.append(f"em.entity_id IN ({', '.join('?' * len(filter.entity_ids))})")
        params.extend(filter.entity_ids)
    if not clauses:
        clauses.append("1=1")
    where = " AND ".join(f"({clause})" for clause in clauses)
    return " ".join(joins), where, params


# 该资产最近一条非空观察的地点。用相关子查询而不是左连接上来的 ``o.place``：
# 一个资产可以有多个观察行，直接读 ``o.place`` 取到哪一行由扫描顺序决定。
_LATEST_OBSERVATION_PLACE = (
    "(SELECT op.place FROM observations op"
    " WHERE op.asset_id = a.id AND op.place <> ''"
    " ORDER BY capture_sort_key(op.captured_at) DESC, op.id DESC LIMIT 1)"
)

_GROUP_EXPR = {
    "month": "substr(a.captured_at, 1, 7)",
    "year": "substr(a.captured_at, 1, 4)",
    "date": "substr(a.captured_at, 1, 10)",
    "media": "a.media_type",
    # 1.0 把 status 这一支写在 ``_query_memory_facts`` 的 raw SQL 里（``COALESCE(NULLIF(...))``），
    # 没进 ``_GROUP_EXPR``。2.0 收进来：分组表达式集中在 ``group_expression`` 一处，
    # 调用方不必为了一个键去走私有的 SQL 分支。
    # ``NULLIF`` 把空串折成 NULL 再 ``COALESCE`` 到 'unknown'——空串与「没有状态」
    # 在报表里是同一个意思，而空串单独成一组会让报表多出一行看起来像状态的东西。
    "status": "COALESCE(NULLIF(a.status, ''), 'unknown')",
    "place": (
        "CASE"
        " WHEN ap.admin_city <> '' THEN ap.admin_city"
        " WHEN ap.nearest_locality <> '' THEN ap.nearest_locality"
        f" WHEN COALESCE({_LATEST_OBSERVATION_PLACE}, '') <> '' THEN {_LATEST_OBSERVATION_PLACE}"
        " WHEN a.gps_latitude IS NOT NULL THEN '有GPS坐标'"
        " ELSE '未知' END"
    ),
}


def group_expression(group_by: str) -> str:
    """分组键 → SQL 表达式。认不出的键在 ``normalize_group_by`` 里就退回了月，
    所以 ``_GROUP_EXPR`` 的索引是全的——这里不留 ``.get(k, 月)`` 那种兜底，
    它会把「表写漏了一个键」变成「静默按月份分组」。"""
    return _GROUP_EXPR[normalize_group_by(group_by)]


class SqliteStructuredQueryRepository:
    """结构化聚合。筛选口径由 ``StructuredFilter`` 决定，这里只把它写成 SQL。

    ``aliases`` 是必填的：给它一个默认空表，会让「忘了接线」表现为「中英地名
    永远匹配不上」，而那是查不出来的——只有一批照片悄悄地不在结果里。
    """

    def __init__(self, database: Database, aliases: PlaceAliases) -> None:
        self._database = database
        self._aliases = aliases

    def _rows(self, sql: str, params: Sequence[object]) -> list[sqlite3.Row]:
        with self._database.connect() as connection:
            connection.create_function(
                "place_admin_supported",
                4,
                lambda value, province, city, district: admin_text_matches(
                    value, (province or "", city or "", district or ""), self._aliases
                ),
                deterministic=True,
            )
            return connection.execute(sql, tuple(params)).fetchall()

    def count(self, filter: StructuredFilter) -> int:
        joins, where, params = build_base(filter, self._aliases)
        rows = self._rows(f"SELECT COUNT(DISTINCT a.id) AS n FROM assets a {joins} WHERE {where}", params)
        return int(rows[0]["n"] or 0) if rows else 0

    def captured_bound(self, filter: StructuredFilter, *, latest: bool) -> str | None:
        """跨offset按绝对时刻取极值；未知时区另组，不假定UTC。"""
        joins, where, params = build_base(filter, self._aliases)
        direction = "DESC" if latest else "ASC"
        rows = self._rows(
            f"SELECT a.captured_at AS v FROM assets a {joins} WHERE {where}"
            " AND a.captured_at IS NOT NULL"
            f" ORDER BY capture_sort_key(a.captured_at) {direction}, a.id LIMIT 1",
            params,
        )
        return rows[0]["v"] if rows and rows[0]["v"] else None

    def group_counts(self, filter: StructuredFilter, group_by: str) -> tuple[GroupCount, ...]:
        joins, where, params = build_base(filter, self._aliases)
        expression = group_expression(group_by)
        time_filter = " AND a.captured_at IS NOT NULL" if group_by in TIME_GROUP_KEYS else ""
        rows = self._rows(
            f"SELECT {expression} AS g, COUNT(DISTINCT a.id) AS n FROM assets a {joins}"
            f" WHERE {where}{time_filter} GROUP BY g ORDER BY n DESC, g",
            params,
        )
        return tuple(GroupCount(group=row["g"], count=int(row["n"] or 0)) for row in rows)

    def sample_observations(self, filter: StructuredFilter, *, limit: int) -> tuple[AssetSample, ...]:
        """按筛选条件取几条代表观察，按拍摄时间倒序。

        子查询的别名是 ``o2`` 不是 ``o``：``o`` 已经被 ``build_base`` 的左连接占用了，
        而 SQLite 里相关子查询会先看到外层作用域里的那个 ``o``——写成 ``o.caption``
        拿到的是**连接上来的那一行**的 caption，不是「这个资产最近一条观察」的，
        并且不报错。
        """
        joins, where, params = build_base(filter, self._aliases)
        rows = self._rows(
            "SELECT a.id AS asset_id, a.captured_at, a.media_type,"
            " (SELECT COALESCE(o2.caption, '') FROM observations o2"
            "  WHERE o2.asset_id = a.id ORDER BY capture_sort_key(o2.captured_at) DESC LIMIT 1) AS caption"
            f" FROM assets a {joins} WHERE {where}"
            " ORDER BY capture_sort_key(a.captured_at) DESC, a.id DESC LIMIT ?",
            [*params, limit],
        )
        return tuple(
            AssetSample(
                asset_id=row["asset_id"],
                captured_at=row["captured_at"],
                media_type=row["media_type"],
                caption=row["caption"] or "",
            )
            for row in rows
        )


__all__ = [
    "SqliteStructuredQueryRepository",
    "build_base",
    "group_expression",
    "place_clause",
    "place_label_sql",
    "strip_admin_suffix_sql",
]

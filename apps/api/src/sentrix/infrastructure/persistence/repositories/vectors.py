"""向量权威存储的 SQLite 仓储。SQL 只出现在这一层。

``memory_vectors`` 与 ``text_vectors`` 的列结构完全一致，只有表名不同，所以实现由
``VectorTableRepository`` 提供、两个子类各钉一张表：视觉/人脸写 ``memory_vectors``，
文本写 ``text_vectors``。端口 ``VectorRepository`` 不变——分表是存储细节。

精确回退（``search_exact``）是**功能**不是兜底：Qdrant 目录被别的进程锁住、
collection 还没建、或者维度对不上时，检索必须还能给出正确答案，只是慢。
1.0 的同类回退存在但把降级写进了没人看的日志；这里由调用方（application 层）
拿 ``VectorIndexStatus`` 决定要不要告诉用户。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sentrix.domain.retrieval.ports import VectorHit, VectorRecord
from sentrix.domain.retrieval.similarity import cosine_similarity
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, space, source_type, source_id, model_name, dimension,"
    " vector_json, metadata_json, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_list(raw: str | None) -> list[float]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [float(item) for item in value]


def _decode_map(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


class VectorTableRepository:
    """一张与 ``VectorRecord`` 同构的表的实现；子类只需钉 ``_TABLE``。

    表名是类常量而不是构造参数：两张表的选择在装配点就定了，运行期换表只会制造
    「同一份数据两个写入目标」的隐患。
    """

    _TABLE = ""

    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert(self, record: VectorRecord) -> None:
        if not record.vector:
            raise ValueError("拒绝写入空向量：1.0 的空向量会被静默 upsert，事后无人发现")
        timestamp = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                f"INSERT INTO {self._TABLE} ("
                f" {_COLUMNS}"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(space, source_type, source_id, model_name) DO UPDATE SET"
                " scope_id = excluded.scope_id, dimension = excluded.dimension,"
                " vector_json = excluded.vector_json, metadata_json = excluded.metadata_json,"
                " updated_at = excluded.updated_at",
                (
                    f"vec_{uuid.uuid4().hex[:12]}",
                    record.scope_id,
                    record.space,
                    record.source_type,
                    record.source_id,
                    record.model_name,
                    record.dimension,
                    json.dumps([float(value) for value in record.vector]),
                    json.dumps(dict(record.metadata), ensure_ascii=False, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )

    def update_metadata(
        self, *, space: str, source_type: str, source_id: str, metadata: dict[str, object]
    ) -> int:
        """只改 metadata，不动向量本身。

        **不带 ``model_name`` 条件**：同一个实例在同一空间下可能有多个模型版本的行
        （换过嵌入模型之后新旧并存），而 ``cluster_id`` 是实例的属性，不是模型版本的
        属性——只改最新那一条会让旧行继续指着已经被撤掉的簇。1.0 的对应 UPDATE
        （``db.py:5034-5037``）同样不带模型条件。
        """
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                f"UPDATE {self._TABLE} SET metadata_json = ?, updated_at = ?"
                " WHERE space = ? AND source_type = ? AND source_id = ?",
                (
                    json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True),
                    _now(),
                    space,
                    source_type,
                    source_id,
                ),
            )
            return int(cursor.rowcount)

    def has_vector(self, *, space: str, source_type: str, source_id: str, model_name: str) -> bool:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM {self._TABLE} WHERE space = ? AND source_type = ?"
                " AND source_id = ? AND model_name = ? LIMIT 1",
                (space, source_type, source_id, model_name),
            ).fetchone()
        return row is not None

    def get(
        self,
        *,
        space: str,
        source_type: str,
        source_id: str,
        model_name: str | None = None,
    ) -> VectorRecord | None:
        sql = f"SELECT {_COLUMNS} FROM {self._TABLE} WHERE space = ? AND source_type = ? AND source_id = ?"
        params: list[Any] = [space, source_type, source_id]
        if model_name:
            sql += " AND model_name = ?"
            params.append(model_name)
        # 1.0 ``db.py:2085`` 就是 ``ORDER BY updated_at DESC LIMIT 1``。同一 asset 被
        # 重新嵌入过之后库里会有多条，取最新的那条才是当前模型的向量。
        sql += " ORDER BY updated_at DESC LIMIT 1"
        with self._database.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        if row is None:
            return None
        return VectorRecord(
            scope_id=row["scope_id"],
            space=row["space"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            model_name=row["model_name"],
            vector=tuple(_decode_list(row["vector_json"])),
            metadata=_decode_map(row["metadata_json"]),
        )

    def count(self, *, space: str, model_name: str | None = None) -> int:
        """按 (space, 可选 model) 计数。

        模型维度必须可选且**默认不过滤**：应用层要用同一个口径去和 Qdrant 的
        collection 计数比对，两边只要有一边多带了过滤条件，比对就恒不成立。
        """
        sql = f"SELECT COUNT(*) FROM {self._TABLE} WHERE space = ?"
        params: list[Any] = [space]
        if model_name:
            sql += " AND model_name = ?"
            params.append(model_name)
        with self._database.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        return int(row[0]) if row else 0

    def search_exact(
        self,
        *,
        space: str,
        vector: Sequence[float],
        limit: int,
        scope_id: str | None = None,
        model_name: str | None = None,
    ) -> Sequence[VectorHit]:
        if not vector or limit < 1:
            return []
        rows = self._fetch(space=space, scope_id=scope_id, model_name=model_name)
        scored: list[VectorHit] = []
        for row in rows:
            candidate = _decode_list(row["vector_json"])
            # 维度不一致的行直接跳过：拿 768 维查询去和 512 维旧向量算余弦，
            # 结果恒为 0.0，会以「相似度很低」的形态混进结果里而不是被识别为脏数据。
            if len(candidate) != len(vector):
                continue
            scored.append(
                VectorHit(
                    source_id=row["source_id"],
                    scope_id=row["scope_id"],
                    source_type=row["source_type"],
                    model_name=row["model_name"],
                    score=cosine_similarity(vector, candidate),
                    metadata=_decode_map(row["metadata_json"]),
                )
            )
        # 同分按 source_id 兜底，保证同一份数据每次给出同一个名次。
        scored.sort(key=lambda item: (-item.score, item.source_id))
        return scored[:limit]

    def _fetch(self, *, space: str, scope_id: str | None, model_name: str | None) -> list[sqlite3.Row]:
        sql = f"SELECT {_COLUMNS} FROM {self._TABLE} WHERE space = ?"
        params: list[Any] = [space]
        if scope_id:
            sql += " AND scope_id = ?"
            params.append(scope_id)
        if model_name:
            sql += " AND model_name = ?"
            params.append(model_name)
        with self._database.connect() as connection:
            return list(connection.execute(sql, tuple(params)).fetchall())


class SqliteVectorRepository(VectorTableRepository):
    """视觉/人脸向量的权威存储（``memory_vectors``）。"""

    _TABLE = "memory_vectors"

"""文本向量表与视觉向量表的隔离。

契约测试（``test_vector_repository.py``）已经证明两套实现行为一致；这里只钉两件
契约测不到的事：

1. 写文本行**不会**落进 ``memory_vectors``，写视觉行也不会落进 ``text_vectors``；
2. ``text_vectors`` 的 ``CHECK`` 把 ``visual`` 空间挡在库层。
"""

from __future__ import annotations

import sqlite3

import pytest

from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.text_vectors import (
    SqliteTextVectorRepository,
)
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository


def text_record(source_id: str = "obs_1", *, space: str = "semantic") -> VectorRecord:
    return VectorRecord(
        scope_id="album1",
        space=space,
        source_type="observation",
        source_id=source_id,
        model_name=scheme.TEXT_MODEL,
        vector=(1.0, 0.0),
        metadata={"asset_id": "asset_1"},
    )


def visual_record(source_id: str = "asset_1") -> VectorRecord:
    return VectorRecord(
        scope_id="album1",
        space=scheme.VISUAL_SPACE,
        source_type="asset",
        source_id=source_id,
        model_name=scheme.IMAGE_MODEL,
        vector=(1.0, 0.0),
    )


def count(database: Database, table: str) -> int:
    with database.connect() as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_text_rows_never_land_in_memory_vectors(migrations_ready_db: Database):
    text = SqliteTextVectorRepository(migrations_ready_db)

    text.upsert(text_record())

    assert text.count(space="semantic") == 1
    assert count(migrations_ready_db, "text_vectors") == 1
    assert count(migrations_ready_db, "memory_vectors") == 0


def test_visual_rows_never_land_in_text_vectors(migrations_ready_db: Database):
    visual = SqliteVectorRepository(migrations_ready_db)

    visual.upsert(visual_record())

    assert visual.count(space="visual") == 1
    assert count(migrations_ready_db, "memory_vectors") == 1
    assert count(migrations_ready_db, "text_vectors") == 0


def test_text_table_rejects_the_visual_space(migrations_ready_db: Database):
    """``CHECK`` 是库层事实：绕过仓储直接 INSERT 也写不进去。"""
    with migrations_ready_db.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO text_vectors (id, scope_id, space, source_type, source_id,"
            " model_name, dimension, vector_json, metadata_json, created_at, updated_at)"
            " VALUES ('vec_bad','album1','visual','asset','asset_1','m',2,'[1.0,0.0]',"
            " '{}','2026-01-01','2026-01-01')"
        )


def test_episodic_rows_are_accepted_by_the_text_table(migrations_ready_db: Database):
    text = SqliteTextVectorRepository(migrations_ready_db)

    text.upsert(text_record("evt_1", space="episodic"))

    assert text.count(space="episodic") == 1

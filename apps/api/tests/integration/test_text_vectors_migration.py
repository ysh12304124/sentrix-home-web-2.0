"""文本向量迁移维护逻辑：copy / verify / reverse / purge / restore-old。

脚本不在覆盖率范围内，但它是这次拆分里唯一会**改生产数据**的部件，所以行为必须被
真 SQLite + 真迁移钉住：dry-run 不写、复制保留 id、冲突显式中止、反向同步覆盖增删改、
purge 前必有备份且只删文本空间。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.text_vectors import (
    SqliteTextVectorRepository,
)
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

MODULE_PATH = Path(__file__).resolve().parents[4] / "scripts" / "maintenance" / "text_vector_migration.py"
_spec = importlib.util.spec_from_file_location("text_vector_migration", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
migration = importlib.util.module_from_spec(_spec)
sys.modules["text_vector_migration"] = migration
_spec.loader.exec_module(migration)


def text_record(source_id: str, *, space: str = "semantic", vector: tuple[float, ...] = (1.0, 0.0)):
    return VectorRecord(
        scope_id="album1",
        space=space,
        source_type="observation",
        source_id=source_id,
        model_name="text-model",
        vector=vector,
        metadata={"asset_id": f"asset_{source_id}"},
    )


def visual_record(source_id: str = "asset_1"):
    return VectorRecord(
        scope_id="album1",
        space="visual",
        source_type="asset",
        source_id=source_id,
        model_name="visual-model",
        vector=(1.0, 0.0),
    )


def rows(database: Database, table: str) -> list[dict[str, Any]]:
    with database.connect() as connection:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]


def count(database: Database, table: str) -> int:
    with database.connect() as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture
def seeded(migrations_ready_db: Database) -> Database:
    repository = SqliteVectorRepository(migrations_ready_db)
    repository.upsert(text_record("obs_1"))
    repository.upsert(text_record("obs_2", space="episodic", vector=(0.0, 1.0)))
    repository.upsert(visual_record())
    return migrations_ready_db


def test_copy_dry_run_reports_without_writing(seeded: Database):
    with seeded.connect() as connection:
        result = migration.copy_rows(connection)

    assert result.ok
    assert result.dry_run
    assert result.source_rows == 2
    assert result.details["planned_inserts"] == 2
    assert result.inserted == 0
    assert count(seeded, "text_vectors") == 0
    assert count(seeded, "memory_vectors") == 3


def test_copy_apply_preserves_ids_and_is_idempotent(seeded: Database):
    old_ids = {row["id"] for row in rows(seeded, "memory_vectors") if row["space"] != "visual"}

    with seeded.connect() as connection:
        first = migration.copy_rows(connection, apply=True)
    with seeded.connect() as connection:
        second = migration.copy_rows(connection, apply=True)

    assert first.ok and first.inserted == 2
    assert second.ok and second.inserted == 0 and second.skipped == 2
    copied = rows(seeded, "text_vectors")
    assert {row["id"] for row in copied} == old_ids
    assert {row["source_id"] for row in copied} == {"obs_1", "obs_2"}
    assert count(seeded, "memory_vectors") == 3


def test_verify_reports_missing_and_content_conflicts(seeded: Database):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    with seeded.connect() as connection:
        connection.execute("DELETE FROM text_vectors WHERE source_id = 'obs_1'")
        connection.execute("UPDATE text_vectors SET vector_json = '[0.5,0.5]' WHERE source_id = 'obs_2'")
        connection.commit()

    with seeded.connect() as connection:
        result = migration.verify(connection)

    assert not result.ok
    assert result.conflict_counts()["missing_in_new"] == 1
    assert result.conflict_counts()["vector_mismatch"] == 1


def test_verify_reports_dimension_mismatch(seeded: Database):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    with seeded.connect() as connection:
        connection.execute("UPDATE text_vectors SET dimension = 3 WHERE source_id = 'obs_1'")
        connection.commit()

    with seeded.connect() as connection:
        result = migration.verify(connection)

    assert result.conflict_counts()["dimension_mismatch"] == 1


def test_copy_aborts_on_content_conflict_without_writing(seeded: Database):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    with seeded.connect() as connection:
        connection.execute("UPDATE text_vectors SET vector_json = '[0.5,0.5]' WHERE source_id = 'obs_1'")
        connection.commit()

    with seeded.connect() as connection:
        result = migration.copy_rows(connection, apply=True)

    assert not result.ok
    assert result.conflict_counts()["vector_mismatch"] == 1
    assert result.updated == 0
    assert count(seeded, "text_vectors") == 2


def test_reverse_syncs_inserts_updates_and_deletes(seeded: Database):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    # 切换后的三种变化：更新一条、删除一条、新增一条。
    with seeded.connect() as connection:
        connection.execute("UPDATE text_vectors SET vector_json = '[0.5,0.5]' WHERE source_id = 'obs_1'")
        connection.execute("DELETE FROM text_vectors WHERE source_id = 'obs_2'")
        connection.commit()
    text = SqliteTextVectorRepository(seeded)
    text.upsert(text_record("obs_3", vector=(0.0, 0.0, 1.0)))

    with seeded.connect() as connection:
        dry = migration.reverse_rows(connection, policy="prefer-new")
    assert dry.dry_run
    assert dry.details["planned_updates"] == 1
    assert dry.details["planned_deletes"] == 1
    assert dry.details["planned_inserts"] == 1

    with seeded.connect() as connection:
        applied = migration.reverse_rows(connection, apply=True, policy="prefer-new")

    assert applied.ok
    assert applied.updated == 1 and applied.deleted == 1 and applied.inserted == 1
    old = {row["source_id"]: row for row in rows(seeded, "memory_vectors") if row["space"] != "visual"}
    assert set(old) == {"obs_1", "obs_3"}
    assert old["obs_1"]["vector_json"] == "[0.5,0.5]"


def test_reverse_refuses_empty_source(seeded: Database):
    with seeded.connect() as connection:
        result = migration.reverse_rows(connection, apply=True)

    assert not result.ok
    assert result.details["refused"] == "empty source"
    assert count(seeded, "memory_vectors") == 3


def test_purge_requires_backup_path(seeded: Database):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    with seeded.connect() as connection:
        result = migration.purge_rows(connection, db_path=seeded.path, backup_path=None, apply=True)

    assert not result.ok
    assert count(seeded, "memory_vectors") == 3


def test_purge_refuses_when_verification_fails(seeded: Database, tmp_path: Path):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    with seeded.connect() as connection:
        connection.execute("DELETE FROM text_vectors WHERE source_id = 'obs_1'")
        connection.commit()
    backup = tmp_path / "pre-purge.db"

    with seeded.connect() as connection:
        result = migration.purge_rows(connection, db_path=seeded.path, backup_path=backup, apply=True)

    assert not result.ok
    assert not backup.exists()
    assert count(seeded, "memory_vectors") == 3


def test_purge_backs_up_and_deletes_only_text_rows(seeded: Database, tmp_path: Path):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    backup = tmp_path / "pre-purge.db"

    with seeded.connect() as connection:
        result = migration.purge_rows(connection, db_path=seeded.path, backup_path=backup, apply=True)

    assert result.ok
    assert result.deleted == 2
    assert backup.exists()
    assert backup.with_name(backup.name + ".sha256").exists()
    assert count(seeded, "memory_vectors") == 1
    assert count(seeded, "text_vectors") == 2
    assert rows(seeded, "memory_vectors")[0]["space"] == "visual"


def test_restore_old_then_reverse_recovers_after_purge(seeded: Database, tmp_path: Path):
    with seeded.connect() as connection:
        migration.copy_rows(connection, apply=True)
    backup = tmp_path / "pre-purge.db"
    with seeded.connect() as connection:
        migration.purge_rows(connection, db_path=seeded.path, backup_path=backup, apply=True)
    # 备份之后又写入的新行：restore-old 不包含它，reverse 负责补上。
    SqliteTextVectorRepository(seeded).upsert(text_record("obs_3", vector=(0.0, 0.0, 1.0)))
    assert count(seeded, "memory_vectors") == 1

    with seeded.connect() as connection:
        restored = migration.restore_old_rows(connection, backup_path=backup, apply=True)
    with seeded.connect() as connection:
        reversed_result = migration.reverse_rows(connection, apply=True)

    assert restored.ok and restored.inserted == 2
    assert reversed_result.ok and reversed_result.inserted == 1
    assert count(seeded, "memory_vectors") == 4
    assert count(seeded, "text_vectors") == 3

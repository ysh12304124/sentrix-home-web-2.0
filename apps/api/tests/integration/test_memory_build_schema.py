"""M1 表结构的契约。表不存在、唯一键或 FK 漂移都要在这里红。"""

from __future__ import annotations

from sentrix.infrastructure.persistence.database import Database


def _tables(db: Database) -> set[str]:
    with db.connect() as connection:
        return {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def _columns(db: Database, table: str) -> set[str]:
    with db.connect() as connection:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _unique_columns(db: Database, table: str) -> set[tuple[str, ...]]:
    with db.connect() as connection:
        unique_indexes = [
            str(row[1])
            for row in connection.execute(f"PRAGMA index_list({table})")
            if int(row[2]) == 1
        ]
        return {
            tuple(str(column[2]) for column in connection.execute(f"PRAGMA index_info({name})"))
            for name in unique_indexes
        }


def test_m1_tables_exist(migrations_ready_db: Database) -> None:
    assert {"observation_builds", "memory_evidence", "memory_build_tasks"} <= _tables(
        migrations_ready_db
    )


def test_observation_builds_columns_and_unique_key(migrations_ready_db: Database) -> None:
    columns = _columns(migrations_ready_db, "observation_builds")
    assert {
        "id",
        "scope_id",
        "asset_id",
        "observation_id",
        "observation_revision",
        "stage",
        "status",
        "build_key",
        "input_hash",
        "model_name",
        "protocol_version",
        "prompt_hash",
        "params_json",
        "input_transform_json",
        "raw_response_ref",
        "error_category",
        "error",
        "attempt",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    } <= columns
    assert ("build_key",) in _unique_columns(migrations_ready_db, "observation_builds")


def test_memory_evidence_columns(migrations_ready_db: Database) -> None:
    assert {
        "scope_id",
        "owner_kind",
        "owner_id",
        "field_path",
        "role",
        "value_json",
        "value_hash",
        "source_kind",
        "source_modality",
        "source_channel",
        "locator_json",
        "observation_revision",
        "build_id",
        "correction_id",
        "status",
    } <= _columns(migrations_ready_db, "memory_evidence")


def test_memory_build_tasks_unique_key(migrations_ready_db: Database) -> None:
    assert {
        "scope_id",
        "asset_id",
        "stage",
        "build_key",
        "status",
        "attempts",
        "next_attempt_at",
        "last_error",
        "lease_until",
    } <= _columns(migrations_ready_db, "memory_build_tasks")
    assert ("build_key", "stage") in _unique_columns(migrations_ready_db, "memory_build_tasks")

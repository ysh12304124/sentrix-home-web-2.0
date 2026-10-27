import sqlite3

import pytest

from sentrix.infrastructure.persistence.database import Database, MigrationError


def test_migrations_are_applied_once_and_recorded(tmp_path):
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_a.sql").write_text("CREATE TABLE a (id TEXT PRIMARY KEY);")
    (tmp_path / "migrations" / "0002_b.sql").write_text("CREATE TABLE b (id TEXT PRIMARY KEY);")

    db.migrate()
    db.migrate()  # 幂等

    with db.connect() as conn:
        applied = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert applied == ["0001", "0002"]
    assert {"a", "b", "schema_migrations"} <= tables


def test_failed_migration_rolls_back(tmp_path):
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_bad.sql").write_text(
        "CREATE TABLE ok (id TEXT); CREATE TABLE ok (id TEXT);"  # 第二次建表必失败
    )

    try:
        db.migrate()
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("expected OperationalError")

    with db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        applied = list(conn.execute("SELECT version FROM schema_migrations"))

    assert "ok" not in tables
    assert applied == []


def test_migrate_fails_when_no_migrations_are_found(tmp_path):
    """有迁移执行器却一个 .sql 都没有，是硬错误，不是合法的空库。

    静默 no-op 会建出一个只有 ``schema_migrations`` 的空库，让 ``database_healthy()``
    那种「表能查通就算好」的判断报 ok。
    """
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()

    with pytest.raises(MigrationError):
        db.migrate()


def test_text_vectors_migration_is_applied(migrations_ready_db):
    """``0015`` 建出文本向量表；``CHECK`` 与唯一键都写在建表语句里。

    迁移只建表不搬数据——存量复制必须走维护脚本（可 dry-run、可审计）。
    """
    with migrations_ready_db.connect() as connection:
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'text_vectors'"
        ).fetchone()
        indexes = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'text_vectors'"
            )
        }

    assert "0015" in versions
    assert row is not None
    assert "CHECK (space IN ('semantic', 'episodic'))" in row[0]
    assert "UNIQUE(space, source_type, source_id, model_name)" in row[0]
    assert {
        "idx_text_vectors_space_model",
        "idx_text_vectors_source",
        "idx_text_vectors_scope",
    } <= indexes


def test_write_connection_uses_wal_and_busy_timeout(tmp_path):
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_a.sql").write_text("CREATE TABLE a (id TEXT PRIMARY KEY);")
    db.migrate()

    with db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


def test_duplicate_migration_versions_fail_before_creating_database(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_a.sql").write_text("CREATE TABLE a (id TEXT);")
    (migrations / "0001_b.sql").write_text("CREATE TABLE b (id TEXT);")
    database = Database(tmp_path / "t.db", migrations)
    with pytest.raises(MigrationError, match="版本号重复"):
        database.migrate()
    assert not database.path.exists()


@pytest.mark.parametrize("baseline", ["fresh", "xy", "main", "previous_merge", "current_main"])
def test_branch_migrations_preserve_data_and_upgrade_once(tmp_path, baseline):
    source = Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations"
    old_scripts = tmp_path / "old"
    old_scripts.mkdir()
    for script in source.glob("*.sql"):
        if script.name[:4] < "0015":
            (old_scripts / script.name).write_bytes(script.read_bytes())
    if baseline == "xy":
        (old_scripts / "0015_capture_time.sql").write_bytes((source / "0018_capture_time.sql").read_bytes())
    elif baseline == "main":
        (old_scripts / "0015_text_vectors.sql").write_bytes((source / "0015_text_vectors.sql").read_bytes())
    if baseline == "previous_merge":
        for name in ("0015_text_vectors.sql", "0018_capture_time.sql"):
            target = name.replace("0018", "0016")
            (old_scripts / target).write_bytes((source / name).read_bytes())
    elif baseline == "current_main":
        for name in ("0015_text_vectors.sql", "0016_video_jobs.sql", "0017_video_job_config_hash.sql"):
            (old_scripts / name).write_bytes((source / name).read_bytes())
    path = tmp_path / "t.db"
    old = Database(path, old_scripts)
    old.migrate()
    with old.connect() as connection:
        connection.execute("CREATE TABLE regression_sentinel (value TEXT)")
        connection.execute("INSERT INTO regression_sentinel VALUES ('preserved')")
        if baseline == "xy":
            connection.execute(
                "CREATE TABLE capture_audit AS SELECT applied_at FROM schema_migrations WHERE version='0015'"
            )
        connection.commit()
    current = Database(path, source)
    current.migrate()
    assert current.migrate() == []
    with current.connect() as connection:
        assert connection.execute("SELECT value FROM regression_sentinel").fetchone()[0] == "preserved"
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
        assert versions.count("0015") == versions.count("0016") == versions.count("0018") == 1
        assert connection.execute("SELECT COUNT(*) FROM text_vectors").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM video_jobs").fetchone()[0] == 0
        assert "config_hash" in {row[1] for row in connection.execute("PRAGMA table_info(video_jobs)")}
        assert "capture_time_json" in {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
        assert "nearest_evidence_json" in {
            row[1] for row in connection.execute("PRAGMA table_info(asset_places)")
        }
        if baseline == "xy":
            assert (
                connection.execute(
                    "SELECT applied_at FROM schema_migrations WHERE version='0018'"
                ).fetchone()[0]
                == connection.execute("SELECT applied_at FROM capture_audit").fetchone()[0]
            )
        assert connection.execute("SELECT capture_sort_key('2026-11-06T12:00:00+08:00')").fetchone()[0]
        assert (
            connection.execute(
                "SELECT capture_in_bounds('2026-11-06T12:00:00+08:00', '2026-11-06', '2026-11-07')"
            ).fetchone()[0]
            == 1
        )


def test_concurrent_migrations_apply_each_schema_once(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations"
    path = tmp_path / "t.db"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(Database(path, source).migrate) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    assert Database(path, source).migrate() == []


def test_unknown_0015_schema_is_not_silently_relabelled(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations"
    old_scripts = tmp_path / "old"
    old_scripts.mkdir()
    for script in source.glob("*.sql"):
        if script.name[:4] < "0015":
            (old_scripts / script.name).write_bytes(script.read_bytes())
    database = Database(tmp_path / "t.db", old_scripts)
    database.migrate()
    with database.connect() as connection:
        connection.execute("INSERT INTO schema_migrations (version) VALUES ('0015')")
        connection.commit()
    with pytest.raises(MigrationError, match="表结构不一致"):
        Database(database.path, source).migrate()
    assert "0015" in database.applied_versions()
    assert "0016" not in database.applied_versions()


def test_nested_repository_connections_complete(tmp_path):
    import subprocess
    import sys

    # family_graph_people opens a second read connection on the same thread.
    path = str(tmp_path / "nested.db")
    code = f"""from pathlib import Path
from sentrix.infrastructure.persistence.database import Database
db = Database(Path({path!r}), Path({str(tmp_path)!r}))
with db.connect() as outer, db.connect() as inner:
    assert inner.execute('SELECT 1').fetchone()[0] == 1
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)

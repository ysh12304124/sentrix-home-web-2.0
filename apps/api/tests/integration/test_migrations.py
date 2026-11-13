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


def test_graph_provenance_column_already_present_is_recorded_without_duplicate_alter(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_result_sets.sql").write_text(
        "CREATE TABLE agent_result_sets (id TEXT PRIMARY KEY, graph_provenance_json TEXT NOT NULL);"
    )
    source = Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations"
    (migrations / "0030_result_set_graph_provenance.sql").write_bytes(
        (source / "0030_result_set_graph_provenance.sql").read_bytes()
    )
    db = Database(tmp_path / "t.db", migrations)

    assert db.migrate() == ["0001", "0030"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert [row[1] for row in conn.execute("PRAGMA table_info(agent_result_sets)")].count(
            "graph_provenance_json"
        ) == 1


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
    """``0024`` 建出文本向量表；``CHECK`` 与唯一键都写在建表语句里。

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

    assert "0024" in versions
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


@pytest.mark.parametrize("baseline", ["fresh", "our_branch", "main"])
def test_branch_migrations_preserve_data_and_upgrade_once(tmp_path, baseline):
    source = Path(__file__).resolve().parents[2] / "src/sentrix/infrastructure/persistence/migrations"
    old_scripts = tmp_path / "old"
    old_scripts.mkdir()
    for script in source.glob("*.sql"):
        if script.name[:4] < "0015":
            (old_scripts / script.name).write_bytes(script.read_bytes())
    if baseline == "our_branch":
        # 153 既有库：本分支 0015–0026 已经应用，只差 0027 的拍摄时间列。
        for script in source.glob("*.sql"):
            if script.name[:4] < "0027":
                (old_scripts / script.name).write_bytes(script.read_bytes())
    elif baseline == "main":
        # main 的编号：0015 文本向量、0016 视频任务、0017 配置哈希、0018 拍摄时间。
        (old_scripts / "0015_text_vectors.sql").write_bytes((source / "0024_text_vectors.sql").read_bytes())
        (old_scripts / "0016_video_jobs.sql").write_bytes((source / "0025_video_jobs.sql").read_bytes())
        (old_scripts / "0017_video_job_config_hash.sql").write_bytes(
            (source / "0026_video_job_config_hash.sql").read_bytes()
        )
        (old_scripts / "0018_capture_time.sql").write_bytes((source / "0027_capture_time.sql").read_bytes())
    path = tmp_path / "t.db"
    if baseline != "fresh":
        old = Database(path, old_scripts)
        old.migrate()
        with old.connect() as connection:
            connection.execute("CREATE TABLE regression_sentinel (value TEXT)")
            connection.execute("INSERT INTO regression_sentinel VALUES ('preserved')")
            if baseline == "main":
                connection.execute(
                    "CREATE TABLE capture_audit AS SELECT applied_at FROM schema_migrations"
                    " WHERE version='0018'"
                )
            connection.commit()
    current = Database(path, source)
    current.migrate()
    assert current.migrate() == []
    with current.connect() as connection:
        if baseline != "fresh":
            assert connection.execute("SELECT value FROM regression_sentinel").fetchone()[0] == "preserved"
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for table in (
            "text_vectors",
            "video_jobs",
            "import_jobs",
            "scope_postprocess",
            "memory_rotation_sequences",
        ):
            assert (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is not None
            )
        assert "config_hash" in {row[1] for row in connection.execute("PRAGMA table_info(video_jobs)")}
        assert "capture_time_json" in {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
        assert "nearest_evidence_json" in {
            row[1] for row in connection.execute("PRAGMA table_info(asset_places)")
        }
        assert "cancel_requested" in {row[1] for row in connection.execute("PRAGMA table_info(import_jobs)")}
        assert "idempotency_key" in {row[1] for row in connection.execute("PRAGMA table_info(agent_turns)")}
        if baseline == "main":
            # main 的数值版本被别的迁移占用；本分支用带后缀的版本记录补齐。
            assert {
                "0015_import_jobs",
                "0016_import_replay_keys",
                "0017_turn_recovery",
                "0018_memory_rotation",
            } <= versions
            assert (
                connection.execute(
                    "SELECT applied_at FROM schema_migrations WHERE version='0018'"
                ).fetchone()[0]
                == connection.execute("SELECT applied_at FROM capture_audit").fetchone()[0]
            )
        if baseline == "our_branch":
            assert "0027" in versions
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


def test_legacy_0015_version_does_not_block_import_tables(tmp_path):
    """main 的 0015 是文本向量；本分支的 0015 是导入作业。

    同一个版本号被两种历史占用时，旧记录不能删除，也不能当作本分支的表已经存在；
    补齐本分支缺的表后用带后缀的版本记录，下一次启动不再重复执行。
    """
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

    Database(database.path, source).migrate()

    versions = database.applied_versions()
    assert "0015" in versions
    assert "0015_import_jobs" in versions
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_jobs'"
        ).fetchone()


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

import pytest

from sentrix.bootstrap.container import MIGRATIONS_DIR, Container
from sentrix.config import Settings
from sentrix.infrastructure.persistence.database import MigrationError

pytestmark = pytest.mark.usefixtures("fake_embedding_startup")


def test_container_builds_all_adapters(tmp_path, monkeypatch):
    # conftest.py 要到 Task 5 才建，这里就地构造 Settings，不依赖 settings fixture
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()
    container = Container.build(settings)

    assert container.settings is settings
    assert container.database.path == settings.database_path
    assert container.database.migrations_dir.name == "migrations"


def test_database_healthy_reports_false_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path / "gone"))
    settings = Settings()
    container = Container.build(settings)

    assert container.database_healthy() is False


def test_migrate_fails_loudly_when_migrations_dir_is_missing(tmp_path, monkeypatch):
    """定位不到迁移目录时必须炸。

    静默 no-op 会让服务带着空 schema 起来，而 health 还会报 ok——1.0 那种
    「起得来、第一次写入才崩」的坏法。
    """
    monkeypatch.setattr("sentrix.bootstrap.container.MIGRATIONS_DIR", tmp_path / "gone")
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path / "data"))
    container = Container.build(Settings())

    with pytest.raises(MigrationError):
        container.database.migrate()


def test_database_healthy_reports_false_when_migrations_are_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    container = Container.build(Settings())

    # 还没跑 migrate()：表都不存在，健康检查不能报 ok
    assert container.database_healthy() is False

    container.database.migrate()
    assert container.database_healthy() is True


def test_shipped_migrations_directory_is_not_empty():
    """打包漏掉 .sql 时 MIGRATIONS_DIR 会变成空目录——那正是静默 no-op 的分支。

    editable 安装读的是源码树，所以这条拦不住 wheel 漏文件；真实验证见构建 wheel 的步骤。
    """
    assert sorted(MIGRATIONS_DIR.glob("*.sql")), f"{MIGRATIONS_DIR} 下没有任何 .sql 迁移"

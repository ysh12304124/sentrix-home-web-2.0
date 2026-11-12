from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.bootstrap.container import MIGRATIONS_DIR, Container
from sentrix.config import Settings
from sentrix.infrastructure.media.operations import InProcessOperationControl
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


def test_local_upload_jobs_are_consumed_without_experience_mode(isolated_app):
    container = replace(
        isolated_app.state.container,
        model_health=SimpleNamespace(check=lambda: (True, "")),
    )
    assert not container.settings.experience_mode
    isolated_app.state.container = container
    executor = container.build_import_executor()
    assert executor is not None
    payload = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(payload, format="PNG")
    client = TestClient(isolated_app)
    response = client.post(
        "/api/v1/media/uploads",
        files={"file": ("local.png", payload.getvalue(), "image/png")},
        headers={"Idempotency-Key": "local-upload-worker"},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert container.import_jobs.get(job_id).status == "accepted"
    assert executor._run_once()
    job = container.import_jobs.get(job_id)
    assert job.status == "completed", job.error_message
    assert job.attempts == 1
    assert container.asset_repository.get(job.asset_id).status.value == "processed"
    assert container.observation_repository.list_by_asset(job.asset_id)


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


def test_local_queued_turn_executes_and_cancelled_turn_is_not_run(isolated_app):
    container = replace(
        isolated_app.state.container,
        model_health=SimpleNamespace(check=lambda: (True, "")),
    )
    assert not container.settings.experience_mode
    isolated_app.state.container = container
    executor = container.build_turn_executor()
    assert executor is not None
    client = TestClient(isolated_app)

    def enqueue():
        response = client.post("/api/v1/assistant/conversations", json={"scope_id": "home-default"})
        assert response.status_code == 201
        conversation_id = response.json()["id"]
        response = client.post(
            f"/api/v1/assistant/conversations/{conversation_id}/turns",
            json={"message": "你好"},
        )
        assert response.status_code == 202
        return response.json()["turn_id"]

    cancelled = enqueue()
    assert client.post(f"/api/v1/assistant/turns/{cancelled}/cancel").status_code == 202
    current = enqueue()
    # 模拟 API 重启：SQLite 回合仍在，新进程没有原先的内存取消登记。
    container = replace(container, operation_control=InProcessOperationControl())
    executor = container.build_turn_executor()
    assert container.turn_repository.get(current).attempt_no == 0
    assert executor._run_once()
    assert container.turn_repository.get(cancelled).status.value == "cancelled"
    assert container.turn_repository.get(current).attempt_no == 0
    assert executor._run_once()
    record = container.turn_repository.get(current)
    assert record.attempt_no == 1
    assert record.status.is_terminal
    assert record.status.value in {"complete", "partial"}
    assert record.answer

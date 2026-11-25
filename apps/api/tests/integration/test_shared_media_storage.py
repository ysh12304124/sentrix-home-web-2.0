"""验证跨相册的真实文件共享、引用生命周期与上传配额。"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.application.media.delete_asset import DeleteAsset
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.domain.media.import_jobs import ImportJob, UploadQuotaExceeded
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.media.upload_cleanup import reconcile_uploads
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.import_jobs import SqliteImportJobRepository
from sentrix.infrastructure.persistence.repositories.media_deletion import SqliteMediaDeletionRepository
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository


def _blobs(root: Path) -> list[Path]:
    return list((root / ".blobs").glob("[0-9a-f]" * 64))


def _job(asset_id: str, path: Path, *, scope: str = "album1") -> ImportJob:
    now = datetime.now(UTC)
    return ImportJob(
        id="job_" + asset_id,
        asset_id=asset_id,
        scope_id=scope,
        receive_seq=0,
        file_name=path.name,
        media_type="image",
        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
        status="accepted",
        stage="accepted",
        attempts=0,
        error_category="",
        error_message="",
        idempotency_key="key_" + asset_id,
        accepted_at=now,
        updated_at=now,
        original_path=str(path),
    )


def test_identical_bytes_keep_asset_names_and_share_one_physical_file(tmp_path):
    storage = FileSystemMediaStorage(tmp_path)
    first = storage.store("asset_1", "first.jpg", b"same photo")
    second = storage.store("asset_2", "renamed.png", b"same photo")

    assert first.name == "asset_1_first.jpg"
    assert second.name == "asset_2_renamed.png"
    assert first.samefile(second)
    assert len(_blobs(tmp_path)) == 1
    assert first.stat().st_nlink == 3
    assert storage.read("asset_1") == storage.read("asset_2") == b"same photo"


def test_external_source_remains_independent_and_can_change(tmp_path):
    source = tmp_path / "original.jpg"
    source.write_bytes(b"original")
    root = tmp_path / "media"
    storage = FileSystemMediaStorage(root)
    first = storage.copy_from_path("asset_1", source.name, source)
    second = storage.copy_from_path("asset_2", "renamed.jpg", source)

    assert first.samefile(second)
    assert not first.samefile(source)
    source.write_bytes(b"edited externally")
    assert first.read_bytes() == second.read_bytes() == b"original"


def test_uploaded_files_are_consumed_and_share_content(tmp_path):
    storage = FileSystemMediaStorage(tmp_path / "media")
    paths = []
    for index in range(2):
        source = storage.staging_dir / f"upload-{index}.part"
        source.write_bytes(b"uploaded")
        paths.append(storage.move_from_path(f"asset_{index}", "a.jpg", source))
        assert not source.exists()
    assert paths[0].samefile(paths[1])
    assert not list(storage.staging_dir.iterdir())


def test_deletion_releases_only_its_link_and_reclaims_last_content(tmp_path):
    storage = FileSystemMediaStorage(tmp_path)
    first = storage.store("asset_1", "a.jpg", b"same")
    second = storage.store("asset_2", "b.jpg", b"same")
    storage.delete_assets(("asset_1",), (str(first),))
    assert not first.exists()
    assert second.read_bytes() == b"same"
    assert second.stat().st_nlink == 2
    storage.delete_assets(("asset_2",), (str(second),))
    storage.delete_assets(("asset_2",), (str(second),))
    assert not _blobs(tmp_path)


def test_legacy_managed_file_is_adopted_without_copying(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    legacy = root / "asset_old_a.jpg"
    legacy.write_bytes(b"legacy")
    identity = legacy.stat().st_ino
    storage = FileSystemMediaStorage(root)
    imported = storage.copy_from_path("asset_new", "b.jpg", legacy)
    assert imported.samefile(legacy)
    assert imported.stat().st_ino == identity
    assert len(_blobs(root)) == 1


def test_adopting_a_legacy_duplicate_reclaims_its_old_copy(tmp_path):
    storage = FileSystemMediaStorage(tmp_path)
    first = storage.store("asset_new", "a.jpg", b"same")
    legacy = tmp_path / "asset_old_a.jpg"
    legacy.write_bytes(b"same")
    assert not legacy.samefile(first)
    adopted = storage.copy_from_path("asset_old", "a.jpg", legacy)
    assert adopted == legacy
    assert legacy.samefile(first)
    assert len(_blobs(tmp_path)) == 1


def test_concurrent_instances_publish_one_complete_content(tmp_path):
    payload = bytes(range(256)) * 8192

    def upload(index):
        return FileSystemMediaStorage(tmp_path).store(f"asset_{index}", "same.jpg", payload)

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(upload, range(24)))
    assert len({(p.stat().st_dev, p.stat().st_ino) for p in paths}) == 1
    assert len(_blobs(tmp_path)) == 1
    assert paths[0].stat().st_nlink == 25
    assert all(path.read_bytes() == payload for path in paths)
    assert not list((tmp_path / ".staging").iterdir())


def _publish_or_delete(root: str, index: int) -> str:
    storage = FileSystemMediaStorage(Path(root))
    if index == 0:
        storage.delete_assets(("asset_old",), (str(Path(root) / "asset_old_a.jpg"),))
        return ""
    return str(storage.store(f"asset_{index}", "same.jpg", b"concurrent content" * 4096))


def test_processes_coordinate_last_reference_deletion_and_content_publication(tmp_path):
    storage = FileSystemMediaStorage(tmp_path)
    old = storage.store("asset_old", "a.jpg", b"concurrent content" * 4096)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(_publish_or_delete, str(tmp_path), index) for index in range(12)]
        paths = [Path(result) for future in futures if (result := future.result(timeout=30))]
    assert not old.exists()
    assert len(paths) == 11
    assert len({(path.stat().st_dev, path.stat().st_ino) for path in paths}) == 1
    assert all(path.read_bytes() == b"concurrent content" * 4096 for path in paths)
    assert len(_blobs(tmp_path)) == 1
    storage.delete_assets((), tuple(str(path) for path in paths))
    assert not _blobs(tmp_path)


def test_asset_id_cannot_overwrite_another_content(tmp_path):
    storage = FileSystemMediaStorage(tmp_path)
    first = storage.store("asset_1", "a.jpg", b"first")
    second = storage.store("asset_2", "a.jpg", b"first")
    with pytest.raises(FileExistsError):
        storage.store("asset_1", "a.jpg", b"changed")
    assert first.read_bytes() == second.read_bytes() == b"first"


def test_failed_link_never_exposes_partial_asset_or_changes_external_source(tmp_path, monkeypatch):
    source = tmp_path / "external.jpg"
    source.write_bytes(b"original")
    root = tmp_path / "media"
    storage = FileSystemMediaStorage(root)

    def fail(*args, **kwargs):
        raise OSError("link unavailable")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError, match="link unavailable"):
        storage.copy_from_path("asset_1", source.name, source)
    assert source.read_bytes() == b"original"
    assert not list(root.glob("asset_*"))
    storage.cleanup_orphans(set(), time.time() + 10)
    assert not _blobs(root)


def test_cleanup_preserves_referenced_link_and_cleans_crash_leftovers(tmp_path, migrations_ready_db):
    root = tmp_path / "media"
    storage = FileSystemMediaStorage(root)
    keep = storage.store("asset_keep", "a.jpg", b"same")
    orphan = storage.store("asset_orphan", "b.jpg", b"same")
    dropped = storage.store("asset_crash", "a.jpg", b"different")
    dropped.unlink()
    jobs = SqliteImportJobRepository(migrations_ready_db)
    jobs.accept(_job("asset_keep", keep), reservation_id=None, quota_bytes=100)
    result = reconcile_uploads(
        storage=storage,
        staging_dir=storage.staging_dir,
        jobs=jobs,
        grace_s=-10,
    )
    assert result["orphans_removed"] == 1
    assert not orphan.exists()
    assert keep.read_bytes() == b"same"
    assert len(_blobs(root)) == 1


def test_recent_hardlink_is_not_expired_when_content_mtime_is_old(tmp_path):
    storage = FileSystemMediaStorage(tmp_path)
    first = storage.store("asset_1", "a.jpg", b"same")
    os.utime(first, (1, 1))
    cutoff = time.time() - 1
    second = storage.store("asset_2", "b.jpg", b"same")
    assert storage.cleanup_orphans({str(first)}, cutoff) == 0
    assert second.read_bytes() == b"same"


def test_upload_quota_counts_hardlinks_once_but_legacy_copies_separately(tmp_path, migrations_ready_db):
    storage = FileSystemMediaStorage(tmp_path / "media")
    first = storage.store("asset_1", "a.jpg", b"same")
    second = storage.store("asset_2", "a.jpg", b"same")
    jobs = SqliteImportJobRepository(migrations_ready_db)
    jobs.accept(_job("asset_1", first), reservation_id=None, quota_bytes=4)
    jobs.accept(_job("asset_2", second, scope="album2"), reservation_id=None, quota_bytes=4)
    assert jobs.original_bytes() == 4
    legacy = storage.media_dir / "asset_legacy_a.jpg"
    legacy.write_bytes(b"same")
    with pytest.raises(UploadQuotaExceeded):
        jobs.accept(_job("asset_legacy", legacy), reservation_id=None, quota_bytes=4)
    jobs.accept(_job("asset_legacy", legacy), reservation_id=None, quota_bytes=8)
    assert jobs.original_bytes() == 8


def test_real_imports_and_deletion_keep_album_records_independent(tmp_path, migrations_ready_db):
    db = migrations_ready_db
    storage = FileSystemMediaStorage(tmp_path / "media")
    assets = SqliteAssetRepository(db)
    observations = SqliteObservationRepository(db)
    importer = ImportAssets(
        assets=assets,
        observations=observations,
        storage=storage,
        metadata=SimpleNamespace(read=lambda path: (None, None, None)),
        spaces=SqliteSpaceRepository(db),
    )
    request = ImportRequest(scope_id="album1", file_name="a.jpg", content_type="image/jpeg", content=b"same")
    first = importer.execute(request)
    second = importer.execute(
        ImportRequest(scope_id="album2", file_name="b.jpg", content_type="image/jpeg", content=b"same")
    )
    assert not first.deduplicated and not second.deduplicated
    assert first.asset_id != second.asset_id
    first_path = Path(assets.get(first.asset_id).path)
    second_path = Path(assets.get(second.asset_id).path)
    assert first_path.samefile(second_path)
    assert observations.list_by_asset(first.asset_id)[0].scope_id == "album1"
    assert observations.list_by_asset(second.asset_id)[0].scope_id == "album2"
    assert importer.execute(request).deduplicated
    vectors = SimpleNamespace(delete_sources=lambda sources: None)
    deletion = DeleteAsset(SqliteMediaDeletionRepository(db), storage, vectors)
    assert deletion.execute(first.asset_id).deleted
    assert assets.get(first.asset_id) is None
    assert not first_path.exists()
    assert storage.read_asset(assets.get(second.asset_id)) == b"same"
    assert observations.list_by_asset(second.asset_id)
    assert deletion.execute(second.asset_id).deleted
    assert not _blobs(storage.media_dir)


def test_real_import_reuses_a_pre_upgrade_original(tmp_path, migrations_ready_db):
    db = migrations_ready_db
    storage = FileSystemMediaStorage(tmp_path / "media")
    assets = SqliteAssetRepository(db)
    importer = ImportAssets(
        assets=assets,
        observations=SqliteObservationRepository(db),
        storage=storage,
        metadata=SimpleNamespace(read=lambda path: (None, None, None)),
        spaces=SqliteSpaceRepository(db),
    )
    first = importer.execute(
        ImportRequest(scope_id="album1", file_name="a.jpg", content_type="image/jpeg", content=b"old")
    )
    path = Path(assets.get(first.asset_id).path)
    # 还原升级前的普通文件状态，数据库记录及路径保持。
    raw = path.read_bytes()
    storage.delete_assets((), (str(path),))
    path.write_bytes(raw)
    inode = path.stat().st_ino
    second = importer.execute(
        ImportRequest(scope_id="album2", file_name="b.jpg", content_type="image/jpeg", content=raw)
    )
    second_path = Path(assets.get(second.asset_id).path)
    assert second_path.samefile(path)
    assert second_path.stat().st_ino == inode


def test_http_upload_retry_processing_and_deletion_preserve_shared_original(isolated_app):
    container = isolated_app.state.container
    container = replace(
        container, settings=container.settings.model_copy(update={"upload_min_free_bytes": 0})
    )
    isolated_app.state.container = container
    client = TestClient(isolated_app)
    content = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(content, format="PNG")
    payload = content.getvalue()

    def upload(scope, key, body=payload):
        return client.post(
            "/api/v1/media/uploads",
            files={"file": ("a.png", body, "image/png")},
            data={"scope_id": scope},
            headers={"Idempotency-Key": key},
        )

    first, second = upload("album1", "first"), upload("album2", "second")
    assert first.status_code == second.status_code == 202
    assert upload("album1", "first").json() == first.json()
    assert upload("album1", "first", payload + b"changed").status_code == 409
    jobs = [container.import_jobs.get(response.json()["job_id"]) for response in (first, second)]
    paths = [Path(job.original_path) for job in jobs]
    assert paths[0].samefile(paths[1])
    assert container.import_jobs.original_bytes() == len(payload)
    assert len(list(container.settings.media_dir.glob("asset_*"))) == 2
    assert len(_blobs(container.settings.media_dir)) == 1
    executor = container.build_import_executor()
    assert executor._run_once()
    assert all(container.import_jobs.get(job.id).status == "completed" for job in jobs)
    assert jobs[0].asset_id != jobs[1].asset_id
    assert client.delete(f"/api/v1/assets/{jobs[0].asset_id}").json()["deleted"]
    assert client.get(f"/api/v1/assets/{jobs[1].asset_id}/file").content == payload
    assert client.delete(f"/api/v1/assets/{jobs[1].asset_id}").json()["deleted"]
    assert not _blobs(container.settings.media_dir)


def test_historical_consolidation_cli_preserves_database_and_is_idempotent(tmp_path, migrations_ready_db):
    db = migrations_ready_db
    root = tmp_path / "media"
    root.mkdir()
    payload = bytes(range(256)) * 4096
    paths = []
    with db.connect() as connection, connection:
        for index in range(3):
            path = root / f"asset_{index}_a.jpg"
            path.write_bytes(payload)
            paths.append(path)
            connection.execute(
                "INSERT INTO assets(id,scope_id,file_name,media_type,path,content_sha256,"
                "created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"asset_{index}",
                    f"album{index}",
                    "a.jpg",
                    "image",
                    str(path),
                    hashlib.sha256(payload).hexdigest(),
                    "now",
                    "now",
                ),
            )
        before = [tuple(row) for row in connection.execute("SELECT * FROM assets ORDER BY id")]
    script = Path(__file__).resolve().parents[4] / "scripts/maintenance/consolidate_media_files.py"
    command = [sys.executable, str(script), "--database", str(db.path), "--media-dir", str(root)]

    def invoke(*extra):
        result = subprocess.run([*command, *extra], check=True, capture_output=True, text=True, timeout=20)
        return json.loads(result.stdout)

    report = invoke()
    assert report["reclaimable_file_bytes"] == 2 * len(payload)
    assert not (root / ".blobs").exists()
    assert len({path.stat().st_ino for path in paths}) == 3
    applied = invoke("--apply")
    assert applied["reclaimed_file_bytes"] == 2 * len(payload)
    assert len({path.stat().st_ino for path in paths}) == 1
    assert all(path.read_bytes() == payload for path in paths)
    assert invoke()["reclaimable_file_bytes"] == 0
    assert invoke("--apply")["reclaimed_file_bytes"] == 0
    with db.connect() as connection:
        after = [tuple(row) for row in connection.execute("SELECT * FROM assets ORDER BY id")]
    assert after == before

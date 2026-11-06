from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, MediaType, VideoMetadata
from sentrix.domain.media.ports import VideoProbe
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.video import SqliteVideoRepository


@pytest.fixture
def video_jobs(migrations_ready_db):
    now = datetime(2026, 11, 6, tzinfo=UTC)
    SqliteAssetRepository(migrations_ready_db).add(
        Asset(
            id="clip",
            scope_id="test",
            file_name="clip.mp4",
            media_type=MediaType.VIDEO,
            path="/test/clip.mp4",
            mime_type="video/mp4",
            size_bytes=12,
            status=AssetStatus.QUEUED,
            content_sha256="hash",
            captured_at=now,
            captured_location=None,
            created_at=now,
            updated_at=now,
        )
    )
    return SqliteVideoRepository(migrations_ready_db)


def test_job_creation_is_idempotent_and_survives_reopening(video_jobs, migrations_ready_db):
    first = video_jobs.create_job("clip", "hash", "first", "config")
    reopened = SqliteVideoRepository(migrations_ready_db)
    second = reopened.create_job("clip", "hash", "duplicate", "config")
    assert first == second
    assert reopened.get_job("duplicate") is None
    assert len(reopened.list_stages("first")) == 8
    assert second["config_hash"] == "config"


def test_concurrent_workers_claim_only_once(video_jobs):
    video_jobs.create_job("clip", "hash", "job")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda worker: video_jobs.claim_job("job", worker), ["one", "two"]))
    assert sorted(outcomes) == [False, True]
    assert video_jobs.get_job("job")["retry_count"] == 1


def test_recovery_preserves_completed_stages_and_artifact(video_jobs, migrations_ready_db):
    video_jobs.create_job("clip", "hash", "job")
    assert video_jobs.claim_job("job", "old")
    video_jobs.update_stage(
        "job",
        "FRAME_SAMPLE_VSD",
        status="SUCCEEDED",
        progress=1.0,
        input_hash="hash",
        output_artifact="/frames/checkpoint.json",
    )
    video_jobs.update_job("job", heartbeat_at="2000-01-01T00:00:00+00:00")
    reopened = SqliteVideoRepository(migrations_ready_db)
    assert reopened.recover_stale_jobs("2026-11-06T00:00:00+00:00") == 1
    checkpoint = reopened.stage("job", "FRAME_SAMPLE_VSD")
    assert checkpoint["status"] == "SUCCEEDED"
    assert checkpoint["output_artifact"] == "/frames/checkpoint.json"
    assert reopened.claim_job("job", "new")
    assert reopened.get_job("job")["retry_count"] == 2
    assert reopened.recover_stale_jobs("2000-01-01T00:00:00+00:00") == 0


def test_completed_job_is_not_reclaimed(video_jobs):
    video_jobs.create_job("clip", "hash", "job")
    video_jobs.update_job("job", status="COMPLETED", heartbeat_at="2000-01-01T00:00:00+00:00")
    assert video_jobs.recover_stale_jobs("2026-11-06T00:00:00+00:00") == 0
    assert not video_jobs.claim_job("job", "new")


def test_processing_timings_preserve_video_metadata(video_jobs):
    video_jobs.save_probe("clip", VideoProbe(metadata=VideoMetadata(duration_sec=12.0)))
    video_jobs.save_processing_timings("clip", {"scene_planning": 1.25, "keyframe_memory": 0.0})
    probe = video_jobs.probe("clip")
    assert probe.metadata.duration_sec == 12.0
    assert probe.processing_timings == {"scene_planning": 1.25, "keyframe_memory": 0.0}

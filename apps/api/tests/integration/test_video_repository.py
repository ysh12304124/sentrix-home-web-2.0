"""视频血缘仓储：探测结果的读写、派生物查询、清场。真 SQLite + 真迁移 0010。

**为什么用真库**：这一层的全部风险都在列名与 SQL 上（迁移 0010 新加了
``parent_asset_id`` / ``derived_kind`` / ``source_*`` 那几列），拿假仓储测等于什么都不测。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import (
    Asset,
    AssetStatus,
    MediaType,
    VideoMetadata,
)
from sentrix.domain.media.ports import VideoProbe
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.video import SqliteVideoRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _asset(asset_id: str, media_type: MediaType = MediaType.VIDEO, *, file_name: str = "clip.mov") -> Asset:
    return Asset(
        id=asset_id,
        scope_id="album1",
        file_name=file_name,
        media_type=media_type,
        path=f"/media/{asset_id}",
        mime_type="video/quicktime" if media_type is MediaType.VIDEO else "image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256=asset_id,
        captured_at=NOW,
        captured_location=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _insert_derived(
    db: Database, derived_id: str, *, parent: str, scene: int, timestamp: float, frame: int
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, captured_at, created_at, updated_at, parent_asset_id, derived_kind,"
            " source_timestamp_sec, source_frame_index, source_scene_index)"
            " VALUES (?, 'album1', ?, 'image', ?, 1, 'processed', ?, ?, ?, ?, ?, 'video_keyframe',"
            " ?, ?, ?)",
            (
                derived_id,
                f"{derived_id}.webp",
                f"/media/{derived_id}",
                derived_id,
                NOW.isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
                parent,
                timestamp,
                frame,
                scene,
            ),
        )


def _insert_scene_event(db: Database, event_id: str, *, source_asset_id: str, scene: int) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, source_type, source_asset_id,"
            " source_scene_index, source_start_sec, source_end_sec, created_at, updated_at)"
            " VALUES (?, 'album1', '视频场景', '视频场景', 'video_scene', ?, ?, 0.0, 5.0, ?, ?)",
            (event_id, source_asset_id, scene, NOW.isoformat(), NOW.isoformat()),
        )


PROBED = VideoMetadata(
    captured_at="2026-08-02T14:55:56+08:00",
    latitude=22.4846,
    longitude=114.5438,
    captured_location="22.484600,114.543800",
    duration_sec=11.735,
    fps=30.0,
    width=1920,
    height=1080,
    codec="hevc",
    rotation=-90,
    device="Apple iPhone 14 Plus",
    creation_source="com.apple.quicktime.creationdate",
    frame_count=300,
)


def test_probe_round_trips_metadata_and_preview(migrations_ready_db: Database):
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(_asset("asset_video"))
    repository = SqliteVideoRepository(migrations_ready_db)

    repository.save_probe(
        "asset_video",
        VideoProbe(
            metadata=PROBED,
            browser_preview_path="/media/derived/video/asset_video/preview.mp4",
            browser_preview_error="",
            probe_error="",
        ),
    )

    probe = repository.probe("asset_video")
    assert probe is not None
    assert probe.metadata == PROBED
    assert probe.browser_preview_path.endswith("preview.mp4")
    assert probe.probe_error == ""


def test_an_unprobed_asset_reads_back_as_none(migrations_ready_db: Database):
    """没探测过与「探测了但什么都没读到」必须是两件事——前者返回 None。"""
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_img", MediaType.IMAGE, file_name="a.jpg"))

    assert SqliteVideoRepository(migrations_ready_db).probe("asset_img") is None
    assert SqliteVideoRepository(migrations_ready_db).probe("asset_missing") is None


def test_save_probe_merges_other_metadata_keys(migrations_ready_db: Database):
    """``metadata_json`` 上可能压着别的东西（1.0 同一列放过反地理/事实 id），
    ``save_probe`` 是合并写，不是整包覆盖。"""
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(_asset("asset_video"))
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET metadata_json = ? WHERE id = ?",
            ('{"reverse_geocode": {"city": "邯郸市"}}', "asset_video"),
        )

    repository = SqliteVideoRepository(migrations_ready_db)
    repository.save_probe("asset_video", VideoProbe(metadata=PROBED))

    with migrations_ready_db.connect() as connection:
        payload = json.loads(
            connection.execute("SELECT metadata_json FROM assets WHERE id = ?", ("asset_video",)).fetchone()[
                "metadata_json"
            ]
        )
    assert payload["reverse_geocode"] == {"city": "邯郸市"}
    assert payload["video_metadata"]["codec"] == "hevc"


def test_save_processing_seconds_merges_without_clobbering_other_keys(migrations_ready_db: Database):
    """``video_processing_seconds`` 单写一个键，不整包覆盖 ``metadata_json``。

    1.0 把整个视频阶段的秒数写进这个键（``processor.py:205``），PhotoBench 的关键帧
    分析读它。这里同时钉住合并不覆盖：``video_metadata`` 那些键必须原样留着。
    """
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(_asset("asset_video"))
    repository = SqliteVideoRepository(migrations_ready_db)
    repository.save_probe("asset_video", VideoProbe(metadata=PROBED))

    repository.save_processing_seconds("asset_video", 12.5)

    probe = repository.probe("asset_video")
    assert probe is not None
    assert probe.processing_seconds == pytest.approx(12.5)
    assert probe.metadata is not None
    assert probe.metadata.codec == "hevc"
    # **探测过但没记秒数**的资产 → None，而不是 0（「没记过」与「记了 0 秒」要能分开）。
    assets.add(_asset("asset_video2"))
    repository.save_probe("asset_video2", VideoProbe(metadata=PROBED))
    probed_only = repository.probe("asset_video2")
    assert probed_only is not None
    assert probed_only.processing_seconds is None


def test_asset_repository_reads_back_the_derived_lineage(migrations_ready_db: Database):
    """``SqliteAssetRepository.get`` 必须带回迁移 0010 的三列。

    PhotoBench 的关键帧分析就是靠这三列（``benchmark_orchestrator.py:5712/5798``）在
    ``/api/assets`` 的行里认出派生帧的——窄投影漏掉它们，那条链在 2.0 就断了。
    同时钉住普通资产这三位是 ``None``，否则「有血缘」与「没血缘」分不开。
    """
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(_asset("asset_video"))
    SqliteVideoRepository(migrations_ready_db).add_derived(
        parent_asset_id="asset_video",
        asset=_asset("asset_frame", MediaType.IMAGE, file_name="f.webp"),
        derived_kind="video_keyframe",
        source_timestamp_sec=7.5,
        source_frame_index=7,
        source_scene_index=2,
    )

    loaded = assets.get("asset_frame")
    assert loaded is not None
    assert loaded.derived_kind == "video_keyframe"
    assert loaded.parent_asset_id == "asset_video"
    assert loaded.source_timestamp_sec == pytest.approx(7.5)
    plain = assets.get("asset_video")
    assert plain is not None
    assert (plain.derived_kind, plain.parent_asset_id, plain.source_timestamp_sec) == (None, None, None)


def test_a_failed_probe_keeps_the_reason_and_no_metadata(migrations_ready_db: Database):
    """探测失败：``probe_error`` 有值、``video_metadata`` **不写**——否则「探测过但没读出来」
    与「读出来是空的」在库里长得一样。"""
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_video"))
    repository = SqliteVideoRepository(migrations_ready_db)

    repository.save_probe("asset_video", VideoProbe(probe_error="ValueError: ffprobe failed"))

    probe = repository.probe("asset_video")
    assert probe is not None
    assert probe.metadata is None
    assert probe.probe_error == "ValueError: ffprobe failed"


def test_save_probe_rejects_an_unknown_asset(migrations_ready_db: Database):
    with pytest.raises(KeyError):
        SqliteVideoRepository(migrations_ready_db).save_probe("asset_missing", VideoProbe())


def test_list_derived_orders_by_scene_time_and_frame(migrations_ready_db: Database):
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_video"))
    # 故意反序插入：断言靠 ORDER BY 而不是插入顺序。
    _insert_derived(migrations_ready_db, "asset_f3", parent="asset_video", scene=0, timestamp=3.0, frame=3)
    _insert_derived(migrations_ready_db, "asset_f1", parent="asset_video", scene=0, timestamp=1.0, frame=1)
    _insert_derived(migrations_ready_db, "asset_f2", parent="asset_video", scene=1, timestamp=7.0, frame=2)
    # 干扰行：普通图片（derived_kind 为空），不该出现在派生物列表里。
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_plain", MediaType.IMAGE, file_name="b.jpg"))

    repository = SqliteVideoRepository(migrations_ready_db)

    assert [item.id for item in repository.list_derived("asset_video")] == [
        "asset_f1",
        "asset_f3",
        "asset_f2",
    ]
    scene_one = repository.list_derived("asset_video", scene_index=1)
    assert [item.id for item in scene_one] == ["asset_f2"]
    assert scene_one[0].derived_kind == "video_keyframe"
    assert scene_one[0].parent_asset_id == "asset_video"
    assert scene_one[0].source_timestamp_sec == pytest.approx(7.0)


def test_cleanup_derivatives_removes_scenes_and_their_rows(migrations_ready_db: Database):
    """清场：场景事件、挂在上面的观察关联、派生物资产本身，都要走。

    **外键顺序是硬约束**（``PRAGMA foreign_keys = ON``）：先删随行数据再删主行，
    反过来这里就会 IntegrityError。
    """
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_video"))
    _insert_derived(migrations_ready_db, "asset_f1", parent="asset_video", scene=0, timestamp=1.0, frame=1)
    _insert_scene_event(migrations_ready_db, "evt_scene", source_asset_id="asset_video", scene=0)
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES ('obs_frame', 'album1', 'asset_f1', 'import', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO event_observations (event_id, observation_id, created_at) VALUES (?,?,?)",
            ("evt_scene", "obs_frame", NOW.isoformat()),
        )

    removed = SqliteVideoRepository(migrations_ready_db).cleanup_derivatives("asset_video")

    assert removed == 1
    with migrations_ready_db.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM assets WHERE parent_asset_id = 'asset_video'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE source_asset_id = 'asset_video'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM event_observations WHERE event_id = 'evt_scene'"
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM observations WHERE asset_id = 'asset_f1'"
            ).fetchone()["n"]
            == 0
        )
    # 源片本身留着——清的是派生数据，不是这条视频。
    assert SqliteAssetRepository(migrations_ready_db).get("asset_video") is not None


def test_list_video_scenes_orders_and_filters_by_source(migrations_ready_db: Database):
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_a"))
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_b"))
    _insert_scene_event(migrations_ready_db, "evt_a1", source_asset_id="asset_a", scene=1)
    _insert_scene_event(migrations_ready_db, "evt_a0", source_asset_id="asset_a", scene=0)
    _insert_scene_event(migrations_ready_db, "evt_b0", source_asset_id="asset_b", scene=0)

    scenes = SqliteEventRepository(migrations_ready_db).list_video_scenes("asset_a")

    assert [item.id for item in scenes] == ["evt_a0", "evt_a1"]
    assert scenes[0].source_type == "video_scene"
    assert scenes[0].source_asset_id == "asset_a"
    assert scenes[0].source_scene_index == 0
    assert scenes[0].source_end_sec == pytest.approx(5.0)


def test_metadata_refresh_updates_source_frames_observations_and_scene_times(migrations_ready_db):
    from sentrix.domain.media.capture_time import CaptureTime, offset_capture
    from sentrix.domain.media.models import GpsPoint

    db = migrations_ready_db
    assets = SqliteAssetRepository(db)
    assets.add(_asset("source"))
    _insert_derived(db, "frame", parent="source", scene=0, timestamp=30, frame=0)
    _insert_scene_event(db, "scene", source_asset_id="source", scene=0)
    with db.connect() as conn, conn:
        for oid, aid in [("source-obs", "source"), ("frame-obs", "frame")]:
            conn.execute(
                "INSERT INTO observations(id,scope_id,asset_id,source_type,"
                "captured_at,created_at,updated_at) "
                "VALUES(?,'album1',?,'import',?,?,?)",
                (oid, aid, NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
            )
    captured = datetime(2019, 1, 10, 5, 4, 21, tzinfo=UTC)
    repo = SqliteVideoRepository(db)
    repo.update_capture_metadata(
        "source",
        captured,
        GpsPoint(30, 120),
        CaptureTime(captured.isoformat(), "format.creation_time", status="offset_only"),
    )
    assert assets.get("source").captured_at == captured
    assert assets.get("frame").captured_at == offset_capture(captured, 30)
    scene = SqliteEventRepository(db).get("scene")
    assert scene.time_start == captured and scene.time_end == offset_capture(captured, 5)
    with db.connect() as conn:
        assert (
            conn.execute("SELECT captured_at FROM observations WHERE id='frame-obs'").fetchone()[0]
            == offset_capture(captured, 30).isoformat()
        )
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    corrected = datetime(2020, 2, 3, tzinfo=UTC)
    with db.connect() as conn, conn:
        conn.execute(
            "UPDATE assets SET captured_at=?,capture_time_json=? WHERE id='source'",
            (
                corrected.isoformat(),
                json.dumps({"raw": corrected.isoformat(), "source": "user_correction", "status": "known"}),
            ),
        )
    repo.update_capture_metadata("source", captured, None, CaptureTime())
    assert assets.get("source").captured_at == corrected


def test_only_empty_automatic_video_source_events_are_archived_after_scene_recovery(migrations_ready_db):
    db = migrations_ready_db
    assets = SqliteAssetRepository(db)
    assets.add(_asset("source"))
    _insert_derived(db, "frame", parent="source", scene=0, timestamp=2, frame=0)
    _insert_scene_event(db, "scene", source_asset_id="source", scene=0)
    with db.connect() as conn, conn:
        for oid, aid, caption in [("empty", "source", ""), ("visible", "frame", "客厅里有人看书")]:
            conn.execute(
                "INSERT INTO observations(id,scope_id,asset_id,source_type,caption,created_at,updated_at) "
                "VALUES(?,'album1',?,'import',?,?,?)",
                (oid, aid, caption, NOW.isoformat(), NOW.isoformat()),
            )
        for eid in ["placeholder", "manual", "shared"]:
            conn.execute(
                "INSERT INTO events(id,scope_id,title,created_at,updated_at) "
                "VALUES(?,'album1','时间戳记录',?,?)",
                (eid, NOW.isoformat(), NOW.isoformat()),
            )
            conn.execute(
                "INSERT INTO event_observations(event_id,observation_id,created_at) VALUES(?,?,?)",
                (eid, "empty", NOW.isoformat()),
            )
        for eid in ["scene", "shared"]:
            conn.execute(
                "INSERT INTO event_observations(event_id,observation_id,created_at) VALUES(?,'visible',?)",
                (eid, NOW.isoformat()),
            )
        conn.execute(
            "INSERT INTO memory_corrections(id,scope_id,target_kind,target_id,created_at,updated_at,status) "
            "VALUES('user-edit','album1','event','manual',?,?,'synced')",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO entities(id,scope_id,entity_type,canonical_name,status,created_at,updated_at) "
            "VALUES('unknown-place','album1','place','其他或不确定','pending',?,?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO event_entities(event_id,entity_id,relation,created_at,updated_at) "
            "VALUES('placeholder','unknown-place','地点',?,?)", (NOW.isoformat(), NOW.isoformat()),
        )
    events = SqliteEventRepository(db)
    assert events.retire_video_placeholders("source") == ("placeholder",)
    assert events.get("placeholder").status == "superseded"
    assert events.get("manual").status == events.get("shared").status == "active"
    assert events.retire_video_placeholders("source") == ()
    assert assets.get("source") is not None
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM observations WHERE id='empty'").fetchone()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()

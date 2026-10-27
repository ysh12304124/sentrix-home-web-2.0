"""视频抽帧与场景成型（S8b T511–T514）：真 SQLite + 真迁移 0010 + 真仓储。

**为什么用真库**：这一层的风险全在血缘列与唯一索引上（``parent_asset_id`` /
``source_timestamp_sec`` / ``source_frame_index`` / ``source_scene_index`` 与两条
唯一约束），拿假仓储测等于什么都不测。

只有两处是假件：抽帧（起 ffmpeg）与元数据探测（起 ffprobe）——它们与「场景成型、
能跳回原片」这条链的正确性无关，却会把「这台机器有没有 ffmpeg」变成前置条件。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentrix.application.media.extract_video_scenes import ExtractVideoScenes
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.domain.media.models import MediaType, VideoMetadata
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.video import SqliteVideoRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        path = self._root / f"{asset_id}-{file_name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


class FakeMetadata:
    def read(self, path: Path):  # noqa: ANN201 - 与 MetadataReader 的元组返回一致
        return (None, None, None)


class FakeVideoMetadataReader:
    """给一个确定的时长——切窗全看它。"""

    def __init__(self, duration_sec: float) -> None:
        self._duration = duration_sec

    def read(self, path: Path) -> VideoMetadata:
        return VideoMetadata(duration_sec=self._duration, codec="h264", width=640, height=480)


class RecordingKeyframeExtractor:
    """每场景写一个真文件（Asset 要算 size 与 sha256），并记下被要求抽的秒数。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.calls: list[tuple[str, int, float]] = []

    def extract(self, source: Path, asset_id: str, scene_index: int, timestamp_sec: float) -> Path:
        self.calls.append((asset_id, scene_index, timestamp_sec))
        target = self._root / f"{asset_id}-{scene_index:03d}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"jpeg-bytes")
        return target


class Harness:
    def __init__(self, db: Database, tmp_path: Path, *, duration_sec: float = 125.0) -> None:
        self.assets = SqliteAssetRepository(db)
        self.observations = SqliteObservationRepository(db)
        self.events = SqliteEventRepository(db)
        self.places = SqliteAssetPlaceRepository(db)
        self.videos = SqliteVideoRepository(db)
        self.extractor = RecordingKeyframeExtractor(tmp_path / "frames")
        self._holder: dict[str, ImportAssets] = {}
        self.scenes = ExtractVideoScenes(
            assets=self.assets,
            events=self.events,
            places=self.places,
            video_repository=self.videos,
            keyframes=self.extractor,
            process_image=self._process_image,
        )
        self.importer = ImportAssets(
            assets=self.assets,
            observations=self.observations,
            storage=FakeStorage(tmp_path / "media"),
            metadata=FakeMetadata(),
            video_metadata=FakeVideoMetadataReader(duration_sec),
            video_repository=self.videos,
            event_attacher=lambda event_id, observation: self.events.attach(event_id, observation.id),
            video_scene_extractor=lambda asset_id: self.scenes.execute(asset_id).scene_count,
        )
        self._holder["importer"] = self.importer

    def _process_image(self, asset, event_id):  # noqa: ANN001
        # 与生产装配同一条：关键帧走 ``ImportAssets.process_image``（强制归属）。
        return self._holder["importer"].process_image(asset, forced_event_id=event_id)

    def import_video(self, *, name: str = "clip.mov", content: bytes = b"mov-bytes"):  # noqa: ANN201
        return self.importer.execute(
            ImportRequest(scope_id="album1", file_name=name, content_type=None, content=content)
        )


def test_importing_a_video_forms_scenes_with_lineage_back_to_the_source(
    migrations_ready_db: Database, tmp_path: Path
):
    """导入 → 场景成型 → 每帧指得回原片秒数（S8b 的验收核心）。

    125 秒的片子按 60 秒一窗切成 3 段，每段取中点一帧：30 / 90 / 122.5 秒。
    """
    harness = Harness(migrations_ready_db, tmp_path)

    result = harness.import_video()

    assert result.video_scenes == 3
    scenes = harness.events.list_video_scenes(result.asset_id)
    assert [item.source_scene_index for item in scenes] == [0, 1, 2]
    assert [(item.source_start_sec, item.source_end_sec) for item in scenes] == [
        (0.0, 60.0),
        (60.0, 120.0),
        (120.0, 125.0),
    ]
    assert all(item.source_type == "video_scene" for item in scenes)
    assert all(item.source_asset_id == result.asset_id for item in scenes)

    derived = harness.videos.list_derived(result.asset_id)
    assert [item.source_frame_index for item in derived] == [0, 1, 2]
    assert [item.source_timestamp_sec for item in derived] == [30.0, 90.0, 122.5]
    assert [item.source_scene_index for item in derived] == [0, 1, 2]
    assert {item.derived_kind for item in derived} == {"video_keyframe"}
    assert {item.parent_asset_id for item in derived} == {result.asset_id}
    # 抽取器被要求抽的秒数就是窗口中点——这是「能跳回原片」在写侧的那一半。
    assert [call[2] for call in harness.extractor.calls] == [30.0, 90.0, 122.5]


def test_a_keyframe_is_an_image_asset_whose_observation_lands_in_its_scene_event(
    migrations_ready_db: Database, tmp_path: Path
):
    """T513：派生帧是 image 资产，**并且真的走进了图片流水线**（白得 observation）。

    判据是那条观察挂在**它自己那个场景事件**上（``event_attacher`` 的强制归属），
    而不是被 6 小时窗折进某个不相干的事件。
    """
    harness = Harness(migrations_ready_db, tmp_path)

    result = harness.import_video()
    scenes = harness.events.list_video_scenes(result.asset_id)
    derived = harness.videos.list_derived(result.asset_id)
    source = harness.assets.get(result.asset_id)

    for scene, frame in zip(scenes, derived, strict=True):
        frame_asset = harness.assets.get(frame.id)
        assert frame_asset is not None
        assert frame_asset.media_type is MediaType.IMAGE
        # 拍摄时间继承源片并往前挪 source_timestamp_sec（1.0 processor.py:186）。
        assert frame_asset.captured_at == source.captured_at + timedelta(seconds=frame.source_timestamp_sec)
        observation_ids = harness.events.observation_ids(scene.id)
        assert len(observation_ids) == 1
        attached = harness.observations.get(observation_ids[0])
        assert attached is not None and attached.asset_id == frame.id


def test_running_the_extraction_again_reuses_the_existing_scenes(
    migrations_ready_db: Database, tmp_path: Path
):
    """T514 幂等：重复处理同一个视频靠 ``(parent_asset_id, source_frame_index)``。

    第二次跑既不新建帧也不新建场景事件——**不靠 timestamp 判重**（同一场景里两帧
    落在同一秒是可能的，迁移 0010 表头写了为什么不拿它当键）。
    """
    harness = Harness(migrations_ready_db, tmp_path)
    result = harness.import_video()
    before = harness.videos.list_derived(result.asset_id)

    again = harness.scenes.execute(result.asset_id)

    assert (again.scene_count, again.keyframe_count, again.reused) == (3, 0, 3)
    assert [item.id for item in harness.videos.list_derived(result.asset_id)] == [item.id for item in before]
    assert len(harness.events.list_video_scenes(result.asset_id)) == 3


def test_a_long_video_is_capped_at_max_scenes_by_growing_the_window(
    migrations_ready_db: Database, tmp_path: Path
):
    """封顶：3000 秒的片子只抽 40 帧（帧数就是 VLM 调用数），窗口被放大到 75 秒。"""
    harness = Harness(migrations_ready_db, tmp_path, duration_sec=3000.0)

    result = harness.import_video()

    assert result.video_scenes == 40
    derived = harness.videos.list_derived(result.asset_id)
    assert len(derived) == 40
    assert derived[0].source_timestamp_sec == 37.5  # (0 + 75) / 2
    assert derived[-1].source_timestamp_sec == 2962.5  # (2925 + 3000) / 2


def test_a_video_without_a_probed_duration_gets_no_scenes(migrations_ready_db: Database, tmp_path: Path):
    """时长读不出来时**不猜**：一个场景都不建，但导入照收（S8a 的口径）。"""
    harness = Harness(migrations_ready_db, tmp_path, duration_sec=0.0)

    result = harness.import_video()
    extraction = harness.scenes.execute(result.asset_id)

    assert result.video_scenes == 0
    assert extraction.reason == "no_duration"
    assert harness.videos.list_derived(result.asset_id) == []


def test_cleanup_derivatives_cascades_into_faces_mentions_places_and_vectors(
    migrations_ready_db: Database, tmp_path: Path
):
    """T514：清场要删到最底下那一层。

    S8a 只删「场景事件 / 派生物 / 挂在派生物上的观察」；关键帧走完图片流水线之后
    上面还挂着人脸实例 / 实体提及 / 地点 / 三种向量 / 词法投影 / 事实证据，
    少删任何一层都会在重跑后留下挂在已删 id 上的孤儿行——它们不报错，只是再也看不见。
    """
    db = migrations_ready_db
    now = NOW.isoformat()
    parent = "asset_video"
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, created_at, updated_at) VALUES (?, 'album1','v.mov','video','/m/v',1,"
            " 'processed', ?, ?, ?)",
            (parent, parent, now, now),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, captured_at, created_at, updated_at, parent_asset_id, derived_kind,"
            " source_timestamp_sec, source_frame_index, source_scene_index)"
            " VALUES ('asset_kf','album1','f.jpg','image','/m/f',1,'processed','asset_kf', ?,?,?,"
            " ?, 'video_keyframe', 30.0, 0, 0)",
            (now, now, now, parent),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at, confidence,"
            " created_at, updated_at) VALUES ('obs_kf','album1','asset_kf','import',?,0.0,?,?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO memory_spaces (id, name, created_at, updated_at) VALUES ('album1','album1',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, created_at,"
            " updated_at) VALUES ('ent_1','album1','person','小明','confirmed',?,?)",
            (now, now),
        )
        # 场景事件要排在 person_moments 之前：后者对 events 有外键。
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, place, activity, source_type,"
            " source_asset_id, source_scene_index, source_start_sec, source_end_sec, created_at,"
            " updated_at) VALUES ('evt_1','album1','视频场景 1','视频场景','x','视频场景',"
            " 'video_scene', ?, 0, 0.0, 60.0, ?, ?)",
            (parent, now, now),
        )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, created_at, updated_at)"
            " VALUES ('cluster_1','album1',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, created_at)"
            " VALUES ('face_1','asset_kf','obs_kf',?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id,"
            " confidence, created_at) VALUES ('mention_1','ent_1','obs_kf','face_1',0.9,?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO person_moments (id, scope_id, person_id, cluster_id, event_id,"
            " observation_id, asset_id, face_instance_id, prompt_version, created_at, updated_at)"
            " VALUES ('moment_1','album1','ent_1','cluster_1','evt_1','obs_kf','asset_kf','face_1',"
            " 'v1', ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO face_prototypes (id, cluster_id, face_instance_id, created_at, updated_at)"
            " VALUES ('proto_1','cluster_1','face_1',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id, observation_id,"
            " asset_id, created_at, updated_at) VALUES ('app_1','ent_1','face_1','obs_kf','asset_kf',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO asset_places (asset_id, scope_id, precision, source, resolved_at)"
            " VALUES ('asset_kf','album1','admin','pygeocn',?)",
            (now,),
        )
        for vector_id, space, source_type, source_id in (
            ("vec_asset", "visual", "asset", "asset_kf"),
            ("vec_face", "visual", "face_instance", "face_1"),
        ):
            connection.execute(
                "INSERT INTO memory_vectors (id, scope_id, space, source_type, source_id, model_name,"
                " dimension, vector_json, metadata_json, created_at, updated_at)"
                " VALUES (?, 'album1', ?, ?, ?, 'm', 2, '[0.1,0.2]', '{}', ?, ?)",
                (vector_id, space, source_type, source_id, now, now),
            )
        for vector_id, space, source_type, source_id in (
            ("vec_obs", "episodic", "observation", "obs_kf"),
            ("vec_event", "episodic", "event", "evt_1"),
        ):
            connection.execute(
                "INSERT INTO text_vectors (id, scope_id, space, source_type, source_id, model_name,"
                " dimension, vector_json, metadata_json, created_at, updated_at)"
                " VALUES (?, 'album1', ?, ?, ?, 'm', 2, '[0.1,0.2]', '{}', ?, ?)",
                (vector_id, space, source_type, source_id, now, now),
            )
        connection.execute(
            "INSERT INTO observation_search_terms (id, observation_id, asset_id, scope_id, field_type,"
            " normalized_value, created_at, updated_at)"
            " VALUES ('term_1','obs_kf','asset_kf','album1','caption','x',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO event_observations (event_id, observation_id, created_at)"
            " VALUES ('evt_1','obs_kf',?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO event_participants (id, event_id, person_id, role, created_at, updated_at)"
            " VALUES ('part_1','evt_1','ent_1','visible_subject',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO facts (id, scope_id, subject, predicate, object, evidence_ids_json,"
            " created_at, updated_at) VALUES ('fact_1','album1','小明','喜欢','跑步',?,?,?)",
            ('["obs_kf"]', now, now),
        )

    removed = SqliteVideoRepository(db).cleanup_derivatives(parent)

    assert removed == 1
    with db.connect() as connection:
        for table, column, value in (
            ("assets", "id", "asset_kf"),
            ("observations", "id", "obs_kf"),
            ("face_instances", "id", "face_1"),
            ("entity_mentions", "id", "mention_1"),
            ("person_moments", "id", "moment_1"),
            ("face_prototypes", "id", "proto_1"),
            ("person_appearance_evidence", "id", "app_1"),
            ("asset_places", "asset_id", "asset_kf"),
            ("observation_search_terms", "id", "term_1"),
            ("events", "id", "evt_1"),
            ("event_observations", "event_id", "evt_1"),
            ("event_participants", "id", "part_1"),
            ("facts", "id", "fact_1"),
        ):
            row = connection.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (value,)
            ).fetchone()
            assert row["n"] == 0, f"{table} 还有 {row['n']} 行没清掉"
        # 向量分表判：视觉两向（asset / face_instance）在 memory_vectors，
        # 文本两向（observation / event）在 text_vectors。
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_vectors WHERE source_id IN ('asset_kf','face_1')"
        ).fetchone()
        assert row["n"] == 0
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM text_vectors WHERE source_id IN ('obs_kf','evt_1')"
        ).fetchone()
        assert row["n"] == 0
        # 源片自己必须还在——清场清的是派生物，不是原片。
        assert (
            connection.execute("SELECT COUNT(*) AS n FROM assets WHERE id = 'asset_video'").fetchone()["n"]
            == 1
        )


def test_a_video_scene_summary_is_not_exempt_from_the_participant_refresh(
    migrations_ready_db: Database, confirmed_person_events, tmp_path: Path
):
    """T512：**2.0 不豁免 ``video_scene`` 的摘要刷新**（修 1.0 的缺陷）。

    1.0 ``db.py:5576`` 对 ``source_type == 'video_scene'`` 跳过 ``refresh_event_summary``，
    于是场景摘要永远停在导入时算的那一次。这里用 ``ConfirmedPersonEvents.refresh_participants``
    走一遍：确认的人写进参与者之后，摘要必须被重写（``revision`` 加一、正文换成参与者那句）。
    谁把 1.0 那条 ``if event.source_type != 'video_scene'`` 抄回来，这一条就红。
    """
    db = migrations_ready_db
    now = NOW.isoformat()
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, size_bytes, status,"
            " content_sha256, created_at, updated_at) VALUES ('asset_v','album1','v.mov','video',"
            " '/m/v',1,'processed','asset_v',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at, confidence,"
            " created_at, updated_at) VALUES ('obs_v','album1','asset_v','import',?,0.0,?,?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, created_at,"
            " updated_at) VALUES ('ent_v','album1','person','小明','confirmed',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, confidence, created_at)"
            " VALUES ('mention_v','ent_v','obs_v',0.9,?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, place, activity, summary,"
            " source_type, source_asset_id, source_scene_index, source_start_sec, source_end_sec,"
            " created_at, updated_at) VALUES ('evt_v','album1','视频场景 1','视频场景','测试地点',"
            " '视频场景','视频场景 · 0.0s~60.0s','video_scene','asset_v',0,0.0,60.0,?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO event_observations (event_id, observation_id, created_at)"
            " VALUES ('evt_v','obs_v',?)",
            (now,),
        )

    refreshed = confirmed_person_events(db).refresh_participants(["obs_v"])

    assert refreshed == ("evt_v",)
    event = SqliteEventRepository(db).get("evt_v")
    assert event is not None
    assert event.revision == 2
    assert "小明" in event.summary
    assert "0.0s~60.0s" in event.summary
    assert event.summary != "视频场景 · 0.0s~60.0s"

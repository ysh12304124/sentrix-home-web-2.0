"""确认之后的外观取样（T85d）。

移植 1.0 ``app.py:2022-2059`` 的 ``_analyze_confirmed_person_appearance``。它的形态就是
一句话——**每人每事件取一张**——所以这份用例的重心在两处：那个「每事件一张」的取样规则
（多给几张脸，看它挑哪张、调了几次模型），以及**一次失败不拖垮整批**。

「失败」在这里有三张不同的面孔，各有一条用例，因为它们在 1.0 里共用同一个 ``except``：

- 文件不在磁盘上（``app.py:2036`` 的 ``is_file()``）；
- 图读不了 / 框退化 / 裁出来太小（``expanded_person_crop`` 抛 ``ValueError``）；
- 模型返回 ``None``（模型不可用、回复不是 JSON）。

**仓储的错误不在此列**，这是 2.0 相对 1.0 的一处有意收紧：``appearance.py`` 的三重守卫
（尤其「这张脸真的确认给了这个人」）被一起吞掉的话，它在生产路径上就永远看不见。
``test_a_repository_guard_is_not_swallowed`` 钉住这条收紧。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sentrix.application.identity.appearance_sampling import (
    AppearanceSamplingExecutor,
    QueueAppearance,
)
from sentrix.application.identity.observe_appearance import ObserveAppearance
from sentrix.infrastructure.media.person_vision import PillowPersonCropRenderer
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import (
    SqliteAppearanceRepository,
)
from sentrix.infrastructure.persistence.repositories.appearance_jobs import (
    MAX_ATTEMPTS,
    SqlitePersonAppearanceJobs,
)
from sentrix.infrastructure.persistence.repositories.assets import SqliteObservationRepository
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository

NOW = "2026-09-28T10:00:00+00:00"
CAPTURED = "2026-09-27T18:30:00+00:00"
HOME = "album1"
PERSON = "person_mom"
OTHER_PERSON = "person_dad"

# 400×600 底图上的一张脸。期望的裁剪框按 `expanded_person_crop` 手算：
#   face 22×32 → center_x 201 → crop_width 29.7
#   left = round(201-14.85) = 186   right = round(201+14.85) = 216
#   top  = round(100-11.2)  = 89    bottom = round(132+144)  = 276
FACE = [190.0, 100.0, 212.0, 132.0]
CROP_BBOX = (186.0, 89.0, 216.0, 276.0)


class ScriptedVision:
    """记下每次调用，按队列回话。队列空了就回 ``{"clothing": [], "confidence": 0.0}``。"""

    def __init__(self, replies: list[dict[str, object] | None] | None = None) -> None:
        self.model_id = "scripted-vlm"
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._replies = list(replies or [])
        self._alive: list[bool] = []

    def analyze_person_appearance(self, path: str, metadata=None):
        # **当场**求值并存下来。存路径、断言时再查是错的：那时临时图已经被删掉，
        # 查到的永远是 False，而那与「一开始就没写出来」是同一种结果——用例就废了。
        # 临时图必须在模型看完之后才删，所以这一刻它一定还在。
        self._alive.append(Path(path).is_file())
        self.calls.append((path, dict(metadata or {})))
        return self._replies.pop(0) if self._replies else {"clothing": [], "confidence": 0.0}

    def write_person_portrait(self, pack):
        raise AssertionError("这份用例不该碰画像")

    def analyze_person_moments(self, path, labels, context=None):
        raise AssertionError("这份用例不该碰瞬间")

    def image_existed_at_call_time(self) -> list[bool]:
        return self._alive


class Album:
    """一个装了真 JPEG 的库：造图、造实例、造提及。"""

    def __init__(self, db: Database, tmp_path: Path) -> None:
        self.db = db
        self.tmp_path = tmp_path
        self._n = 0
        with db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO memory_spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (HOME, HOME, NOW, NOW),
            )
            for entity_id, name in ((PERSON, "妈妈"), (OTHER_PERSON, "爸爸")):
                connection.execute(
                    "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                    " summary, confidence, created_at, updated_at)"
                    " VALUES (?, ?, 'person', ?, 'confirmed', '', 1.0, ?, ?)",
                    (entity_id, HOME, name, NOW, NOW),
                )
            for event_id in ("event_a", "event_b"):
                connection.execute(
                    "INSERT INTO events (id, scope_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (event_id, HOME, NOW, NOW),
                )

    def photo(self, name: str, *, size: tuple[int, int] = (400, 600), missing: bool = False) -> str:
        path = self.tmp_path / f"{name}.jpg"
        if not missing:
            Image.new("RGB", size, (255, 0, 0)).save(path, format="JPEG", quality=100)
        return str(path)

    def add(
        self,
        *,
        event_id: str,
        quality: float,
        detection_confidence: float = 0.9,
        path: str | None = None,
        person_id: str = PERSON,
        bbox: list[float] | None = None,
        capture_time: str | None = CAPTURED,
    ) -> str:
        """造一张脸：asset + observation + 事件归属 + 实例 + 提及。返回实例 id。"""
        self._n += 1
        n = self._n
        asset_id, observation_id, instance_id = f"asset_{n}", f"obs_{n}", f"ins_{n}"
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, HOME, f"p{n}.jpg", path or self.photo(f"p{n}"), NOW, NOW),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
                (observation_id, HOME, asset_id, capture_time, NOW, NOW),
            )
            connection.execute(
                "INSERT INTO event_observations (event_id, observation_id, created_at) VALUES (?, ?, ?)",
                (event_id, observation_id, NOW),
            )
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, bbox_json,"
                " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
                " embedding_model, embedding_version, created_at)"
                " VALUES (?, ?, ?, ?, '[]', ?, ?, '{}', 'front', 'buffalo_l', 'w600k_r50', ?)",
                (
                    instance_id,
                    asset_id,
                    observation_id,
                    json.dumps(FACE if bbox is None else bbox),
                    detection_confidence,
                    quality,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id,"
                " confidence, created_at) VALUES (?, ?, ?, ?, 0.9, ?)",
                (f"men_{n}", person_id, observation_id, instance_id, NOW),
            )
        return instance_id


@pytest.fixture
def album(migrations_ready_db: Database, tmp_path: Path) -> Album:
    return Album(migrations_ready_db, tmp_path)


@pytest.fixture
def db(album: Album) -> Database:
    return album.db


def build(db: Database, vision: ScriptedVision) -> ObserveAppearance:
    return ObserveAppearance(
        faces=SqliteFaceRepository(db),
        entities=SqliteEntityRepository(db),
        observations=SqliteObservationRepository(db),
        appearances=SqliteAppearanceRepository(db),
        crops=PillowPersonCropRenderer(),
        vision=vision,
    )


def evidence(db: Database, person_id: str = PERSON):
    return SqliteAppearanceRepository(db).list_for(person_id, include_empty=True)


# ---- 取样：每人每事件取一张 ----------------------------------------------------------


def test_one_photo_per_event_however_many_faces(album: Album, db: Database) -> None:
    """**这个用例的形态就是这条规则。** 同一个事件里三张脸 → 一次模型调用。

    1.0 的 SQL 是 ``ORDER BY quality DESC, detection_confidence DESC LIMIT 1``：一个人
    在一个事件里被拍到多少次都只送一张，因为同一场合同一身衣服，多送不产生新信息，
    只是把导入的模型调用次数乘以每个事件的照片数。
    """
    album.add(event_id="event_a", quality=0.9, detection_confidence=0.8)
    album.add(event_id="event_a", quality=0.5, detection_confidence=0.99)
    album.add(event_id="event_a", quality=0.7, detection_confidence=0.7)
    vision = ScriptedVision()

    recorded = build(db, vision)(PERSON)

    assert recorded == 1
    assert len(vision.calls) == 1


def test_each_event_contributes_its_own_pick(album: Album, db: Database) -> None:
    """两个事件 → 两张证据。跨事件累积，事件内只取一张。"""
    album.add(event_id="event_a", quality=0.9)
    album.add(event_id="event_a", quality=0.4)
    album.add(event_id="event_b", quality=0.6)
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 2
    assert len(vision.calls) == 2
    assert {record.crop_bbox for record in evidence(db)} == {CROP_BBOX}


def test_quality_beats_detection_confidence(album: Album, db: Database) -> None:
    """排序**先看质量**：质量低但检测置信度更高的那张不该被选中。

    两列的顺序是 1.0 的 ``ORDER BY fi.quality DESC, fi.detection_confidence DESC``。

    **差的那张先入库。** 这不是随手写的顺序：SQLite 没有 ORDER BY 时通常按插入顺序
    出结果，所以差的那张先入库，才让「排序两列对调」与「整个 ORDER BY 掉了」两种
    改法都会选错。反过来写（好的先入库）时，掉了 ORDER BY 的实现照样能过，
    这条用例就只守得住前一种改法。
    """
    album.add(event_id="event_a", quality=0.3, detection_confidence=0.99)
    high_quality = album.add(event_id="event_a", quality=0.9, detection_confidence=0.5)
    vision = ScriptedVision()

    build(db, vision)(PERSON)

    assert len(evidence(db)) == 1
    assert evidence(db)[0].face_instance_id == high_quality


def test_an_instance_not_mentioned_for_this_person_is_never_picked(album: Album, db: Database) -> None:
    """**这个人在这个事件里没有脸 → 一次模型都不调。**

    库里那张脸是另一个人的（``entity_mentions`` 认给 ``person_dad``）。少了 JOIN 那一条
    判据，它会被当成妈妈的脸送进模型——而模型的职责恰好是「描述这个目标人物的衣物」，
    于是爸爸的衣服记到了妈妈头上。这正是外观证据第三重守卫挡的那件事，在取样这一步
    先挡一次。
    """
    album.add(event_id="event_a", quality=0.99, person_id=OTHER_PERSON)
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 0
    assert vision.calls == []


def test_an_instance_in_no_event_is_never_picked(album: Album, db: Database) -> None:
    """没有事件归属的脸取不到——``event_ids`` 数不出它，取样的循环就碰不到它。"""
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES ('asset_x', ?, 'x.jpg', 'image', ?, ?, ?)",
            (HOME, album.photo("x"), NOW, NOW),
        )
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 0
    assert vision.calls == []


# ---- 三张失败的面孔，各不拖垮整批 ----------------------------------------------------


def test_a_missing_file_is_skipped_and_the_batch_continues(album: Album, db: Database) -> None:
    """库里有一行、磁盘上没有那个文件：跳过它，后面的照做（1.0 ``app.py:2036``）。"""
    album.add(event_id="event_a", quality=0.9, path=album.photo("gone", missing=True))
    album.add(event_id="event_b", quality=0.9)
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 1
    assert len(vision.calls) == 1


def test_a_crop_too_small_is_skipped_and_the_batch_continues(album: Album, db: Database) -> None:
    """图太小 → ``expanded_person_crop`` 抛 ``ValueError`` → 跳过，不中断整批。

    底图 30×40，脸框就是整张图：裁出来的高度只有 40px，低于 ``MIN_CROP_HEIGHT = 48``。
    """
    album.add(
        event_id="event_a", quality=0.9, path=album.photo("tiny", size=(30, 40)), bbox=[0.0, 0.0, 30.0, 40.0]
    )
    album.add(event_id="event_b", quality=0.9)
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 1
    assert len(vision.calls) == 1


def test_an_unreadable_image_is_skipped_and_the_batch_continues(
    album: Album, db: Database, tmp_path: Path
) -> None:
    """文件在、但不是图：PIL 抛 ``OSError`` 那一族 → 跳过，不中断整批。"""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image at all")
    album.add(event_id="event_a", quality=0.9, path=str(broken))
    album.add(event_id="event_b", quality=0.9)
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 1
    assert len(vision.calls) == 1


def test_a_model_failure_records_nothing_but_the_batch_continues(album: Album, db: Database) -> None:
    """模型返回 ``None``（不可用 / 不是 JSON）→ 这张不算数，后面的照做。

    外观是增强，不是确认的前置条件——所以模型整个挂掉时，确认这件事本身仍然成立。
    """
    album.add(event_id="event_a", quality=0.9)
    album.add(event_id="event_b", quality=0.9)
    vision = ScriptedVision([None, {"clothing": ["蓝外套"], "confidence": 0.8}])

    assert build(db, vision)(PERSON) == 1
    assert [record.clothing for record in evidence(db)] == [("蓝外套",)]


def test_a_repository_guard_is_not_swallowed(album: Album, db: Database) -> None:
    """**2.0 相对 1.0 的一处有意收紧。** 未确认的人 → 仓储抛 ``ValueError`` → 往上走。

    1.0 那个大 ``except (OSError, ValueError, RuntimeError)`` 会把这条一起吞掉，
    于是「有人把 A 的衣服记到 B 头上」在生产上只剩一个静默的 ``continue``。
    这里要求它穿出来：媒体失败是数据问题（这张照片用不了），守卫失败是代码问题。
    """
    album.add(event_id="event_a", quality=0.9)
    with db.connect() as connection, connection:
        connection.execute("UPDATE entities SET status = 'pending' WHERE id = ?", (PERSON,))

    with pytest.raises(ValueError, match="confirmed person"):
        build(db, ScriptedVision())(PERSON)


# ---- 落库内容 -----------------------------------------------------------------------


def test_the_recorded_crop_bbox_is_the_crop_not_the_face(album: Album, db: Database) -> None:
    """落库的是**裁剪框**的源坐标，不是人脸框——两者差 4.5 倍身高，一眼能分开。

    这一列是事后复核的唯一依据（``expanded_person_crop`` 的 docstring 写明）：
    只存裁剪件等于把「这段描述对应照片的哪一块」丢掉。
    """
    album.add(event_id="event_a", quality=0.9)

    build(db, ScriptedVision())(PERSON)

    record = evidence(db)[0]
    assert record.crop_bbox == CROP_BBOX
    assert record.crop_bbox != tuple(FACE), "落成了人脸框，说明裁剪那一步被跳过了"


def test_the_model_name_comes_from_the_port_not_from_the_reply(album: Album, db: Database) -> None:
    """``model_name`` 取自 ``vision.model_id``。

    1.0 从模型**回复**里取 ``model``（``model_clients.py:1350``），2.0 提到端口上
    （``PersonVision`` 的 docstring 写明）：这次请求用哪个模型不该由一次回复的内容决定。
    这里让回复里塞一个假模型名，落库的必须仍是端口那个。
    """
    album.add(event_id="event_a", quality=0.9)
    vision = ScriptedVision([{"clothing": ["红裙"], "confidence": 0.9, "model": "冒牌模型"}])

    build(db, vision)(PERSON)

    assert evidence(db)[0].model_name == "scripted-vlm"


def test_the_metadata_is_the_three_fields_1_0_sent(album: Album, db: Database) -> None:
    """提示词末尾拼的就是这个 dict（``json.dumps`` 之后），所以**键数就是模型输入**。

    1.0 ``app.py:2046-2050`` 只有三个键。多一个键就是改了模型看到的东西——而这种改动
    在提示词那条用例里看不出来（那条比的是模板，模板没变）。
    """
    instance_id = album.add(event_id="event_a", quality=0.9)
    vision = ScriptedVision()

    build(db, vision)(PERSON)

    _, metadata = vision.calls[0]
    assert set(metadata) == {"face_instance_id", "target_face_bbox", "capture_time"}
    assert metadata["face_instance_id"] == instance_id
    assert metadata["target_face_bbox"] == FACE
    assert metadata["capture_time"] == CAPTURED


def test_a_missing_capture_time_is_null_not_a_crash(album: Album, db: Database) -> None:
    """观察没有拍摄时间时是 ``null``（1.0 的 ``(store.get_observation(...) or {}).get(...)``）。"""
    album.add(event_id="event_a", quality=0.9, capture_time=None)
    vision = ScriptedVision()

    assert build(db, vision)(PERSON) == 1
    assert vision.calls[0][1]["capture_time"] is None


def test_an_empty_reply_is_recorded_as_empty_not_as_a_missing_row(album: Album, db: Database) -> None:
    """模型回空数组也要落库：空表的意思是「模型没能可靠归属给目标人物」，
    与「没采过」是两件事。默认的 ``list_for`` 会把空表滤掉，所以要用
    ``include_empty=True`` 看（``AppearanceRepository`` 的 docstring 写明这条）。"""
    album.add(event_id="event_a", quality=0.9)
    vision = ScriptedVision([{"clothing": [], "confidence": 0.9}])

    assert build(db, vision)(PERSON) == 1
    assert evidence(db)[0].clothing == ()
    assert SqliteAppearanceRepository(db).list_for(PERSON) == []


def test_the_temporary_crop_is_deleted_after_the_model_sees_it(album: Album, db: Database) -> None:
    """临时图**在模型看过之后**才删，且一定删掉。

    两头都要钉：删早了模型拿到的是一个不存在的路径（而两边都不报错，只有那次调用会
    失败）；不删就把每次确认都变成一次磁盘泄漏。所以这里在调用当场记下「文件在不在」，
    再看调用之后它没了。
    """
    album.add(event_id="event_a", quality=0.9)
    vision = ScriptedVision()

    build(db, vision)(PERSON)

    assert vision.image_existed_at_call_time() == [True]
    assert not Path(vision.calls[0][0]).exists()


def test_the_temporary_crop_is_deleted_even_when_the_model_raises(album: Album, db: Database) -> None:
    """模型抛异常时临时文件也得删——``finally`` 那一句就是为这个路径写的。"""
    album.add(event_id="event_a", quality=0.9)

    class Exploding(ScriptedVision):
        def analyze_person_appearance(self, path, metadata=None):
            self._alive.append(Path(path).is_file())
            self.calls.append((path, dict(metadata or {})))
            raise RuntimeError("模型炸了")

    vision = Exploding()
    with pytest.raises(RuntimeError):
        build(db, vision)(PERSON)

    assert vision.image_existed_at_call_time() == [True]
    assert not Path(vision.calls[0][0]).exists()


def test_a_second_run_updates_instead_of_duplicating(album: Album, db: Database) -> None:
    """同一个人同一个实例同一个模型跑两次 → 一行（唯一键在仓储那层）。

    确认是会被重复点的，这条钉住「重复确认不会让证据翻倍」。
    """
    album.add(event_id="event_a", quality=0.9)

    build(db, ScriptedVision())(PERSON)
    build(db, ScriptedVision())(PERSON)

    assert len(evidence(db)) == 1


# ---- 已采过的脸不再送模型（2026-11-20：取样移到后台、可增量）---------------------------


def test_a_face_already_sampled_is_not_sent_to_the_model_again(album: Album, db: Database) -> None:
    """重复确认、确认后再合并：已采过的事件不再付那十秒，只给新事件调模型。"""
    album.add(event_id="event_a", quality=0.9)
    build(db, ScriptedVision())(PERSON)

    album.add(event_id="event_b", quality=0.9)
    second = ScriptedVision()
    build(db, second)(PERSON)

    assert len(second.calls) == 1
    assert len(evidence(db)) == 2


def test_a_rejected_evidence_is_sampled_again(album: Album, db: Database) -> None:
    """被驳回的证据不算「采过」：重新采到之后它应当重新生效（``record`` 的原样行为）。"""
    album.add(event_id="event_a", quality=0.9)
    build(db, ScriptedVision())(PERSON)
    with db.connect() as connection, connection:
        connection.execute("UPDATE person_appearance_evidence SET status = 'rejected'")

    again = ScriptedVision()
    build(db, again)(PERSON)

    assert len(again.calls) == 1
    assert [item.status for item in evidence(db)] == ["confirmed"]


# ---- 后台取样：确认只入队，线程逐人调模型 -------------------------------------------------


def _executor(db: Database, vision: ScriptedVision) -> AppearanceSamplingExecutor:
    return AppearanceSamplingExecutor(
        jobs=SqlitePersonAppearanceJobs(db),
        entities=SqliteEntityRepository(db),
        observe=build(db, vision),
    )


def test_queueing_does_not_call_the_model(album: Album, db: Database) -> None:
    album.add(event_id="event_a", quality=0.9)
    jobs = SqlitePersonAppearanceJobs(db)

    QueueAppearance(jobs)(PERSON)

    assert jobs.status(PERSON) == "pending"
    assert evidence(db) == []


def test_the_executor_samples_a_queued_person_and_clears_the_job(album: Album, db: Database) -> None:
    album.add(event_id="event_a", quality=0.9)
    album.add(event_id="event_b", quality=0.9)
    jobs = SqlitePersonAppearanceJobs(db)
    QueueAppearance(jobs)(PERSON)
    QueueAppearance(jobs)(PERSON)  # 确认后马上合并：两次入队合并成一行

    vision = ScriptedVision()
    assert _executor(db, vision).run_once() == 1

    assert len(vision.calls) == 2
    assert len(evidence(db)) == 2
    assert jobs.status(PERSON) is None


def test_a_person_no_longer_confirmed_is_skipped(album: Album, db: Database) -> None:
    """入队之后人被并走或驳回：不再给他采，任务照样清掉。"""
    album.add(event_id="event_a", quality=0.9)
    jobs = SqlitePersonAppearanceJobs(db)
    QueueAppearance(jobs)(PERSON)
    with db.connect() as connection, connection:
        connection.execute("UPDATE entities SET status = 'superseded' WHERE id = ?", (PERSON,))

    vision = ScriptedVision()
    _executor(db, vision).run_once()

    assert vision.calls == []
    assert jobs.status(PERSON) is None


def test_a_failure_backs_off_and_finally_marks_the_job_failed(album: Album, db: Database) -> None:
    album.add(event_id="event_a", quality=0.9)
    jobs = SqlitePersonAppearanceJobs(db)
    QueueAppearance(jobs)(PERSON)

    class Boom(ScriptedVision):
        def analyze_person_appearance(self, path: str, metadata=None):
            raise RuntimeError("model down")

    executor = _executor(db, Boom())
    for _ in range(MAX_ATTEMPTS):
        executor.run_once()
        assert jobs.status(PERSON) in {"pending", "failed"}
        # 退避期间不会被再次取走；测试里直接把重试时间拨到现在。
        with db.connect() as connection, connection:
            connection.execute("UPDATE person_appearance_jobs SET next_attempt_at = ''")

    assert jobs.status(PERSON) == "failed"
    assert executor.run_once() == 0


def test_a_job_queued_again_while_running_runs_once_more(album: Album, db: Database) -> None:
    """跑到一半又入队（合并带来新的脸）：这一轮跑完不删行，下一轮再跑。"""
    album.add(event_id="event_a", quality=0.9)
    jobs = SqlitePersonAppearanceJobs(db)
    QueueAppearance(jobs)(PERSON)

    claimed = jobs.claim_next()
    assert claimed is not None
    QueueAppearance(jobs)(PERSON)
    jobs.finish(*claimed)

    assert jobs.status(PERSON) == "pending"


def test_a_job_left_running_by_a_dead_process_is_requeued(album: Album, db: Database) -> None:
    jobs = SqlitePersonAppearanceJobs(db)
    QueueAppearance(jobs)(PERSON)
    assert jobs.claim_next() is not None
    assert jobs.claim_next() is None

    assert jobs.requeue_running() == 1
    assert jobs.status(PERSON) == "pending"

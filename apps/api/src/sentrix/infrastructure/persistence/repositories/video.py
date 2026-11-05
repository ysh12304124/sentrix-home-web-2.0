"""视频血缘仓储。SQL 只出现在这一层。

对应 1.0 ``backend/db.py`` 的 ``list_derived_assets``（:2383）与
``cleanup_video_derivatives``（:2435），加上 2.0 单开的探测结果读写
（1.0 把 ``video_metadata`` / ``browser_preview_path`` 直接摊在 ``assets.metadata_json`` 上，
写完就没有回读的地方——2.0 的 ``GET /videos/{id}`` 要把它拿出来，所以有了成对的读写）。

**元数据为什么留在 ``metadata_json`` 而不是摊成列。** 迁移 0010 的表头注释定了这条：
每类媒体自己的元数据形状不同（EXIF / 视频 / 音频），摊成列会让列数随媒体类型膨胀，
而视频这几个字段都不参与检索过滤。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from sentrix.domain.media.models import Asset, DerivedAsset, VideoMetadata
from sentrix.domain.media.ports import VideoProbe
from sentrix.infrastructure.persistence.database import Database

# 写进 ``assets.metadata_json`` 的键。前三个是 1.0 的键名（``processor.py:118-121``），
# ``video_probe_error`` 是 2.0 加的（探测失败的原因，1.0 那时是整条资产标 failed），
# ``video_processing_seconds`` 是 1.0 的键名（``processor.py:205`` / ``:420``，
# PhotoBench 的关键帧分析读它，S8a 补回）。
_VIDEO_METADATA_KEY = "video_metadata"
_PREVIEW_PATH_KEY = "browser_preview_path"
_PREVIEW_ERROR_KEY = "browser_preview_error"
_PROBE_ERROR_KEY = "video_probe_error"
_PROCESSING_SECONDS_KEY = "video_processing_seconds"

_VIDEO_KEYS = (_VIDEO_METADATA_KEY, _PREVIEW_PATH_KEY, _PREVIEW_ERROR_KEY, _PROBE_ERROR_KEY)

# VideoMetadata 的字段名与字典键一一对应，构造时按它取；缺键用 dataclass 的默认值，
# 所以老库/手改过的 payload 不会让读侧炸。
_METADATA_FIELDS = (
    "captured_at",
    "latitude",
    "longitude",
    "captured_location",
    "duration_sec",
    "fps",
    "width",
    "height",
    "codec",
    "rotation",
    "device",
    "creation_source",
    "frame_count",
)

_DERIVED_COLUMNS = (
    "id, file_name, media_type, status, captured_at, gps_latitude, gps_longitude,"
    " parent_asset_id, derived_kind, source_timestamp_sec, source_frame_index, source_scene_index"
)


def _placeholders(values: Sequence[object]) -> str:
    """一串 ``?``。清理级联里 ``IN`` 的名单全是这样拼的——SQLite 不支持绑定一个列表。"""
    return ",".join("?" for _ in values)


def _parse_metadata(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_payload(metadata: VideoMetadata) -> dict[str, object]:
    return {field: getattr(metadata, field) for field in _METADATA_FIELDS}


def _metadata_from_payload(payload: object) -> VideoMetadata | None:
    if not isinstance(payload, dict):
        return None
    known = {field: payload[field] for field in _METADATA_FIELDS if field in payload}
    return VideoMetadata(**known)


def _location_text(latitude: float | None, longitude: float | None) -> str | None:
    """派生物的 ``captured_location``。与 ``GpsPoint.as_text`` 同一个表示（六位小数）。"""
    if latitude is None or longitude is None:
        return None
    return f"{float(latitude):.6f},{float(longitude):.6f}"


def _row_to_derived(row: sqlite3.Row) -> DerivedAsset:
    return DerivedAsset(
        id=row["id"],
        file_name=row["file_name"],
        media_type=row["media_type"],
        status=row["status"],
        captured_at=row["captured_at"],
        captured_location=_location_text(row["gps_latitude"], row["gps_longitude"]),
        parent_asset_id=row["parent_asset_id"],
        derived_kind=row["derived_kind"],
        source_timestamp_sec=row["source_timestamp_sec"],
        source_frame_index=row["source_frame_index"],
        source_scene_index=row["source_scene_index"],
    )


class SqliteVideoRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_probe(self, asset_id: str, probe: VideoProbe) -> None:
        """**合并写**，不整包覆盖 ``metadata_json``：那里可能已经有别的东西
        （1.0 同一列上还压着反地理、事实 id 等），覆盖会把它们一起抹掉。"""
        with self._database.connect() as connection, connection:
            row = connection.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            payload = _parse_metadata(row["metadata_json"])
            payload[_PREVIEW_PATH_KEY] = probe.browser_preview_path
            payload[_PREVIEW_ERROR_KEY] = probe.browser_preview_error
            payload[_PROBE_ERROR_KEY] = probe.probe_error
            # metadata 为 None（探测失败）时**不写这个键**：留下一份空元数据会让
            # 「探测过但没读出来」与「读出来是空的」分不开。失败原因在上面那个键里。
            if probe.metadata is not None:
                payload[_VIDEO_METADATA_KEY] = _metadata_payload(probe.metadata)
            connection.execute(
                "UPDATE assets SET metadata_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), asset_id),
            )

    def save_processing_seconds(self, asset_id: str, seconds: float) -> None:
        """**合并写**，只动 ``video_processing_seconds`` 这一个键（与 ``save_probe`` 同一条
        理由：``metadata_json`` 上可能还压着别的东西，覆盖会把它们一起抹掉）。"""
        with self._database.connect() as connection, connection:
            row = connection.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            payload = _parse_metadata(row["metadata_json"])
            payload[_PROCESSING_SECONDS_KEY] = float(seconds)
            connection.execute(
                "UPDATE assets SET metadata_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), asset_id),
            )

    def probe(self, asset_id: str) -> VideoProbe | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            return None
        payload = _parse_metadata(row["metadata_json"])
        if not any(key in payload for key in _VIDEO_KEYS):
            return None
        seconds = payload.get(_PROCESSING_SECONDS_KEY)
        return VideoProbe(
            metadata=_metadata_from_payload(payload.get(_VIDEO_METADATA_KEY)),
            browser_preview_path=str(payload.get(_PREVIEW_PATH_KEY) or ""),
            browser_preview_error=str(payload.get(_PREVIEW_ERROR_KEY) or ""),
            probe_error=str(payload.get(_PROBE_ERROR_KEY) or ""),
            # 缺键 / 老库 / 类型不对都回落 None——「没记过」与「记了 0 秒」要能分开。
            processing_seconds=float(seconds) if isinstance(seconds, (int, float)) else None,
        )

    def list_derived(self, parent_asset_id: str, *, scene_index: int | None = None) -> Sequence[DerivedAsset]:
        where = "parent_asset_id = ? AND derived_kind IS NOT NULL"
        params: list[object] = [parent_asset_id]
        if scene_index is not None:
            where += " AND source_scene_index = ?"
            params.append(int(scene_index))
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_DERIVED_COLUMNS} FROM assets WHERE {where}"
                " ORDER BY source_scene_index, source_timestamp_sec, source_frame_index",
                params,
            ).fetchall()
        return [_row_to_derived(row) for row in rows]

    def add_derived(
        self,
        *,
        parent_asset_id: str,
        asset: Asset,
        derived_kind: str,
        source_timestamp_sec: float,
        source_frame_index: int,
        source_scene_index: int,
    ) -> None:
        """落一张派生帧，连同迁移 0010 的血缘列一次写完。

        **血缘列只能在这里写**：``SqliteAssetRepository.add`` 是通用写入，不认识那几列，
        而 ``Asset`` 里也没有它们（见 ``domain/media/ports.py`` 的 ``add_derived`` 注释）。

        ``(parent_asset_id, source_frame_index)`` 上的唯一索引**故意不用 DO NOTHING 兜**：
        重复插入要炸出来。抽取链靠 ``list_derived`` 前置判重保证幂等，索引是那道判重
        写错时的最后一道网——把它静音掉，重复处理一个视频就会静默多出一批孤儿观察。
        """
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, mime_type,"
                " size_bytes, status, content_sha256, captured_at, gps_latitude, gps_longitude,"
                " created_at, updated_at, parent_asset_id, derived_kind, source_timestamp_sec,"
                " source_frame_index, source_scene_index)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset.id,
                    asset.scope_id,
                    asset.file_name,
                    asset.media_type.value,
                    asset.path,
                    asset.mime_type,
                    asset.size_bytes,
                    asset.status.value,
                    asset.content_sha256,
                    asset.captured_at.isoformat() if asset.captured_at else None,
                    asset.captured_location.latitude if asset.captured_location else None,
                    asset.captured_location.longitude if asset.captured_location else None,
                    asset.created_at.isoformat(),
                    asset.updated_at.isoformat(),
                    parent_asset_id,
                    derived_kind,
                    float(source_timestamp_sec),
                    int(source_frame_index),
                    int(source_scene_index),
                ),
            )

    def cleanup_derivatives(self, parent_asset_id: str) -> int:
        """删掉这个源片的派生物、它们的随行数据、以及场景事件，返回删掉的派生物行数。

        **顺序是硬约束**：连接开着 ``PRAGMA foreign_keys = ON``，先删主行直接
        ``IntegrityError``。总则一句话：**引用者先死**——人脸/提及/地点/向量 → 观察 →
        事件投影 → 事件 → 资产。1.0 的 ``cleanup_asset_derivatives``（``db.py:1648``）
        逐层递归同一个道理。

        **S8a 只清到「S8a 能产生的那几行」**（场景事件 / 派生物 / 挂在派生物上的观察）。
        S8b 的 T513 让关键帧真的走完了图片流水线，于是派生物上多出人脸实例 / 实体提及 /
        地点 / 向量 / 词法投影那几层——这一版把它们一起接上（T514，``s8-deviations.md``
        §4）。少删一层的代价不是报错，是**下一次重跑留下孤儿行**：它们仍挂在已经不存在的
        资产/观察 id 上，谁也看不见，直到某次按 observation_id 的连接查询莫名其妙地多出几行。
        """
        with self._database.connect() as connection, connection:
            derived_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM assets WHERE parent_asset_id = ? AND derived_kind IS NOT NULL",
                    (parent_asset_id,),
                ).fetchall()
            ]
            scene_event_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM events WHERE source_type = 'video_scene' AND source_asset_id = ?",
                    (parent_asset_id,),
                ).fetchall()
            ]
            for asset_id in derived_ids:
                observation_ids = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM observations WHERE asset_id = ?", (asset_id,)
                    ).fetchall()
                ]
                if not observation_ids:
                    # 处理到一半失败的派生物可能一行观察都没有（资产已落库、流水线没跑完）。
                    # 它仍然可能有 asset_places——那行不清掉的话，最后那句 DELETE FROM assets
                    # 会直接撞外键。报错是好的，但不该为一行地点把整次清场拖死。
                    connection.execute("DELETE FROM asset_places WHERE asset_id = ?", (asset_id,))
                    continue
                observations = tuple(observation_ids)
                obs_slots = _placeholders(observations)
                face_ids = [
                    row["id"]
                    for row in connection.execute(
                        f"SELECT id FROM face_instances WHERE observation_id IN ({obs_slots})",
                        observations,
                    ).fetchall()
                ]
                if face_ids:
                    faces = tuple(face_ids)
                    face_slots = _placeholders(faces)
                    # 人脸带出来的四张投影 + 它自己的向量，都要先走。
                    for table in (
                        "person_appearance_evidence",
                        "person_moments",
                        "face_prototypes",
                        "entity_mentions",
                    ):
                        connection.execute(
                            f"DELETE FROM {table} WHERE face_instance_id IN ({face_slots})", faces
                        )
                    connection.execute(
                        "DELETE FROM memory_vectors WHERE source_type = 'face_instance'"
                        f" AND source_id IN ({face_slots})",
                        faces,
                    )
                    connection.execute(f"DELETE FROM face_instances WHERE id IN ({face_slots})", faces)
                # 观察级：外观证据 / 瞬间 / 提及 / 事实桥 / 事件桥 / 词法投影。
                connection.execute(
                    f"DELETE FROM person_appearance_evidence WHERE observation_id IN ({obs_slots})",
                    observations,
                )
                for table in (
                    "person_moments",
                    "entity_mentions",
                    "entity_observations",
                    "event_observations",
                    "observation_search_terms",
                ):
                    connection.execute(
                        f"DELETE FROM {table} WHERE observation_id IN ({obs_slots})", observations
                    )
                connection.execute(
                    f"DELETE FROM observation_search_fts WHERE observation_id IN ({obs_slots})",
                    observations,
                )
                for observation_id in observations:
                    connection.execute(
                        "DELETE FROM facts WHERE evidence_ids_json LIKE ?",
                        (f"%{observation_id}%",),
                    )
                # 向量分两处：资产级一行在 memory_vectors（visual/asset）；观察级两行
                # （episodic 与 semantic，source_type='observation'）在 text_vectors。
                # 1.0 那一下 ``metadata_json LIKE`` 不搬：2.0 的 source_id 就是权威键，
                # LIKE 会误伤同名后缀的 id。
                connection.execute(
                    "DELETE FROM memory_vectors WHERE source_type = 'asset' AND source_id = ?",
                    (asset_id,),
                )
                connection.execute(
                    "DELETE FROM text_vectors WHERE source_type = 'observation'"
                    f" AND source_id IN ({obs_slots})",
                    observations,
                )
                connection.execute("DELETE FROM asset_places WHERE asset_id = ?", (asset_id,))
                connection.execute("DELETE FROM observations WHERE asset_id = ?", (asset_id,))
            if scene_event_ids:
                events = tuple(scene_event_ids)
                event_slots = _placeholders(events)
                for table in (
                    "person_moments",
                    "event_participants",
                    "event_entities",
                    "event_observations",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE event_id IN ({event_slots})", events)
                connection.execute(
                    f"DELETE FROM text_vectors WHERE source_type = 'event' AND source_id IN ({event_slots})",
                    events,
                )
                connection.execute(f"DELETE FROM events WHERE id IN ({event_slots})", events)
            connection.execute(
                "DELETE FROM assets WHERE parent_asset_id = ? AND derived_kind IS NOT NULL",
                (parent_asset_id,),
            )
        return len(derived_ids)

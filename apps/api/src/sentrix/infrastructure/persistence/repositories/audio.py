"""音频探测结果的落库。SQL 只出现在这一层。

**为什么与 ``video.py`` 分开、又为什么没有读方法。** 见 ``domain/media/ports.py`` 的
``AudioRepository``：视频那边的读写成对是因为 ``GET /videos/{id}`` 与交付分流要读回
预览件路径；音频没有读侧消费者，**预留一个没有调用者的读方法就是死代码**
（`check_method_reachability.py` 会当场报红）。

**元数据为什么留在 ``metadata_json`` 而不是摊成列。** 迁移 0010 的表头注释定了这条：
每类媒体自己的元数据形状不同（EXIF / 视频 / 音频），摊成列会让列数随媒体类型膨胀，
而音频这几个字段都不参与检索过滤。键名与视频同一套体例（``video_metadata`` /
``video_probe_error`` → ``audio_metadata`` / ``audio_probe_error``）。
"""

from __future__ import annotations

import json

from sentrix.domain.media.models import AudioMetadata
from sentrix.domain.media.ports import AudioProbe
from sentrix.infrastructure.persistence.database import Database

# 写进 ``assets.metadata_json`` 的两个键。1.0 没有对应物（它不读音频元数据），
# 键名照视频那两个的体例起。
_AUDIO_METADATA_KEY = "audio_metadata"
_PROBE_ERROR_KEY = "audio_probe_error"

# AudioMetadata 的字段名与字典键一一对应，构造时按它取；缺键用 dataclass 的默认值，
# 所以老库/手改过的 payload 不会让读侧炸。
_METADATA_FIELDS = (
    "captured_at",
    "latitude",
    "longitude",
    "captured_location",
    "duration_sec",
    "codec",
    "sample_rate",
    "channels",
    "bit_rate",
    "creation_source",
)


def _parse_metadata(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_payload(metadata: AudioMetadata) -> dict[str, object]:
    return {field: getattr(metadata, field) for field in _METADATA_FIELDS}


class SqliteAudioRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_probe(self, asset_id: str, probe: AudioProbe) -> None:
        """**合并写**，不整包覆盖 ``metadata_json``：那里可能已经有别的东西
        （1.0 同一列上还压着反地理、事实 id 等），覆盖会把它们一起抹掉。"""
        with self._database.connect() as connection, connection:
            row = connection.execute(
                "SELECT metadata_json FROM readable_assets WHERE id = ?", (asset_id,)
            ).fetchone()
            if row is None:
                raise KeyError(asset_id)
            payload = _parse_metadata(row["metadata_json"])
            payload[_PROBE_ERROR_KEY] = probe.probe_error
            # metadata 为 None（探测失败）时**不写这个键**：留下一份空元数据会让
            # 「探测过但没读出来」与「读出来是空的」分不开。失败原因在上面那个键里。
            if probe.metadata is not None:
                payload[_AUDIO_METADATA_KEY] = _metadata_payload(probe.metadata)
            connection.execute(
                "UPDATE assets SET metadata_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), asset_id),
            )

"""外观证据仓储。SQL 只出现在这一层。

逐行移植 1.0 ``backend/db.py:4482``（``record_person_appearance_evidence``）、
``:4529``（``get_person_appearance_evidence``）、``:4535``
（``list_person_appearance_evidence``）。

**写入口有三重守卫**（1.0 ``:4485-4497``），每一重挡掉的是不同的一件事：

1. 人存在且 ``status == 'confirmed'``——未确认的候选人物不该有外观记忆；
2. 人脸实例存在；
3. **这个实例真的经 ``entity_mentions`` 确认给了这个人**——前两重都过了、这一重没过，
   意味着「把 A 的衣服记到了 B 头上」，而那是这个功能最要紧的一种错。

唯一键是 ``(person_id, face_instance_id, model_name)``：换一个模型是**另一条**证据，
不覆盖旧模型的结论（见 ``AppearanceEvidenceRecord`` 的 docstring）。

**一处不对称，照实记下不擅自补**：1.0 的 ``list`` 里 JOIN 了 ``assets`` 取
``file_name`` / ``media_type``，而 2.0 的 ``AppearanceEvidenceRecord`` 没有这两个字段，
所以这一层不 JOIN——读回来少两列。消费方（T85d 的取样用例）如果要用它们，
得先给记录加字段。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import AppearanceEvidenceRecord
from sentrix.infrastructure.persistence.database import Database

# 1.0 ``db.py:4535-4546`` 的 ``list_person_appearance_evidence`` **内连 ``assets``** 并选出
# ``a.file_name`` / ``a.media_type``。2.0 早先只搬了主表那几列，是同一段的不完整移植——
# T128b 的 ``evidence_files`` 要用文件名（1.0 的兜底是「取不到就用 asset_id」，
# 少了这两列就永远走兜底，读起来像文件名本来就是 asset_id）。
_COLUMNS = (
    "pae.id, pae.person_id, pae.face_instance_id, pae.observation_id, pae.asset_id,"
    " pae.crop_bbox_json, pae.clothing_json, pae.confidence, pae.model_name, pae.status,"
    " pae.created_at, pae.updated_at, a.file_name, a.media_type"
)
_ASSET_JOIN = " JOIN readable_assets a ON a.id = pae.asset_id"

CONFIRMED = "confirmed"
CONFIRMED_PERSON_STATUS = "confirmed"


def _new_id() -> str:
    """1.0 ``make_id("appearance")``。"""
    return f"appearance_{uuid.uuid4().hex[:12]}"


def _clean_clothing(clothing: Sequence[str] | None) -> list[str]:
    """去空白、丢空串、**去重且保序**（1.0 的 ``dict.fromkeys``）。"""
    values = [str(item or "").strip() for item in (clothing or [])]
    return list(dict.fromkeys(value for value in values if value))


def _parsed_list(raw: object) -> list[object]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


# 拆成两个专用函数而不是一个带 fallback 参数的：联合返回类型在调用处不收敛，
# 得靠 assert 或 cast 才能过 mypy，那等于把类型检查关掉一半。
def _load_numbers(raw: object) -> tuple[float, ...]:
    # JSON 里合法但类型不对的成员（字符串、null、嵌套表）直接丢掉，而不是让 int()/float()
    # 在运行期抛——那会让一条坏数据把整个人的外观证据都读不出来。
    return tuple(float(item) for item in _parsed_list(raw) if isinstance(item, int | float | str))


def _load_strings(raw: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _parsed_list(raw))


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


class SqliteAppearanceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def record(
        self,
        *,
        person_id: str,
        face_instance_id: str,
        crop_bbox: Sequence[float],
        clothing: Sequence[str],
        confidence: float,
        model_name: str,
    ) -> AppearanceEvidenceRecord:
        """记一条外观证据，或覆盖同一 ``(人, 实例, 模型)`` 的那条。"""
        with self._database.connect() as connection:
            person = connection.execute(
                "SELECT status FROM readable_entities WHERE id = ?", (person_id,)
            ).fetchone()
            instance = connection.execute(
                "SELECT observation_id, asset_id FROM readable_face_instances WHERE id = ?",
                (face_instance_id,),
            ).fetchone()
            linked = connection.execute(
                "SELECT 1 FROM readable_entity_mentions WHERE entity_id = ? AND face_instance_id = ?",
                (person_id, face_instance_id),
            ).fetchone()

        # 三重守卫。顺序与 1.0 一致——报错文字指向的是**不同**的原因，
        # 合成一句「参数不合法」会让排查时少了最关键的那条线索。
        if person is None or person["status"] != CONFIRMED_PERSON_STATUS:
            raise ValueError("appearance evidence requires a confirmed person")
        if instance is None:
            raise ValueError("face instance not found")
        if linked is None:
            raise ValueError("face instance is not confirmed for this person")

        values = _clean_clothing(clothing)
        now = datetime.now(UTC).isoformat()
        model = str(model_name or "unknown")
        with self._database.connect() as connection, connection:
            existing = connection.execute(
                "SELECT id FROM readable_person_appearance_evidence"
                " WHERE person_id = ? AND face_instance_id = ? AND model_name = ?",
                (person_id, face_instance_id, model),
            ).fetchone()
            if existing is not None:
                # ``status`` 被**重置**成 confirmed：用户驳回过的证据在重新采到之后
                # 应当重新生效，而不是永远停在驳回态（1.0 原样）。
                connection.execute(
                    "UPDATE person_appearance_evidence SET crop_bbox_json = ?,"
                    " clothing_json = ?, confidence = ?, status = ?, updated_at = ?"
                    " WHERE id = ?",
                    (
                        json.dumps(list(crop_bbox), ensure_ascii=False),
                        json.dumps(values, ensure_ascii=False),
                        float(confidence or 0),
                        CONFIRMED,
                        now,
                        existing["id"],
                    ),
                )
                evidence_id = str(existing["id"])
            else:
                evidence_id = _new_id()
                connection.execute(
                    "INSERT INTO person_appearance_evidence("
                    " id, person_id, face_instance_id, observation_id, asset_id, crop_bbox_json,"
                    " clothing_json, confidence, model_name, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        evidence_id,
                        person_id,
                        face_instance_id,
                        instance["observation_id"],
                        instance["asset_id"],
                        json.dumps(list(crop_bbox), ensure_ascii=False),
                        json.dumps(values, ensure_ascii=False),
                        float(confidence or 0),
                        model,
                        now,
                        now,
                    ),
                )
        created = self.get(evidence_id)
        assert created is not None, "刚写进去的那条证据读不回来"
        return created

    def get(self, evidence_id: str) -> AppearanceEvidenceRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_appearance_evidence pae{_ASSET_JOIN} "
                f"WHERE pae.id = ?",
                (evidence_id,),
            ).fetchone()
        return _record(row)

    def list_for(
        self, person_id: str, *, include_empty: bool = False, limit: int = 500
    ) -> Sequence[AppearanceEvidenceRecord]:
        """这个人的外观证据，按置信度降序。

        ``include_empty=False``（默认）会滤掉 ``clothing`` 为空表的那些——**空表不是
        「没穿衣服」，是「模型没能可靠归属给目标人物」**（见 ``analyze_person_appearance``
        的提示词）。默认把它们排除，是因为它们没有信息量；要统计「模型判不出来的比例」
        时才传 ``include_empty=True``。
        """
        where = "pae.person_id = ? AND pae.status = ?"
        if not include_empty:
            where += " AND pae.clothing_json != '[]'"
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_appearance_evidence pae{_ASSET_JOIN}"
                f" WHERE {where}"
                " ORDER BY pae.confidence DESC, pae.updated_at DESC LIMIT ?",
                (person_id, CONFIRMED, limit),
            ).fetchall()
        return [record for row in rows if (record := _record(row)) is not None]


def _record(row: sqlite3.Row | None) -> AppearanceEvidenceRecord | None:
    if row is None:
        return None
    return AppearanceEvidenceRecord(
        id=row["id"],
        person_id=row["person_id"],
        face_instance_id=row["face_instance_id"],
        observation_id=row["observation_id"],
        asset_id=row["asset_id"],
        crop_bbox=_load_numbers(row["crop_bbox_json"]),
        clothing=_load_strings(row["clothing_json"]),
        confidence=float(row["confidence"]),
        model_name=row["model_name"],
        status=row["status"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
        file_name=row["file_name"],
        media_type=row["media_type"],
    )

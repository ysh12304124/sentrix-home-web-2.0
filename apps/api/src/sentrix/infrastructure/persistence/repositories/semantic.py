"""``semantic_profiles`` / ``semantic_claims`` 的仓储（S6 · T130 的读侧 + S7a · T206 的写侧）。

这两张表从 T127 建起来之后一直没有读者，这里是第一个。列集与 1.0 ``db.py:811-849``
逐字一致（迁移 0008 的注释里写着），所以 ``_COLUMNS`` 按表定义列全，不用 ``SELECT *``：
1.0 用 ``SELECT *`` 是因为它的表是运行期 ``ALTER`` 长出来的，2.0 一次建对。

**写侧只有一条路**（S7a · T206）：``maintain_claim``，1.0 ``db.py:2654`` 的
``maintain_semantic_claim``。那是个**通用**函数，1.0 有六个维度（关系 + 画像的
activity / place / capture / clothing / identity）；2.0 目前只有 ``dimension="relationship"``
一个调用者——画像那五个的写入者 ``rebuild_person_memory`` 在 2.0 没有，
``person_event_memory`` / ``person_patterns`` 两张表也没建。**到达不了的分支只省了一处**：
``dimension == 'clothing'`` 的 ``normalize_clothing`` 归一（那个函数 2.0 没有，而
clothing 只出现在画像那一支）。

**一处 1.0 的名字谎言，照搬不改**：``person_id`` 列在这个域里**存的是实体 id**，
不是「人」——1.0 的 ``get_entity_detail`` 与 ``/api/knowledge`` 都拿非人物实体的 id
去查过它，调用点不判类型。改名会让新老库的行对不上。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import SemanticClaimRecord, SemanticProfileRecord
from sentrix.infrastructure.persistence.database import Database

_PROFILE_COLUMNS = (
    "id, scope_id, person_id, summary_zh, activity_summary_zh, place_summary_zh,"
    " appearance_summary_zh, preference_summary_zh, event_memory_json, patterns_json,"
    " evidence_ids_json, revision, created_at, updated_at"
)
_CLAIM_COLUMNS = (
    "id, scope_id, person_id, dimension, predicate, value_text, normalized_value_text,"
    " normalization_version, support_count, value_entity_id, valid_from, valid_to,"
    " supporting_event_ids_json, evidence_ids_json, status, confidence, confidence_source,"
    " supersedes_claim_id, revision, created_at, updated_at"
)

# 1.0 ``db.py:2711`` 的状态白名单。它是 ``include_history=False``（默认）那一支——
# 也就是唯一被 2.0 用到的取值。
ACTIVE_CLAIM_STATUSES = ("active", "pending")

# 1.0 ``db.py:2655-2659`` 的九条谓词别名。**未命中取 strip 后的原值**，所以它是个
# 「改写」而不是白名单——关系那条路会经过它（用户 POST 上来的谓词是自由文本）。
PREDICATE_ALIASES = {
    "参加过": "参与",
    "参加": "参与",
    "出席": "参与",
    "出现于": "出现在",
    "位于": "出现在",
    "穿着": "穿着",
    "拍摄了": "拍摄",
    "拍照": "拍摄",
    "家庭成员角色": "家庭角色",
}

# 1.0 ``db.py:2689`` 的多值维度。它们在**没有**同键 active 行时也直接落 active；
# 其余维度（含 ``relationship``）要先让位——落 pending 并记 supersedes。
MULTI_VALUED_DIMENSIONS = {"activity", "place", "clothing", "capture", "attendance"}

# 1.0 ``maintain_semantic_claim`` 的默认值（``db.py:2654``）。
DEFAULT_CLAIM_CONFIDENCE = 0.5
DERIVED_SOURCE = "derived"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_claim_id() -> str:
    """1.0 ``make_id("claim")``。"""
    return f"claim_{uuid.uuid4().hex[:12]}"


def _dump(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _json_list(raw: object) -> list[object]:
    """解一个 ``*_json`` 列。1.0 的 ``_decode`` 解不开时回落空表，这里一样。"""
    try:
        value = json.loads(str(raw) if raw is not None else "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _strings(raw: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _json_list(raw))


def _row_to_profile(row: sqlite3.Row) -> SemanticProfileRecord:
    return SemanticProfileRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        person_id=row["person_id"],
        summary_zh=row["summary_zh"],
        activity_summary_zh=row["activity_summary_zh"],
        place_summary_zh=row["place_summary_zh"],
        appearance_summary_zh=row["appearance_summary_zh"],
        preference_summary_zh=row["preference_summary_zh"],
        event_memory=tuple(_json_list(row["event_memory_json"])),
        patterns=tuple(_json_list(row["patterns_json"])),
        evidence_ids=_strings(row["evidence_ids_json"]),
        revision=row["revision"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


def _row_to_claim(row: sqlite3.Row) -> SemanticClaimRecord:
    return SemanticClaimRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        person_id=row["person_id"],
        dimension=row["dimension"],
        predicate=row["predicate"],
        value_text=row["value_text"],
        normalized_value_text=row["normalized_value_text"],
        normalization_version=row["normalization_version"],
        support_count=row["support_count"],
        value_entity_id=row["value_entity_id"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        supporting_event_ids=_strings(row["supporting_event_ids_json"]),
        evidence_ids=_strings(row["evidence_ids_json"]),
        status=row["status"],
        confidence=row["confidence"],
        confidence_source=row["confidence_source"],
        supersedes_claim_id=row["supersedes_claim_id"],
        revision=row["revision"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


class SqliteSemanticRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_profile(self, person_id: str) -> SemanticProfileRecord | None:
        """1.0 ``db.py:2614-2616``。``person_id`` 在库里是 UNIQUE，所以至多一行。"""
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_PROFILE_COLUMNS} FROM readable_semantic_profiles WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return _row_to_profile(row) if row else None

    def list_claims(self, person_id: str | None = None, *, limit: int = 500) -> Sequence[SemanticClaimRecord]:
        """1.0 ``db.py:2710-2716``。**恒带状态白名单**，排序 ``updated_at DESC``。

        两个分支的 SQL 形状与 1.0 一一对应：给了 ``person_id`` 按它过滤，没给就是全表
        （1.0 那个分支的 ``WHERE 1 = 1`` 只是为了让状态子句能直接拼上去）。
        """
        marks = ",".join("?" * len(ACTIVE_CLAIM_STATUSES))
        with self._database.connect() as connection:
            if person_id:
                rows = connection.execute(
                    f"SELECT {_CLAIM_COLUMNS} FROM readable_semantic_claims"
                    f" WHERE person_id = ? AND status IN ({marks})"
                    " ORDER BY updated_at DESC LIMIT ?",
                    (person_id, *ACTIVE_CLAIM_STATUSES, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_CLAIM_COLUMNS} FROM readable_semantic_claims"
                    f" WHERE status IN ({marks})"
                    " ORDER BY updated_at DESC LIMIT ?",
                    (*ACTIVE_CLAIM_STATUSES, limit),
                ).fetchall()
        return [_row_to_claim(row) for row in rows]

    def maintain_claim(
        self,
        *,
        person_id: str,
        scope_id: str,
        dimension: str,
        predicate: str,
        value_text: str,
        evidence_ids: Sequence[str] = (),
        event_ids: Sequence[str] = (),
        confidence: float = DEFAULT_CLAIM_CONFIDENCE,
        confidence_source: str = DERIVED_SOURCE,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> SemanticClaimRecord:
        """1.0 ``db.py:2654-2710`` 的 ``maintain_semantic_claim``，逐段搬。

        两处 1.0 的 SQL 写得**不直观但有用**：``COALESCE(normalized_value_text,
        value_text) = ?`` 让「归一过的」与「还没归一的」老行能被同一个键找到；
        ``ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, revision DESC`` 让同键
        的 active 行优先于 pending 行被并掉。
        """
        normalized_predicate = PREDICATE_ALIASES.get(str(predicate).strip(), str(predicate).strip())
        merged_evidence = list(dict.fromkeys(evidence_ids))
        merged_events = list(dict.fromkeys(event_ids))
        # 1.0 在这里还要判 ``dimension == "clothing"`` 走 ``normalize_clothing``——
        # 那个函数 2.0 没有，而 clothing 只出现在画像那一支（没有调用者）。见模块 docstring。
        normalized_value = str(value_text or "").strip()
        timestamp = _now()
        with self._database.connect() as connection, connection:
            matching = connection.execute(
                f"SELECT {_CLAIM_COLUMNS} FROM readable_semantic_claims"
                " WHERE person_id = ? AND dimension = ? AND predicate = ?"
                " AND COALESCE(normalized_value_text, value_text) = ?"
                f" AND status IN ({','.join('?' * len(ACTIVE_CLAIM_STATUSES))})"
                " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, revision DESC LIMIT 1",
                (person_id, dimension, normalized_predicate, normalized_value, *ACTIVE_CLAIM_STATUSES),
            ).fetchone()
            if matching is not None:
                kept_evidence = list(
                    dict.fromkeys([*_strings(matching["evidence_ids_json"]), *merged_evidence])
                )
                kept_events = list(
                    dict.fromkeys([*_strings(matching["supporting_event_ids_json"]), *merged_events])
                )
                connection.execute(
                    "UPDATE semantic_claims SET evidence_ids_json = ?,"
                    " supporting_event_ids_json = ?, confidence = MAX(confidence, ?),"
                    " confidence_source = ?, normalized_value_text = ?, support_count = ?,"
                    " valid_from = COALESCE(valid_from, ?),"
                    " valid_to = CASE WHEN valid_to IS NULL THEN ? ELSE valid_to END,"
                    " updated_at = ? WHERE id = ?",
                    (
                        _dump(kept_evidence),
                        _dump(kept_events),
                        float(confidence or 0),
                        confidence_source,
                        normalized_value,
                        max(int(matching["support_count"] or 1), len(kept_events) or 1),
                        valid_from,
                        valid_to,
                        timestamp,
                        matching["id"],
                    ),
                )
                return _row_to_claim(self._claim(connection, matching["id"]))
            active = connection.execute(
                "SELECT id, revision FROM readable_semantic_claims"
                " WHERE person_id = ? AND dimension = ? AND predicate = ? AND status = 'active'"
                " ORDER BY revision DESC LIMIT 1",
                (person_id, dimension, normalized_predicate),
            ).fetchone()
            status = "active" if dimension in MULTI_VALUED_DIMENSIONS or active is None else "pending"
            claim_id = _new_claim_id()
            connection.execute(
                "INSERT INTO semantic_claims("
                " id, scope_id, person_id, dimension, predicate, value_text,"
                " normalized_value_text, valid_from, valid_to, supporting_event_ids_json,"
                " evidence_ids_json, status, confidence, confidence_source, support_count,"
                " supersedes_claim_id, revision, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    claim_id,
                    scope_id,
                    person_id,
                    dimension,
                    normalized_predicate,
                    value_text,
                    normalized_value,
                    valid_from,
                    valid_to,
                    _dump(merged_events),
                    _dump(merged_evidence),
                    status,
                    float(confidence or 0),
                    confidence_source,
                    max(1, len(merged_events)),
                    active["id"] if active is not None and status == "pending" else None,
                    (int(active["revision"]) + 1) if active is not None and status == "pending" else 1,
                    timestamp,
                    timestamp,
                ),
            )
            return _row_to_claim(self._claim(connection, claim_id))

    def _claim(self, connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
        """1.0 ``get_semantic_claim``：按 id 取回刚写/刚并的那一行。**不筛 status。**"""
        row: sqlite3.Row | None = connection.execute(
            f"SELECT {_CLAIM_COLUMNS} FROM readable_semantic_claims WHERE id = ?", (claim_id,)
        ).fetchone()
        if row is None:  # 刚写完就读不到只会是磁盘坏了，但别让类型变成 Optional
            raise LookupError(f"semantic claim disappeared: {claim_id}")
        return row

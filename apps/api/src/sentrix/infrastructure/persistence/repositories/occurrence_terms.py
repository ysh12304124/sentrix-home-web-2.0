"""人物出现词法投影：派生表写入与读取。

对应 v3 §4.1：只索引当前有效描述、稳定标签/ID 和获准使用的姓名；不建 per-face
文本向量，也不碰旧 ``person_bridge``。投影是派生数据，读取时还要按当前人物/身份/
描述状态复核，异步清理晚到不会把无效绑定继续交出去。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.retrieval.lexical import fts_content, normalize_text, query_tokens
from sentrix.infrastructure.persistence.database import Database

FIELD_DESCRIPTION = "face_description"
FIELD_LABEL = "face_label"
FIELD_FACE_ID = "face_id"
FIELD_PERSON_NAME = "person_name"

_VERIFIED_STATES = ("user_verified", "auto_trusted")

_FACE_SQL = """
    SELECT fi.id AS face_instance_id, fi.asset_id, fi.observation_id, fi.cluster_id,
           fi.identity_state, a.scope_id, fc.entity_id,
           e.canonical_name, e.status AS entity_status, e.name_state
    FROM face_instances fi
    JOIN assets a ON a.id = fi.asset_id
    LEFT JOIN face_clusters fc ON fc.id = fi.cluster_id
    LEFT JOIN entities e ON e.id = fc.entity_id
    WHERE fi.id = ?
"""

_APPEARANCE_SQL = """
    SELECT pae.id, pae.description_text, pae.source_revision
    FROM person_appearance_evidence pae
    WHERE pae.face_instance_id = ? AND pae.description_state = 'effective'
      AND pae.description_text != ''
    ORDER BY pae.source_revision DESC, pae.updated_at DESC, pae.id DESC LIMIT 1
"""

_EVIDENCE_SQL = """
    SELECT me.id, me.value_json, me.observation_revision
    FROM memory_evidence me
    WHERE me.owner_kind = 'face_instance' AND me.owner_id = ?
      AND me.field_path = 'face.description' AND me.status = 'effective'
    ORDER BY me.observation_revision DESC, me.created_at DESC, me.id DESC LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class OccurrenceTermHit:
    face_instance_id: str
    matched_tokens: frozenset[str]
    # (field_type, normalized_value, evidence_id, source_revision)
    terms: tuple[tuple[str, str, str, int], ...]
    exact: bool = False

    @property
    def score(self) -> float:
        return float(len(self.matched_tokens)) + (2.0 if self.exact else 0.0)


@dataclass(frozen=True, slots=True)
class OccurrenceSearchHits:
    by_face: dict[str, OccurrenceTermHit]
    ranked_face_ids: tuple[str, ...]


def _new_id() -> str:
    return f"pot_{uuid.uuid4().hex[:16]}"


def _display(row: sqlite3.Row, face_instance_id: str) -> tuple[str, str, str]:
    """返回 ``(label, verified_name, person_id)``。未验证时姓名绝不进入投影。"""
    person_id = str(row["entity_id"] or "")
    verified_name = ""
    if (
        str(row["entity_status"] or "") == "confirmed"
        and str(row["name_state"] or "") == "confirmed"
        and str(row["identity_state"] or "") in _VERIFIED_STATES
    ):
        verified_name = str(row["canonical_name"] or "").strip()
    if verified_name:
        label = verified_name
    elif person_id:
        label = f"人物…{person_id[-8:]}"
    else:
        label = f"本图…{face_instance_id[-8:]}"
    return label, verified_name, person_id


def _description(row_connection: sqlite3.Connection, face_instance_id: str) -> tuple[str, str, int]:
    appearance = row_connection.execute(_APPEARANCE_SQL, (face_instance_id,)).fetchone()
    if appearance is not None:
        return (
            str(appearance["description_text"] or "").strip(),
            str(appearance["id"]),
            int(appearance["source_revision"] or 0),
        )
    evidence = row_connection.execute(_EVIDENCE_SQL, (face_instance_id,)).fetchone()
    if evidence is not None:
        try:
            payload = json.loads(str(evidence["value_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        description = str(payload.get("description") or "").strip() if isinstance(payload, dict) else ""
        return description, str(evidence["id"]), int(evidence["observation_revision"] or 0)
    return "", "", 0


def refresh_occurrence_terms(connection: sqlite3.Connection, face_instance_id: str) -> int:
    """重写一个 face_instance 的投影。必须在写描述/归属的同一事务里调用。"""
    connection.execute(
        "DELETE FROM person_occurrence_search_terms WHERE face_instance_id = ?",
        (face_instance_id,),
    )
    connection.execute(
        "DELETE FROM person_occurrence_search_fts WHERE face_instance_id = ?",
        (face_instance_id,),
    )
    row = connection.execute(_FACE_SQL, (face_instance_id,)).fetchone()
    if row is None or str(row["identity_state"] or "") == "rejected":
        return 0

    scope_id = str(row["scope_id"])
    asset_id = str(row["asset_id"])
    observation_id = str(row["observation_id"])
    person_id = str(row["entity_id"] or "")
    label, verified_name, _ = _display(row, face_instance_id)
    description, evidence_id, revision = _description(connection, face_instance_id)
    now = datetime.now(UTC).isoformat()

    candidates: list[tuple[str, str, str, int]] = []
    if description:
        candidates.append((FIELD_DESCRIPTION, description, evidence_id, revision))
    candidates.append((FIELD_LABEL, label, "", revision))
    candidates.append((FIELD_FACE_ID, face_instance_id, "", revision))
    if verified_name:
        candidates.append((FIELD_PERSON_NAME, verified_name, "", revision))

    written = 0
    seen: set[tuple[str, str, str]] = set()
    for field_type, raw_value, ref, source_revision in candidates:
        value = normalize_text(raw_value)
        key = (field_type, value, ref)
        if not value or key in seen:
            continue
        seen.add(key)
        term_id = _new_id()
        connection.execute(
            "INSERT INTO person_occurrence_search_terms("
            " id, scope_id, face_instance_id, asset_id, observation_id, person_id,"
            " field_type, normalized_value, evidence_id, source_revision, created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                term_id,
                scope_id,
                face_instance_id,
                asset_id,
                observation_id,
                person_id,
                field_type,
                value,
                ref,
                int(source_revision),
                now,
                now,
            ),
        )
        tokens = fts_content(value)
        if tokens:
            connection.execute(
                "INSERT INTO person_occurrence_search_fts("
                " tokens, term_id, scope_id, field_type, face_instance_id, asset_id,"
                " person_id, normalized_value, evidence_id, source_revision"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    tokens,
                    term_id,
                    scope_id,
                    field_type,
                    face_instance_id,
                    asset_id,
                    person_id,
                    value,
                    ref,
                    int(source_revision),
                ),
            )
        written += 1
    return written


def _collect(
    rows: Iterable[sqlite3.Row],
    *,
    token: str,
    matched: dict[str, set[str]],
    terms: dict[str, list[tuple[str, str, str, int]]],
) -> None:
    for row in rows:
        face_id = str(row["face_instance_id"])
        matched.setdefault(face_id, set()).add(token)
        terms.setdefault(face_id, []).append(
            (
                str(row["field_type"]),
                str(row["normalized_value"]),
                str(row["evidence_id"] or ""),
                int(row["source_revision"] or 0),
            )
        )


def search_occurrence_terms(
    connection: sqlite3.Connection,
    query: str,
    *,
    scope_id: str = "",
    limit_faces: int = 2000,
) -> OccurrenceSearchHits:
    """按描述/标签/ID/验证姓名找 face。无效命中的复核在读取仓储里做。

    ``scope_id`` 为空是「全部相册」，按 ``include_in_people`` 排除评测空间——与
    ``observation_search_terms.assets_for_person`` 同一条口径，不给跨 scope 泄漏后门。
    """
    tokens = query_tokens(query)
    matched: dict[str, set[str]] = {}
    terms: dict[str, list[tuple[str, str, str, int]]] = {}
    exact_faces: set[str] = set()
    scope_clause = (
        " AND scope_id = ?"
        if scope_id
        else (" AND scope_id NOT IN (SELECT id FROM memory_spaces WHERE include_in_people = 0)")
    )
    scope_params: tuple[object, ...] = (scope_id,) if scope_id else ()
    for token in tokens:
        try:
            rows = connection.execute(
                "SELECT face_instance_id, field_type, normalized_value, evidence_id, source_revision"
                " FROM person_occurrence_search_fts"
                " WHERE person_occurrence_search_fts MATCH ?" + scope_clause,
                (f'"{token}"', *scope_params),
            ).fetchall()
        except Exception:
            continue
        _collect(rows, token=token, matched=matched, terms=terms)

    whole = normalize_text(query)
    if whole:
        rows = connection.execute(
            "SELECT face_instance_id, field_type, normalized_value, evidence_id, source_revision"
            " FROM person_occurrence_search_terms"
            " WHERE normalized_value = ?" + scope_clause,
            (whole, *scope_params),
        ).fetchall()
        _collect(rows, token=whole, matched=matched, terms=terms)
        exact_faces.update(str(row["face_instance_id"]) for row in rows)

    by_face = {
        face_id: OccurrenceTermHit(
            face_instance_id=face_id,
            matched_tokens=frozenset(matched[face_id]),
            terms=tuple(terms[face_id]),
            exact=face_id in exact_faces,
        )
        for face_id in matched
    }
    ranked = tuple(
        sorted(by_face, key=lambda face_id: (-by_face[face_id].score, face_id))[: max(1, limit_faces)]
    )
    return OccurrenceSearchHits(by_face=by_face, ranked_face_ids=ranked)


def occurrence_descriptions(connection: sqlite3.Connection, observation_id: str) -> tuple[str, ...]:
    """一张图当前有效的脸级描述，按出现顺序去重；只用于观察语义文本组合。"""
    values: list[str] = []
    rows = connection.execute(
        "SELECT pae.description_text AS description FROM person_appearance_evidence pae"
        " WHERE pae.observation_id = ? AND pae.description_state = 'effective'"
        "   AND pae.description_text != ''"
        " ORDER BY pae.source_revision DESC, pae.updated_at DESC, pae.id DESC",
        (observation_id,),
    ).fetchall()
    values.extend(str(row["description"] or "").strip() for row in rows)
    evidence = connection.execute(
        "SELECT me.value_json FROM memory_evidence me"
        " JOIN face_instances fi ON fi.id = me.owner_id"
        " WHERE me.owner_kind = 'face_instance' AND me.field_path = 'face.description'"
        "   AND me.status = 'effective' AND fi.observation_id = ?"
        " ORDER BY me.observation_revision DESC, me.created_at DESC, me.id DESC",
        (observation_id,),
    ).fetchall()
    for row in evidence:
        try:
            payload = json.loads(str(row["value_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            values.append(str(payload.get("description") or "").strip())
    return tuple(dict.fromkeys(value for value in values if value))


def validate_hit(
    hit: OccurrenceTermHit,
    *,
    description: str,
    display_label: str,
    verified_name: str,
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """按当前投影复核；返回 ``(matched_fields, evidence_refs, source_revision)``。"""
    current = {
        FIELD_DESCRIPTION: normalize_text(description),
        FIELD_LABEL: normalize_text(display_label),
        FIELD_FACE_ID: normalize_text(hit.face_instance_id),
        FIELD_PERSON_NAME: normalize_text(verified_name),
    }
    fields: set[str] = set()
    refs: set[str] = set()
    revision = 0
    for field_type, value, evidence_id, source_revision in hit.terms:
        current_value = current.get(field_type)
        if not current_value:
            continue
        if field_type == FIELD_DESCRIPTION:
            if value not in current_value:
                continue
        elif value != current_value:
            continue
        fields.add(field_type)
        if evidence_id:
            refs.add(evidence_id)
        revision = max(revision, source_revision)
    return tuple(sorted(fields)), tuple(sorted(refs)), revision


class SqlitePersonOccurrenceIndex:
    """写侧/重建入口；读写都走同一套投影定义。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    def refresh(self, face_instance_id: str) -> int:
        with self._database.connect() as connection, connection:
            return refresh_occurrence_terms(connection, face_instance_id)

    def rebuild(self, scope_id: str | None = None) -> int:
        with self._database.connect() as connection, connection:
            if scope_id:
                rows = connection.execute(
                    "SELECT fi.id AS id FROM face_instances fi JOIN assets a ON a.id = fi.asset_id"
                    " WHERE a.scope_id = ? ORDER BY fi.id",
                    (scope_id,),
                ).fetchall()
            else:
                rows = connection.execute("SELECT id FROM face_instances ORDER BY id").fetchall()
            return sum(refresh_occurrence_terms(connection, str(row["id"])) for row in rows)

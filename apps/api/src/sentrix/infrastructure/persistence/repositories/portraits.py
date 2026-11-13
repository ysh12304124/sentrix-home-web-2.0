"""画像修订仓储。SQL 只出现在这一层。

逐行移植 1.0 ``backend/db.py:6366-6440``。**``create`` 里的 ``user_locked`` 短路是
这个仓储最重要的一行**：用户在画像上唯一的否决权落在写入口，而不是界面上的禁用按钮
——界面上禁用了，导入后的自动触发照样会写进去（见 ``PortraitRepository`` 的 docstring）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import (
    PORTRAIT_STATUS_ACTIVE,
    PORTRAIT_STATUS_SUPERSEDED,
    PORTRAIT_STATUS_USER_LOCKED,
    PORTRAIT_TRIGGER_MANUAL,
    PortraitRevisionRecord,
)
from sentrix.infrastructure.persistence.database import Database

# 1.0 ``_portrait_row`` 的两列 JSON。空串与 NULL 都当空表——1.0 用 ``or "[]"`` 兜住。
_COLUMNS = (
    "id, scope_id, person_id, revision, portrait_text, themes_json, evidence_refs_json,"
    " trigger_type, model_name, prompt_version, status, user_feedback, created_at, updated_at"
)


def _new_id(prefix: str) -> str:
    """1.0 ``make_id``：``f"{prefix}_{uuid4().hex[:12]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_list(value: Sequence[dict[str, object]] | None) -> str:
    return json.dumps(list(value or ()), ensure_ascii=False)


def _load_list(raw: object) -> tuple[dict[str, object], ...]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return ()
    return tuple(item for item in parsed if isinstance(item, dict))


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


class SqlitePortraitRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---- 读 ----------------------------------------------------------------

    def get(self, revision_id: str) -> PortraitRevisionRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_portrait_revisions WHERE id = ?", (revision_id,)
            ).fetchone()
        return _record(row)

    def get_active(self, person_id: str) -> PortraitRevisionRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_portrait_revisions"
                " WHERE person_id = ? AND status IN (?, ?) ORDER BY revision DESC LIMIT 1",
                (person_id, PORTRAIT_STATUS_ACTIVE, PORTRAIT_STATUS_USER_LOCKED),
            ).fetchone()
        return _record(row)

    def list_for_person(self, person_id: str) -> Sequence[PortraitRevisionRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM readable_person_portrait_revisions"
                " WHERE person_id = ? ORDER BY revision DESC",
                (person_id,),
            ).fetchall()
        return [record for row in rows if (record := _record(row)) is not None]

    # ---- 写 ----------------------------------------------------------------

    def create(
        self,
        person_id: str,
        *,
        portrait_text: str,
        prompt_version: str,
        themes: Sequence[dict[str, object]] = (),
        evidence_refs: Sequence[dict[str, object]] = (),
        trigger_type: str = PORTRAIT_TRIGGER_MANUAL,
        model_name: str = "unknown",
    ) -> PortraitRevisionRecord:
        current = self.get_active(person_id)
        # **否决权在这一行。** 用户锁住的版本不被顶掉，也不产生新版本。
        if current is not None and current.status == PORTRAIT_STATUS_USER_LOCKED:
            return current

        with self._database.connect() as check:
            if check.execute(
                "SELECT 1 FROM person_portrait_revisions WHERE person_id=? AND status='user_locked'",
                (person_id,),
            ).fetchone():
                from sentrix.domain.media.deletion import MediaIsolated

                raise MediaIsolated("locked invalid portrait requires explicit revalidation")
            revision = check.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM person_portrait_revisions WHERE person_id=?",
                (person_id,),
            ).fetchone()[0]
        now = datetime.now(UTC).isoformat()
        revision_id = _new_id("portrait")
        with self._database.connect() as connection, connection:
            if current is not None:
                connection.execute(
                    "UPDATE person_portrait_revisions SET status = ?, updated_at = ? WHERE id = ?",
                    (PORTRAIT_STATUS_SUPERSEDED, now, current.id),
                )
            connection.execute(
                "INSERT INTO person_portrait_revisions("
                " id, scope_id, person_id, revision, portrait_text, themes_json,"
                " evidence_refs_json, trigger_type, model_name, prompt_version, status,"
                " created_at, updated_at"
                ") VALUES (?, (SELECT scope_id FROM readable_entities WHERE id = ?), ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision_id,
                    person_id,
                    person_id,
                    revision,
                    portrait_text,
                    _json_list(themes),
                    _json_list(evidence_refs),
                    trigger_type,
                    model_name,
                    prompt_version,
                    PORTRAIT_STATUS_ACTIVE,
                    now,
                    now,
                ),
            )
        created = self.get(revision_id)
        assert created is not None, "刚写进去的那一版读不回来"
        return created

    def set_feedback(self, revision_id: str, feedback: str | None) -> PortraitRevisionRecord | None:
        current = self.get(revision_id)
        if current is None:
            return None
        with (
            self._database.model_generation(current.scope_id),
            self._database.connect() as connection,
            connection,
        ):
            connection.execute(
                "UPDATE person_portrait_revisions SET user_feedback = ?, updated_at = ? WHERE id = ?",
                (feedback, datetime.now(UTC).isoformat(), revision_id),
            )
        return self.get(revision_id)

    def set_lock(self, revision_id: str, locked: bool) -> PortraitRevisionRecord | None:
        status = PORTRAIT_STATUS_USER_LOCKED if locked else PORTRAIT_STATUS_ACTIVE
        current = self.get(revision_id)
        if current is None:
            return None
        with (
            self._database.model_generation(current.scope_id),
            self._database.connect() as connection,
            connection,
        ):
            connection.execute(
                "UPDATE person_portrait_revisions SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(UTC).isoformat(), revision_id),
            )
        return self.get(revision_id)


def _record(row: sqlite3.Row | None) -> PortraitRevisionRecord | None:
    if row is None:
        return None
    return PortraitRevisionRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        person_id=row["person_id"],
        revision=int(row["revision"]),
        portrait_text=row["portrait_text"],
        themes=_load_list(row["themes_json"]),
        evidence_refs=_load_list(row["evidence_refs_json"]),
        trigger_type=row["trigger_type"],
        model_name=row["model_name"],
        prompt_version=row["prompt_version"],
        status=row["status"],
        user_feedback=row["user_feedback"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )

"""回合与回合进度事件的 SQLite 仓储。

同回合恢复（任务 6）：认领使用到期租约与围栏号；事件写进单调序号的 v2 表，
旧尝试的进度不会被新尝试覆盖；最终助手消息与回合终态在同一事务里提交。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sentrix.domain.assistant.models import (
    Attachment,
    ProgressEvent,
    ProgressStage,
    ProgressStatus,
    TurnIdempotencyConflict,
    TurnRecord,
    TurnStatus,
)
from sentrix.infrastructure.persistence.database import Database

_TURN_COLUMNS = (
    "id, conversation_id, scope_id, question, status, termination_reason, answer,"
    " attachments_json, requirements_json, candidates_json, trace_json, created_at, updated_at,"
    " idempotency_key, execution_state, attempt_no, lease_expires_at, fence,"
    " waiting_reason, next_retry_at, cancel_requested"
)


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_turn(row: sqlite3.Row) -> TurnRecord:
    attachments = json.loads(row["attachments_json"] or "[]")
    return TurnRecord(
        id=row["id"],
        conversation_id=row["conversation_id"],
        scope_id=row["scope_id"],
        question=row["question"],
        status=TurnStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        answer=row["answer"],
        termination_reason=row["termination_reason"],
        attachments=tuple(
            Attachment(handle=item["handle"], asset_id=item["asset_id"], file_name=item["file_name"])
            for item in attachments
        ),
        requirements=tuple(json.loads(row["requirements_json"] or "[]")),
        retrieved_candidates=tuple(json.loads(row["candidates_json"] or "[]")),
        trace=dict(json.loads(row["trace_json"] or "{}")),
        idempotency_key=str(row["idempotency_key"] or ""),
        execution_state=str(row["execution_state"] or ""),
        attempt_no=int(row["attempt_no"] or 0),
        lease_expires_at=_parse(row["lease_expires_at"]),
        fence=int(row["fence"] or 0),
        waiting_reason=str(row["waiting_reason"] or ""),
        next_retry_at=_parse(row["next_retry_at"]),
        cancel_requested=bool(row["cancel_requested"]),
    )


def _event_to_json(event: ProgressEvent) -> str:
    return json.dumps(
        {
            "step_index": event.step_index,
            "stage": event.stage.value,
            "status": event.status.value,
            "text": event.text,
            "kind": event.kind,
            "timestamp": event.timestamp,
            "payload": dict(event.payload),
        },
        ensure_ascii=False,
    )


def _json_to_event(payload: str) -> ProgressEvent:
    data: Mapping[str, Any] = json.loads(payload)
    return ProgressEvent(
        step_index=int(data["step_index"]),
        stage=ProgressStage(data["stage"]),
        status=ProgressStatus(data["status"]),
        text=str(data["text"]),
        kind=str(data.get("kind") or "status"),
        timestamp=str(data.get("timestamp") or ""),
        payload=data.get("payload") or {},
    )


class SqliteTurnRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, record: TurnRecord) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO agent_turns"
                " (id, conversation_id, scope_id, question, status, termination_reason, answer,"
                " attachments_json, requirements_json, trace_json, created_at, updated_at,"
                " idempotency_key, execution_state, attempt_no, fence)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.conversation_id,
                    record.scope_id,
                    record.question,
                    record.status.value,
                    record.termination_reason,
                    record.answer,
                    "[]",
                    "[]",
                    json.dumps(dict(record.trace), ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.idempotency_key,
                    record.execution_state,
                    record.attempt_no,
                    record.fence,
                ),
            )

    def create_with_user_message(
        self, record: TurnRecord, *, message_id: str
    ) -> tuple[TurnRecord, bool]:
        """用户消息与回合同一事务落库；同键同文返回已有回合。"""
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if record.idempotency_key:
                    existing = connection.execute(
                        f"SELECT {_TURN_COLUMNS} FROM agent_turns"
                        " WHERE conversation_id = ? AND idempotency_key = ?",
                        (record.conversation_id, record.idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        connection.commit()
                        if str(existing["question"]) != record.question:
                            raise TurnIdempotencyConflict(
                                "idempotency key reused for a different question"
                            )
                        return _row_to_turn(existing), False
                connection.execute(
                    "INSERT INTO agent_turns"
                    " (id, conversation_id, scope_id, question, status, termination_reason, answer,"
                    " attachments_json, requirements_json, trace_json, created_at, updated_at,"
                    " idempotency_key)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.conversation_id,
                        record.scope_id,
                        record.question,
                        record.status.value,
                        record.termination_reason,
                        record.answer,
                        "[]",
                        "[]",
                        json.dumps(dict(record.trace), ensure_ascii=False),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                        record.idempotency_key,
                    ),
                )
                connection.execute(
                    "INSERT INTO agent_conversation_messages"
                    " (id, conversation_id, scope_id, turn_id, role, content, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        message_id,
                        record.conversation_id,
                        record.scope_id,
                        record.id,
                        "user",
                        record.question,
                        record.created_at.isoformat(),
                    ),
                )
                connection.execute(
                    "UPDATE agent_conversations SET last_message_at = ?, updated_at = ? WHERE id = ?",
                    (
                        record.created_at.isoformat(),
                        record.created_at.isoformat(),
                        record.conversation_id,
                    ),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return record, True

    def get(self, turn_id: str) -> TurnRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM agent_turns WHERE id = ?", (turn_id,)
            ).fetchone()
        return _row_to_turn(row) if row else None

    def claim(self, turn_id: str, updated_at: datetime) -> bool:
        """兼容旧入口：pending → running。"""
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_turns SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (
                    TurnStatus.RUNNING.value,
                    updated_at.isoformat(),
                    turn_id,
                    TurnStatus.PENDING.value,
                ),
            )
        return cursor.rowcount > 0

    def claim_due(self, *, now: datetime, lease_s: int) -> TurnRecord | None:
        """认领 pending 或租约过期的 running 回合，尝试号与围栏号各自加一。"""
        stamp = now.isoformat()
        expires = (now + timedelta(seconds=lease_s)).isoformat()
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    f"SELECT {_TURN_COLUMNS} FROM agent_turns WHERE "
                    "(status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= ?)) "
                    "OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?) "
                    "ORDER BY created_at ASC, rowid ASC LIMIT 1",
                    (stamp, stamp),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    "UPDATE agent_turns SET status = 'running', attempt_no = attempt_no + 1,"
                    " fence = fence + 1, lease_expires_at = ?, execution_state = 'recovering',"
                    " updated_at = ? WHERE id = ?",
                    (expires, stamp, row["id"]),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return self.get(str(row["id"]))

    def heartbeat(self, turn_id: str, fence: int, *, now: datetime, lease_s: int) -> bool:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_turns SET lease_expires_at = ?, updated_at = ?"
                " WHERE id = ? AND fence = ? AND status = 'running'",
                ((now + timedelta(seconds=lease_s)).isoformat(), now.isoformat(), turn_id, fence),
            )
        return cursor.rowcount > 0

    def set_execution_state(
        self,
        turn_id: str,
        fence: int,
        *,
        now: datetime,
        execution_state: str,
        waiting_reason: str = "",
        next_retry_at: datetime | None = None,
        pending: bool = False,
    ) -> bool:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_turns SET execution_state = ?, waiting_reason = ?,"
                " next_retry_at = ?, lease_expires_at = NULL,"
                " status = CASE WHEN ? THEN 'pending' ELSE status END, updated_at = ?"
                " WHERE id = ? AND fence = ? AND status IN ('pending', 'running')",
                (
                    execution_state,
                    waiting_reason,
                    next_retry_at.isoformat() if next_retry_at else None,
                    1 if pending else 0,
                    now.isoformat(),
                    turn_id,
                    fence,
                ),
            )
        return cursor.rowcount > 0

    def request_cancel(self, turn_id: str, *, now: datetime) -> bool:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_turns SET cancel_requested = 1, updated_at = ?"
                " WHERE id = ? AND status IN ('pending', 'running')",
                (now.isoformat(), turn_id),
            )
        return cursor.rowcount > 0

    def append_event(
        self,
        turn_id: str,
        event: ProgressEvent,
        *,
        fence: int,
        attempt_no: int,
        now: datetime,
    ) -> int | None:
        """在回合内单调分配事件序号；旧尝试的围栏不匹配时拒绝写入。"""
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                owner = connection.execute(
                    "SELECT fence FROM agent_turns WHERE id = ?", (turn_id,)
                ).fetchone()
                if owner is None or int(owner["fence"]) != fence:
                    connection.rollback()
                    return None
                row = connection.execute(
                    "SELECT COALESCE(MAX(event_seq), 0) + 1 AS n"
                    " FROM agent_turn_events_v2 WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()
                seq = int(row["n"])
                payload = _event_to_json(
                    ProgressEvent(
                        step_index=seq,
                        stage=event.stage,
                        status=event.status,
                        text=event.text,
                        kind=event.kind,
                        timestamp=event.timestamp,
                        payload=event.payload,
                    )
                )
                connection.execute(
                    "INSERT INTO agent_turn_events_v2"
                    " (turn_id, event_seq, attempt_no, payload_json) VALUES (?,?,?,?)",
                    (turn_id, seq, attempt_no, payload),
                )
                connection.execute(
                    "UPDATE agent_turns SET updated_at = ? WHERE id = ?", (now.isoformat(), turn_id)
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return seq

    def events_after(
        self, turn_id: str, event_seq: int
    ) -> Sequence[tuple[int, int, ProgressEvent]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT event_seq, attempt_no, payload_json FROM agent_turn_events_v2"
                " WHERE turn_id = ? AND event_seq > ? ORDER BY event_seq ASC",
                (turn_id, event_seq),
            ).fetchall()
        return [
            (int(row["event_seq"]), int(row["attempt_no"]), _json_to_event(row["payload_json"]))
            for row in rows
        ]

    def finish_with_message(
        self,
        turn_id: str,
        *,
        fence: int,
        now: datetime,
        status: str,
        termination_reason: str,
        answer: str,
        attachments: Sequence[Attachment],
        requirements: Sequence[Mapping[str, Any]],
        retrieved_candidates: Sequence[str],
        trace: Mapping[str, Any],
        message_id: str,
        conversation_id: str,
        scope_id: str,
    ) -> bool:
        """最终助手消息与回合终态在同一事务里提交；围栏不匹配则不写。"""
        payload = json.dumps(
            [
                {"handle": item.handle, "asset_id": item.asset_id, "file_name": item.file_name}
                for item in attachments
            ],
            ensure_ascii=False,
        )
        ledger_payload = json.dumps([dict(item) for item in requirements], ensure_ascii=False)
        candidates_payload = json.dumps(list(retrieved_candidates), ensure_ascii=False)
        trace_payload = json.dumps(dict(trace), ensure_ascii=False)
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "UPDATE agent_turns SET status = ?, termination_reason = ?, answer = ?,"
                    " attachments_json = ?, requirements_json = ?, candidates_json = ?,"
                    " trace_json = ?, execution_state = '', waiting_reason = '',"
                    " next_retry_at = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND fence = ? AND status IN ('pending', 'running')",
                    (
                        status,
                        termination_reason,
                        answer,
                        payload,
                        ledger_payload,
                        candidates_payload,
                        trace_payload,
                        now.isoformat(),
                        turn_id,
                        fence,
                    ),
                )
                if cursor.rowcount == 0:
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT OR IGNORE INTO agent_conversation_messages"
                    " (id, conversation_id, scope_id, turn_id, role, content, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        message_id,
                        conversation_id,
                        scope_id,
                        turn_id,
                        "assistant",
                        answer,
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    "UPDATE agent_conversations SET last_message_at = ?, updated_at = ? WHERE id = ?",
                    (now.isoformat(), now.isoformat(), conversation_id),
                )
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return True

    def finish(
        self,
        turn_id: str,
        *,
        status: str,
        termination_reason: str,
        answer: str,
        attachments: Sequence[Attachment],
        requirements: Sequence[Mapping[str, Any]],
        retrieved_candidates: Sequence[str],
        trace: Mapping[str, Any],
        updated_at: datetime,
    ) -> None:
        """兼容旧入口（无围栏）；新执行器走 ``finish_with_message``。"""
        payload = json.dumps(
            [
                {"handle": item.handle, "asset_id": item.asset_id, "file_name": item.file_name}
                for item in attachments
            ],
            ensure_ascii=False,
        )
        ledger_payload = json.dumps([dict(item) for item in requirements], ensure_ascii=False)
        candidates_payload = json.dumps(list(retrieved_candidates), ensure_ascii=False)
        trace_payload = json.dumps(dict(trace), ensure_ascii=False)
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE agent_turns SET status = ?, termination_reason = ?, answer = ?,"
                " attachments_json = ?, requirements_json = ?, candidates_json = ?,"
                " trace_json = ?, updated_at = ? WHERE id = ?",
                (
                    status,
                    termination_reason,
                    answer,
                    payload,
                    ledger_payload,
                    candidates_payload,
                    trace_payload,
                    updated_at.isoformat(),
                    turn_id,
                ),
            )

    def oldest_active_updated_at(self) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT MIN(updated_at) AS value FROM agent_turns"
                " WHERE status IN ('pending', 'running')"
            ).fetchone()
        return str(row["value"]) if row is not None and row["value"] is not None else None

    def status_counts(self) -> dict[str, int]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM agent_turns GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def list_for_conversation(self, conversation_id: str, limit: int) -> Sequence[TurnRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM agent_turns WHERE conversation_id = ?"
                " ORDER BY created_at ASC, rowid ASC LIMIT ?",
                (conversation_id, max(1, limit)),
            ).fetchall()
        return [_row_to_turn(row) for row in rows]

    def list_active(self, *, limit: int = 20) -> Sequence[TurnRecord]:
        """在飞回合：控制面只读运行元数据，不读问题与答案正文。"""
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM agent_turns WHERE status IN ('pending', 'running')"
                " ORDER BY updated_at ASC, rowid ASC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_row_to_turn(row) for row in rows]

    def list_recent(self, *, limit: int = 20) -> Sequence[TurnRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM agent_turns"
                " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_row_to_turn(row) for row in rows]

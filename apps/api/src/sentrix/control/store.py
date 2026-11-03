"""控制库：账号、会话、登录限速、审计、反馈、事件与采样。

复用产品侧的 SQLite 迁移器（``infrastructure.persistence.database``），但迁移目录
是本包的 ``migrations/``，与用户数据根完全分开。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentrix.control import security
from sentrix.infrastructure.persistence.database import Database

CONTROL_MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_ACCOUNT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


class AccountError(ValueError):
    pass


@dataclass(frozen=True)
class Account:
    id: str
    display_name: str
    role: str
    disabled: bool
    api_url: str
    data_root: str
    password_algo: str
    password_salt: str
    password_hash: str
    password_params: str


@dataclass(frozen=True)
class SessionRecord:
    id: str
    account_id: str
    csrf_hash: str
    created_at: str
    last_seen_at: str
    expires_at: str
    revoked_at: str | None


@dataclass(frozen=True)
class SessionView:
    account: Account
    session: SessionRecord


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ControlStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    @property
    def database(self) -> Database:
        return self._database

    def migrate(self) -> list[str]:
        return self._database.migrate()

    def ping(self) -> bool:
        try:
            with self._database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001 - ready 只看可用性，不吞栈到调用方
            return False
        return True

    def account_count(self, *, role: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM accounts"
        parameters: tuple[object, ...] = ()
        if role is not None:
            sql += " WHERE role = ? AND disabled = 0"
            parameters = (role,)
        with self._database.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return int(row["n"]) if row is not None else 0

    def schema_version(self) -> str:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return str(row["version"]) if row is not None else ""

    # ---- 账号 ---------------------------------------------------------------

    def create_account(
        self,
        account_id: str,
        *,
        display_name: str,
        role: str,
        password: str,
        api_url: str = "",
        data_root: str = "",
    ) -> Account:
        if not _ACCOUNT_ID.fullmatch(account_id):
            raise AccountError("account id must be 3-64 chars of [a-z0-9_-]")
        if role not in {"user", "admin"}:
            raise AccountError("role must be user or admin")
        if role == "user":
            if not api_url or not data_root:
                raise AccountError("user account requires api_url and data_root")
            if not Path(data_root).is_absolute():
                raise AccountError("data_root must be absolute")
        salt, digest, params = security.hash_password(password)
        now = _iso(_now())
        try:
            with self._database.connect() as connection, connection:
                if role == "user":
                    clash = connection.execute(
                        "SELECT id FROM accounts WHERE api_url = ? AND role = 'user'", (api_url,)
                    ).fetchone()
                    if clash is not None:
                        raise AccountError(f"api_url already registered to {clash['id']}")
                connection.execute(
                    """
                    INSERT INTO accounts (
                        id, display_name, role, password_algo, password_salt, password_hash,
                        password_params_json, api_url, data_root, disabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        account_id,
                        display_name,
                        role,
                        security.PASSWORD_ALGO,
                        salt,
                        digest,
                        params,
                        api_url,
                        data_root,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AccountError(f"account already exists: {account_id}") from error
        account = self.get_account(account_id)
        if account is None:  # pragma: no cover - 插入后必然可读
            raise AccountError("account insert did not persist")
        return account

    def get_account(self, account_id: str) -> Account | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _account(row) if row is not None else None

    def set_password(self, account_id: str, password: str) -> bool:
        salt, digest, params = security.hash_password(password)
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET password_algo = ?, password_salt = ?, password_hash = ?,
                    password_params_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (security.PASSWORD_ALGO, salt, digest, params, _iso(_now()), account_id),
            )
        return cursor.rowcount > 0

    def verify_account_password(self, account: Account, password: str) -> bool:
        if account.disabled:
            return False
        return security.verify_password(
            password,
            salt_b64=account.password_salt,
            hash_b64=account.password_hash,
            params_json=account.password_params,
            algo=account.password_algo,
        )

    # ---- 会话 ---------------------------------------------------------------

    def create_session(
        self,
        account_id: str,
        *,
        token: str,
        csrf_token: str,
        ip: str,
        user_agent: str,
        idle_s: int,
        max_s: int,
    ) -> SessionRecord:
        now = _now()
        expires = now + timedelta(seconds=min(idle_s, max_s))
        session = SessionRecord(
            id=security.token_digest(token),
            account_id=account_id,
            csrf_hash=security.token_digest(csrf_token),
            created_at=_iso(now),
            last_seen_at=_iso(now),
            expires_at=_iso(expires),
            revoked_at=None,
        )
        with self._database.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, account_id, csrf_hash, created_at, last_seen_at,
                    expires_at, revoked_at, ip, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    session.id,
                    session.account_id,
                    session.csrf_hash,
                    session.created_at,
                    session.last_seen_at,
                    session.expires_at,
                    ip,
                    user_agent,
                ),
            )
        return session

    def get_session(self, token: str, *, idle_s: int, max_s: int) -> SessionView | None:
        digest = security.token_digest(token)
        now = _now()
        with self._database.connect() as connection, connection:
            row = connection.execute(
                """
                SELECT s.*, a.id AS a_id, a.display_name, a.role, a.disabled,
                       a.api_url, a.data_root, a.password_algo, a.password_salt,
                       a.password_hash, a.password_params_json
                FROM sessions s JOIN accounts a ON a.id = s.account_id
                WHERE s.id = ?
                """,
                (digest,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or bool(row["disabled"]):
                return None
            created_at = _parse(str(row["created_at"]))
            expires_at = _parse(str(row["expires_at"]))
            absolute_deadline = created_at + timedelta(seconds=max_s)
            if now >= expires_at or now >= absolute_deadline:
                connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE id = ?",
                    (_iso(now), digest),
                )
                return None
            next_expiry = min(now + timedelta(seconds=idle_s), absolute_deadline)
            connection.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                (_iso(now), _iso(next_expiry), digest),
            )
            session = SessionRecord(
                id=str(row["id"]),
                account_id=str(row["account_id"]),
                csrf_hash=str(row["csrf_hash"]),
                created_at=str(row["created_at"]),
                last_seen_at=_iso(now),
                expires_at=_iso(next_expiry),
                revoked_at=None,
            )
            account = Account(
                id=str(row["a_id"]),
                display_name=str(row["display_name"]),
                role=str(row["role"]),
                disabled=bool(row["disabled"]),
                api_url=str(row["api_url"]),
                data_root=str(row["data_root"]),
                password_algo=str(row["password_algo"]),
                password_salt=str(row["password_salt"]),
                password_hash=str(row["password_hash"]),
                password_params=str(row["password_params_json"]),
            )
        return SessionView(account=account, session=session)

    def revoke_session(self, token: str) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_iso(_now()), security.token_digest(token)),
            )

    def revoke_account_sessions(self, account_id: str) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL",
                (_iso(_now()), account_id),
            )
        return cursor.rowcount

    # ---- 登录限速 ------------------------------------------------------------

    def record_login_attempt(self, account_id: str, ip: str, *, success: bool) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO login_attempts (account_id, ip, attempted_at, success) VALUES (?, ?, ?, ?)",
                (account_id, ip, _iso(_now()), 1 if success else 0),
            )

    def login_failures(self, account_id: str, ip: str, *, window_s: int) -> tuple[int, int]:
        """返回 (账号+IP 失败数, 同 IP 失败数)。"""
        since = _iso(_now() - timedelta(seconds=window_s))
        with self._database.connect() as connection:
            pair = connection.execute(
                "SELECT COUNT(*) AS n FROM login_attempts "
                "WHERE account_id = ? AND ip = ? AND success = 0 AND attempted_at >= ?",
                (account_id, ip, since),
            ).fetchone()
            by_ip = connection.execute(
                "SELECT COUNT(*) AS n FROM login_attempts "
                "WHERE ip = ? AND success = 0 AND attempted_at >= ?",
                (ip, since),
            ).fetchone()
        return int(pair["n"]), int(by_ip["n"])

    def purge_login_attempts(self, *, older_than_s: int) -> None:
        cutoff = _iso(_now() - timedelta(seconds=older_than_s))
        with self._database.connect() as connection, connection:
            connection.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))

    # ---- 事件与采样 ---------------------------------------------------------

    def record_metric(self, *, sampled_at: str, source: str, payload: dict[str, object]) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO metric_samples (sampled_at, source, payload_json) VALUES (?, ?, ?)",
                (sampled_at, source, json.dumps(payload, ensure_ascii=False)),
            )

    def open_incident(
        self,
        *,
        source: str,
        category: str,
        severity: str,
        message: str,
        now: str,
        user_id: str = "",
        request_id: str = "",
        job_id: str = "",
        turn_id: str = "",
    ) -> str:
        with self._database.connect() as connection, connection:
            existing = connection.execute(
                "SELECT event_id FROM incidents WHERE source = ? AND category = ? AND status = 'open'"
                " ORDER BY last_seen DESC LIMIT 1",
                (source, category),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "UPDATE incidents SET last_seen = ?, occurrences = occurrences + 1"
                    " WHERE event_id = ?",
                    (now, existing["event_id"]),
                )
                return str(existing["event_id"])
            event_id = f"inc_{uuid.uuid4().hex[:16]}"
            connection.execute(
                """
                INSERT INTO incidents (
                    event_id, time, source, severity, user_id, request_id, job_id, turn_id,
                    category, message, recovery_action, result, status, occurrences,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 'open', 1, ?, ?)
                """,
                (
                    event_id,
                    now,
                    source,
                    severity,
                    user_id,
                    request_id,
                    job_id,
                    turn_id,
                    category,
                    message,
                    now,
                    now,
                ),
            )
        return event_id

    def close_incidents(self, *, source: str, category: str, now: str, result: str) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE incidents SET status = 'closed', closed_at = ?, result = ?"
                " WHERE source = ? AND category = ? AND status = 'open'",
                (now, result, source, category),
            )
        return cursor.rowcount

    def list_incidents(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents ORDER BY last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_metrics(self, *, older_than_days: int = 30) -> None:
        cutoff = _iso(_now() - timedelta(days=older_than_days))
        with self._database.connect() as connection, connection:
            connection.execute("DELETE FROM metric_samples WHERE sampled_at < ?", (cutoff,))

    def latest_metric(self, *, source: str) -> dict[str, object] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT sampled_at, payload_json FROM metric_samples"
                " WHERE source = ? ORDER BY sampled_at DESC LIMIT 1",
                (source,),
            ).fetchone()
        if row is None:
            return None
        return {
            "sampled_at": str(row["sampled_at"]),
            "payload": json.loads(row["payload_json"]),
        }

    def list_metric_series(
        self, *, source: str, since: str, limit: int = 1440
    ) -> list[dict[str, object]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT sampled_at, payload_json FROM metric_samples"
                " WHERE source = ? AND sampled_at >= ? ORDER BY sampled_at ASC LIMIT ?",
                (source, since, max(1, limit)),
            ).fetchall()
        return [
            {"sampled_at": str(row["sampled_at"]), "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def purge_audit(self, *, older_than_days: int = 90) -> int:
        cutoff = _iso(_now() - timedelta(days=older_than_days))
        with self._database.connect() as connection, connection:
            cursor = connection.execute("DELETE FROM audit_events WHERE time < ?", (cutoff,))
        return cursor.rowcount

    def purge_feedback(self, *, older_than_days: int = 90) -> list[str]:
        """返回被删反馈的截图路径，由调用方删除文件。"""
        cutoff = _iso(_now() - timedelta(days=older_than_days))
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                paths = [
                    str(row["storage_path"])
                    for row in connection.execute(
                        "SELECT storage_path FROM feedback_attachments WHERE feedback_id IN"
                        " (SELECT id FROM feedback WHERE created_at < ?)",
                        (cutoff,),
                    ).fetchall()
                ]
                connection.execute(
                    "DELETE FROM feedback_updates WHERE feedback_id IN"
                    " (SELECT id FROM feedback WHERE created_at < ?)",
                    (cutoff,),
                )
                connection.execute(
                    "DELETE FROM feedback_attachments WHERE feedback_id IN"
                    " (SELECT id FROM feedback WHERE created_at < ?)",
                    (cutoff,),
                )
                connection.execute("DELETE FROM feedback WHERE created_at < ?", (cutoff,))
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        return paths

    # ---- 反馈 ---------------------------------------------------------------

    def create_feedback(
        self,
        account_id: str,
        *,
        kind: str,
        body: str,
        route: str,
        scope_id: str,
        job_id: str,
        turn_id: str,
        event_id: str,
        asset_id: str,
        request_id: str,
        idempotency_key: str,
        content_signature: str,
    ) -> tuple[dict[str, object], bool]:
        feedback_id = f"fb_{uuid.uuid4().hex[:16]}"
        now = _iso(_now())
        with self._database.connect() as connection, connection:
            existing = connection.execute(
                "SELECT * FROM feedback WHERE account_id = ? AND idempotency_key = ?",
                (account_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["content_signature"]) != content_signature:
                    raise AccountError("idempotency key reused for different feedback")
                return _feedback(existing), False
            connection.execute(
                """
                INSERT INTO feedback (
                    id, account_id, kind, body, route, scope_id, job_id, turn_id,
                    event_id, asset_id, request_id, status, idempotency_key,
                    content_signature, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    account_id,
                    kind,
                    body,
                    route,
                    scope_id,
                    job_id,
                    turn_id,
                    event_id,
                    asset_id,
                    request_id,
                    idempotency_key,
                    content_signature,
                    now,
                    now,
                ),
            )
        row = self._feedback_row(feedback_id)
        if row is None:  # pragma: no cover
            raise AccountError("feedback insert did not persist")
        return row, True

    def list_feedback(self, account_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM feedback WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        return [_feedback(row) for row in rows]

    def list_feedback_admin(
        self,
        *,
        account_id: str = "",
        kind: str = "",
        status: str = "",
        since: str = "",
        until: str = "",
        related: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (("account_id", account_id), ("kind", kind), ("status", status)):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if since:
            clauses.append("created_at >= ?")
            parameters.append(since)
        if until:
            clauses.append("created_at <= ?")
            parameters.append(until)
        if related:
            clauses.append(
                "(job_id = ? OR turn_id = ? OR event_id = ? OR asset_id = ? OR request_id = ?)"
            )
            parameters.extend([related] * 5)
        sql = "SELECT * FROM feedback"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self._database.connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [_feedback_admin(row) for row in rows]

    def list_feedback_attachments(self, feedback_id: str) -> list[dict[str, object]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, content_type, byte_size, created_at FROM feedback_attachments"
                " WHERE feedback_id = ? ORDER BY created_at ASC",
                (feedback_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_feedback_admin(self, feedback_id: str) -> dict[str, object] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
            ).fetchone()
        return _feedback_admin(row) if row is not None else None

    def get_feedback(
        self, feedback_id: str, *, account_id: str | None = None
    ) -> dict[str, object] | None:
        with self._database.connect() as connection:
            if account_id is None:
                row = connection.execute(
                    "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM feedback WHERE id = ? AND account_id = ?",
                    (feedback_id, account_id),
                ).fetchone()
        return _feedback(row) if row is not None else None

    def add_feedback_update(self, feedback_id: str, *, author: str, body: str) -> dict[str, object]:
        update_id = f"fbu_{uuid.uuid4().hex[:16]}"
        now = _iso(_now())
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO feedback_updates (id, feedback_id, author, body, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (update_id, feedback_id, author, body, now),
            )
            connection.execute(
                "UPDATE feedback SET updated_at = ? WHERE id = ?", (now, feedback_id)
            )
        return {"id": update_id, "feedback_id": feedback_id, "author": author, "body": body, "created_at": now}

    def list_feedback_updates(self, feedback_id: str) -> list[dict[str, object]]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM feedback_updates WHERE feedback_id = ? ORDER BY created_at ASC",
                (feedback_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_feedback_admin(
        self,
        feedback_id: str,
        *,
        status: str | None = None,
        assignee: str | None = None,
        severity: str | None = None,
        internal_note: str | None = None,
        reply: str | None = None,
    ) -> bool:
        fields: list[str] = []
        values: list[object] = []
        for column, value in (
            ("status", status),
            ("assignee", assignee),
            ("severity", severity),
            ("internal_note", internal_note),
            ("reply", reply),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if not fields:
            return True
        fields.append("updated_at = ?")
        values.append(_iso(_now()))
        values.append(feedback_id)
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                f"UPDATE feedback SET {', '.join(fields)} WHERE id = ?", tuple(values)
            )
        return cursor.rowcount > 0

    def add_feedback_attachment(
        self,
        feedback_id: str,
        *,
        content_type: str,
        byte_size: int,
        sha256: str,
        storage_path: str,
    ) -> dict[str, object]:
        attachment_id = f"fba_{uuid.uuid4().hex[:16]}"
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO feedback_attachments"
                " (id, feedback_id, content_type, byte_size, sha256, storage_path, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    attachment_id,
                    feedback_id,
                    content_type,
                    byte_size,
                    sha256,
                    storage_path,
                    _iso(_now()),
                ),
            )
        return self.get_attachment(attachment_id) or {}

    def get_attachment(self, attachment_id: str) -> dict[str, object] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM feedback_attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _feedback_row(self, feedback_id: str) -> dict[str, object] | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        return _feedback(row) if row is not None else None

    # ---- 客户端操作轨迹 ------------------------------------------------------

    def record_client_event(
        self,
        *,
        account_id: str,
        session_id: str,
        operation_id: str,
        event_name: str,
        route: str,
        result: str,
        scope_id: str = "",
        job_id: str = "",
        turn_id: str = "",
        event_id: str = "",
        asset_id: str = "",
        entity_id: str = "",
        error_code: str = "",
    ) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO client_events (
                    received_at, account_id, session_id, operation_id, event_name,
                    route, result, scope_id, job_id, turn_id, event_id, asset_id,
                    entity_id, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _iso(_now()),
                    account_id,
                    session_id,
                    operation_id,
                    event_name,
                    route,
                    result,
                    scope_id,
                    job_id,
                    turn_id,
                    event_id,
                    asset_id,
                    entity_id,
                    error_code,
                ),
            )
        return int(cursor.lastrowid or 0)

    def list_client_events(
        self, account_id: str, *, after_id: int = 0, limit: int = 50
    ) -> tuple[list[dict[str, object]], bool]:
        """返回 (按时间正序的事件, has_more)。

        首次查询（after_id=0）取最近有限条后在响应中恢复正序，不整表拉回。
        增量查询按自增游标升序，避免同一批事件被重复返回。
        """
        size = max(1, min(limit, 200))
        with self._database.connect() as connection:
            if after_id <= 0:
                rows = connection.execute(
                    "SELECT * FROM client_events WHERE account_id = ?"
                    " ORDER BY id DESC LIMIT ?",
                    (account_id, size),
                ).fetchall()
                return [dict(row) for row in reversed(rows)], False
            rows = connection.execute(
                "SELECT * FROM client_events WHERE account_id = ? AND id > ?"
                " ORDER BY id ASC LIMIT ?",
                (account_id, after_id, size),
            ).fetchall()
        items = [dict(row) for row in rows]
        return items, len(items) >= size

    def latest_client_event_times(
        self, account_ids: list[str]
    ) -> dict[str, dict[str, object]]:
        """按账号批量取最新一条事件（id/时间），不逐账号扫描整段轨迹。"""
        if not account_ids:
            return {}
        placeholders = ",".join("?" for _ in account_ids)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.account_id, c.id, c.received_at, c.event_name
                FROM client_events c
                JOIN (
                    SELECT account_id, MAX(id) AS max_id FROM client_events
                    WHERE account_id IN ({placeholders}) GROUP BY account_id
                ) latest ON latest.max_id = c.id
                """,
                tuple(account_ids),
            ).fetchall()
        return {
            str(row["account_id"]): {
                "id": int(row["id"]),
                "received_at": str(row["received_at"]),
                "event_name": str(row["event_name"]),
            }
            for row in rows
        }

    def purge_client_events(self, *, older_than_days: int = 7) -> int:
        cutoff = _iso(_now() - timedelta(days=older_than_days))
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM client_events WHERE received_at < ?", (cutoff,)
            )
        return cursor.rowcount

    # ---- 审计 ---------------------------------------------------------------

    def audit(
        self,
        action: str,
        *,
        actor_account_id: str = "",
        target: str = "",
        result: str = "",
        detail: dict[str, object] | None = None,
        request_id: str = "",
    ) -> str:
        event_id = f"aud_{uuid.uuid4().hex[:20]}"
        with self._database.connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, time, actor_account_id, action, target, result, detail_json, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _iso(_now()),
                    actor_account_id,
                    action,
                    target,
                    result,
                    json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                    request_id,
                ),
            )
        return event_id

    # ---- 重启操作的幂等记录 --------------------------------------------------

    def get_restart_request(self, idempotency_key: str) -> dict[str, object] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM restart_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def save_restart_request(
        self,
        idempotency_key: str,
        *,
        service_id: str,
        request_id: str,
        status_code: int,
        body: dict[str, object],
    ) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO restart_requests"
                " (idempotency_key, service_id, request_id, status_code, body_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    idempotency_key,
                    service_id,
                    request_id,
                    status_code,
                    json.dumps(body, ensure_ascii=False),
                    _iso(_now()),
                ),
            )


def _feedback(row: sqlite3.Row) -> dict[str, object]:
    """浏览器可见的反馈投影：不含内部备注。"""
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "body": str(row["body"]),
        "route": str(row["route"]),
        "scope_id": str(row["scope_id"]),
        "job_id": str(row["job_id"]),
        "turn_id": str(row["turn_id"]),
        "event_id": str(row["event_id"]),
        "asset_id": str(row["asset_id"]),
        "request_id": str(row["request_id"]),
        "status": str(row["status"]),
        "reply": str(row["reply"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _feedback_admin(row: sqlite3.Row) -> dict[str, object]:
    values = _feedback(row)
    values.update(
        {
            "account_id": str(row["account_id"]),
            "assignee": str(row["assignee"]),
            "severity": str(row["severity"]),
            "internal_note": str(row["internal_note"]),
        }
    )
    return values


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        disabled=bool(row["disabled"]),
        api_url=str(row["api_url"]),
        data_root=str(row["data_root"]),
        password_algo=str(row["password_algo"]),
        password_salt=str(row["password_salt"]),
        password_hash=str(row["password_hash"]),
        password_params=str(row["password_params_json"]),
    )

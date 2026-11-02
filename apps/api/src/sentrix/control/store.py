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

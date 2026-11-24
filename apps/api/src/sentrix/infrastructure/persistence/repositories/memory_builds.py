"""图片构建版本与阶段续办任务的 SQLite 实现。

构建记录只回答「这次产出用了什么输入/模型/协议」，不承载素材正文；素材当前有效值
仍在 Observation。所有写入都按 ``build_key`` 幂等，重复执行不会造第二个版本。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sentrix.domain.media.models import MemoryBuildTask, ObservationBuild
from sentrix.infrastructure.persistence.database import Database

_BUILD_COLUMNS = (
    "id, scope_id, asset_id, observation_id, observation_revision, stage, status,"
    " build_key, input_hash, model_name, protocol_version, prompt_hash, params_json,"
    " input_transform_json, raw_response_ref, error_category, error, attempt,"
    " started_at, finished_at, created_at, updated_at"
)
_TASK_COLUMNS = (
    "id, scope_id, asset_id, stage, build_key, status, attempts, next_attempt_at,"
    " last_error, lease_until, created_at, updated_at"
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _row_to_build(row: sqlite3.Row) -> ObservationBuild:
    return ObservationBuild(
        id=str(row["id"]),
        scope_id=str(row["scope_id"]),
        asset_id=str(row["asset_id"]),
        observation_id=str(row["observation_id"]) if row["observation_id"] else None,
        observation_revision=int(row["observation_revision"]),
        stage=str(row["stage"]),
        status=str(row["status"]),
        build_key=str(row["build_key"]),
        input_hash=str(row["input_hash"]),
        model_name=str(row["model_name"]),
        protocol_version=str(row["protocol_version"]),
        prompt_hash=str(row["prompt_hash"]),
        params=json.loads(str(row["params_json"])),
        input_transform=json.loads(str(row["input_transform_json"])),
        raw_response_ref=str(row["raw_response_ref"]),
        error_category=str(row["error_category"]),
        error=str(row["error"]),
        attempt=int(row["attempt"]),
        started_at=_parse_datetime(row["started_at"]),
        finished_at=_parse_datetime(row["finished_at"]),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _row_to_task(row: sqlite3.Row) -> MemoryBuildTask:
    return MemoryBuildTask(
        id=str(row["id"]),
        scope_id=str(row["scope_id"]),
        asset_id=str(row["asset_id"]),
        stage=str(row["stage"]),
        build_key=str(row["build_key"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        next_attempt_at=_parse_datetime(row["next_attempt_at"]) or datetime.now(UTC),
        last_error=str(row["last_error"]),
        lease_until=_parse_datetime(row["lease_until"]),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


class SqliteMemoryBuildRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_or_get(self, build: ObservationBuild) -> tuple[ObservationBuild, bool]:
        now = _now()
        with self._database.connect() as connection, connection:
            asset = connection.execute(
                "SELECT scope_id FROM assets WHERE id = ?", (build.asset_id,)
            ).fetchone()
            if asset is None:
                raise ValueError("build asset not found")
            if str(asset["scope_id"]) != build.scope_id:
                raise ValueError("build asset scope mismatch")
            if build.observation_id:
                observation = connection.execute(
                    "SELECT scope_id, asset_id FROM observations WHERE id = ?",
                    (build.observation_id,),
                ).fetchone()
                if observation is None:
                    raise ValueError("build observation not found")
                if (
                    str(observation["scope_id"]) != build.scope_id
                    or str(observation["asset_id"]) != build.asset_id
                ):
                    raise ValueError("build observation does not belong to asset")
            cursor = connection.execute(
                "INSERT INTO observation_builds("
                " id, scope_id, asset_id, observation_id, observation_revision, stage, status,"
                " build_key, input_hash, model_name, protocol_version, prompt_hash, params_json,"
                " input_transform_json, raw_response_ref, error_category, error, attempt,"
                " started_at, finished_at, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(build_key) DO NOTHING",
                (
                    build.id or _new_id("build"),
                    build.scope_id,
                    build.asset_id,
                    build.observation_id,
                    build.observation_revision,
                    build.stage,
                    build.status,
                    build.build_key,
                    build.input_hash,
                    build.model_name,
                    build.protocol_version,
                    build.prompt_hash,
                    _json(build.params),
                    _json(build.input_transform),
                    build.raw_response_ref,
                    build.error_category,
                    build.error,
                    build.attempt,
                    _timestamp(build.started_at),
                    _timestamp(build.finished_at),
                    _timestamp(build.created_at) or now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                f"SELECT {_BUILD_COLUMNS} FROM observation_builds WHERE build_key = ?",
                (build.build_key,),
            ).fetchone()
        assert row is not None, "刚写入的构建记录读不回来"
        return _row_to_build(row), created

    def get(self, build_id: str) -> ObservationBuild | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_BUILD_COLUMNS} FROM observation_builds WHERE id = ?", (build_id,)
            ).fetchone()
        return _row_to_build(row) if row is not None else None

    def update_status(
        self,
        build_id: str,
        *,
        status: str,
        error_category: str = "",
        error: str = "",
        finished_at: datetime | None = None,
    ) -> ObservationBuild | None:
        with self._database.connect() as connection, connection:
            row = connection.execute(
                "SELECT status FROM observation_builds WHERE id = ?", (build_id,)
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) == "succeeded" and status != "succeeded":
                raise ValueError("succeeded build cannot regress")
            connection.execute(
                "UPDATE observation_builds SET status = ?, error_category = ?, error = ?,"
                " finished_at = COALESCE(?, finished_at), updated_at = ? WHERE id = ?",
                (status, error_category, error, _timestamp(finished_at), _now(), build_id),
            )
        return self.get(build_id)

    def list_for_asset(self, asset_id: str) -> Sequence[ObservationBuild]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_BUILD_COLUMNS} FROM observation_builds"
                " WHERE asset_id = ? ORDER BY created_at DESC, id DESC",
                (asset_id,),
            ).fetchall()
        return tuple(_row_to_build(row) for row in rows)

    def latest_for_stage(self, asset_id: str, stage: str) -> ObservationBuild | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_BUILD_COLUMNS} FROM observation_builds"
                " WHERE asset_id = ? AND stage = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (asset_id, stage),
            ).fetchone()
        return _row_to_build(row) if row is not None else None


class SqliteMemoryBuildTaskRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def enqueue(self, task: MemoryBuildTask) -> MemoryBuildTask:
        now = _now()
        with self._database.connect() as connection, connection:
            asset = connection.execute(
                "SELECT scope_id FROM assets WHERE id = ?", (task.asset_id,)
            ).fetchone()
            if asset is None:
                raise ValueError("task asset not found")
            if str(asset["scope_id"]) != task.scope_id:
                raise ValueError("task asset scope mismatch")
            connection.execute(
                "INSERT INTO memory_build_tasks("
                " id, scope_id, asset_id, stage, build_key, status, attempts, next_attempt_at,"
                " last_error, lease_until, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(build_key, stage) DO NOTHING",
                (
                    task.id or _new_id("task"),
                    task.scope_id,
                    task.asset_id,
                    task.stage,
                    task.build_key,
                    task.status,
                    task.attempts,
                    task.next_attempt_at.isoformat(),
                    task.last_error,
                    _timestamp(task.lease_until),
                    _timestamp(task.created_at) or now,
                    now,
                ),
            )
            row = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM memory_build_tasks WHERE build_key = ? AND stage = ?",
                (task.build_key, task.stage),
            ).fetchone()
        assert row is not None, "刚写入的阶段任务读不回来"
        return _row_to_task(row)

    def get(self, task_id: str) -> MemoryBuildTask | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM memory_build_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def list_due(self, *, limit: int, now: datetime | None = None) -> Sequence[MemoryBuildTask]:
        cutoff = (now or datetime.now(UTC)).isoformat()
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM memory_build_tasks"
                " WHERE status = 'queued' AND next_attempt_at <= ?"
                " ORDER BY next_attempt_at, id LIMIT ?",
                (cutoff, max(0, int(limit))),
            ).fetchall()
        return tuple(_row_to_task(row) for row in rows)

    def list_for_scope(
        self, scope_id: str, *, statuses: Sequence[str] = (), limit: int = 200
    ) -> Sequence[MemoryBuildTask]:
        params: list[object] = [scope_id]
        clause = "scope_id = ?"
        if statuses:
            clause += f" AND status IN ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM memory_build_tasks WHERE {clause}"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (*params, max(1, int(limit))),
            ).fetchall()
        return tuple(_row_to_task(row) for row in rows)

    def claim_due(
        self, *, limit: int, now: datetime | None = None, lease_seconds: int = 300
    ) -> Sequence[MemoryBuildTask]:
        moment = now or datetime.now(UTC)
        cutoff = moment.isoformat()
        lease = (moment + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        claimed: list[MemoryBuildTask] = []
        with self._database.connect() as connection, connection:
            rows = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM memory_build_tasks"
                " WHERE status = 'queued' AND next_attempt_at <= ? ORDER BY next_attempt_at, id LIMIT ?",
                (cutoff, max(1, int(limit))),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    "UPDATE memory_build_tasks SET status = 'running', lease_until = ?,"
                    " attempts = attempts + 1, updated_at = ? WHERE id = ? AND status = 'queued'",
                    (lease, cutoff, row["id"]),
                )
                if cursor.rowcount:
                    claimed.append(_row_to_task(row))
        return tuple(claimed)

    def complete(self, task_id: str, *, status: str, error: str = "") -> MemoryBuildTask | None:
        if status not in {"done", "failed", "canceled"}:
            raise ValueError(f"unknown task completion status: {status}")
        current = self.get(task_id)
        if current is None:
            return None
        moment = datetime.now(UTC)
        next_status = status
        next_attempt = moment
        if status == "failed" and current.attempts < 3:
            next_status = "queued"
            next_attempt = moment + timedelta(seconds=60 * current.attempts)
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE memory_build_tasks SET status = ?, last_error = ?, lease_until = NULL,"
                " next_attempt_at = ?, updated_at = ? WHERE id = ?",
                (
                    next_status,
                    error[:1000],
                    next_attempt.isoformat(),
                    moment.isoformat(),
                    task_id,
                ),
            )
        return self.get(task_id)

    def cancel_scope(self, scope_id: str) -> int:
        with self._database.connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE memory_build_tasks SET status = 'canceled', lease_until = NULL,"
                " updated_at = ? WHERE scope_id = ? AND status IN ('queued', 'running')",
                (datetime.now(UTC).isoformat(), scope_id),
            )
            return int(cursor.rowcount)

    def list_for_asset(self, asset_id: str, *, statuses: Sequence[str] = ()) -> Sequence[MemoryBuildTask]:
        params: list[object] = [asset_id]
        clause = "asset_id = ?"
        if statuses:
            clause += f" AND status IN ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_TASK_COLUMNS} FROM memory_build_tasks WHERE {clause} ORDER BY created_at, id",
                tuple(params),
            ).fetchall()
        return tuple(_row_to_task(row) for row in rows)

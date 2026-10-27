"""``memory_vectors`` ↔ ``text_vectors`` 的复制、校验、反向同步与清理。

这是维护脚本的纯逻辑层：不做 argparse、不读环境变量，全部参数显式传入，方便测试。
CLI 包装在 ``text_vectors_migrate.py``。

三条不变量：

1. **只碰文本空间**。所有读写都带 ``space IN ('semantic','episodic')``，视觉/人脸行
   （``visual/asset``、``visual/face_instance``）永远不在范围内。
2. **默认不写**。每个写操作都有 ``apply=False`` 的 dry-run 路径；审计报告与 dry-run
   共用同一份计划，避免「演练看的数」和「实际做的数」是两套逻辑。
3. **冲突显式化**。行数相等不代表内容一致；这里比对自然键集合、物理主键、声明维度与
   实际维度、向量/元数据内容哈希与时间戳，并按固定分类回报。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

TEXT_SPACES: tuple[str, str] = ("semantic", "episodic")
OLD_TABLE = "memory_vectors"
NEW_TABLE = "text_vectors"

COLUMNS = (
    "id, scope_id, space, source_type, source_id, model_name, dimension,"
    " vector_json, metadata_json, created_at, updated_at"
)

CONFLICT_KINDS = (
    "missing_in_new",
    "extra_in_new",
    "id_mismatch",
    "dimension_mismatch",
    "vector_mismatch",
    "metadata_mismatch",
    "timestamp_mismatch",
    "malformed",
)

MAX_REPORTED_CONFLICTS = 100
CONFLICT_POLICIES = ("abort", "prefer-old", "prefer-new")


def text_space_clause(column: str = "space") -> str:
    return f"{column} IN (?, ?)"


@dataclass(frozen=True)
class NaturalKey:
    space: str
    source_type: str
    source_id: str
    model_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "space": self.space,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "model_name": self.model_name,
        }

    def label(self) -> str:
        return f"{self.space}/{self.source_type}/{self.source_id}/{self.model_name}"


@dataclass(frozen=True)
class RowSummary:
    row_id: str
    scope_id: str
    key: NaturalKey
    declared_dimension: int
    actual_dimension: int
    vector_sha: str
    metadata_sha: str
    created_at: str
    updated_at: str

    def content_signature(self) -> tuple[str, int, str, str, str, str]:
        return (
            self.scope_id,
            self.actual_dimension,
            self.vector_sha,
            self.metadata_sha,
            self.created_at,
            self.updated_at,
        )


@dataclass
class Conflict:
    kind: str
    key: NaturalKey | None
    detail: str
    row_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "key": self.key.label() if self.key else "",
            "id": self.row_id,
            "detail": self.detail,
        }


@dataclass
class TableRead:
    rows: dict[NaturalKey, RowSummary] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)

    def count_by_space(self) -> dict[str, int]:
        counts = {space: 0 for space in TEXT_SPACES}
        for key in self.rows:
            counts[key.space] = counts.get(key.space, 0) + 1
        return counts


@dataclass
class OperationResult:
    command: str
    dry_run: bool
    source_rows: int = 0
    target_rows: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    checks: list[dict[str, object]] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def conflicts_of(self, kind: str) -> list[Conflict]:
        return [item for item in self.conflicts if item.kind == kind]

    def conflict_counts(self) -> dict[str, int]:
        counts = {kind: 0 for kind in CONFLICT_KINDS}
        for item in self.conflicts:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts


def _canonical_vector(values: Sequence[float]) -> str:
    return json.dumps([float(value) for value in values], separators=(",", ":"))


def _canonical_metadata(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decode_vector(raw: str) -> list[float] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list):
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _decode_metadata(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return dict(value) if isinstance(value, dict) else None


def summarize(row: sqlite3.Row) -> tuple[RowSummary | None, Conflict | None]:
    key = NaturalKey(
        space=row["space"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        model_name=row["model_name"],
    )
    vector = _decode_vector(row["vector_json"])
    metadata = _decode_metadata(row["metadata_json"])
    if vector is None:
        return None, Conflict("malformed", key, "vector_json 无法解码为浮点数组", row["id"])
    if metadata is None:
        return None, Conflict("malformed", key, "metadata_json 无法解码为对象", row["id"])
    summary = RowSummary(
        row_id=row["id"],
        scope_id=row["scope_id"],
        key=key,
        declared_dimension=int(row["dimension"]),
        actual_dimension=len(vector),
        vector_sha=_sha(_canonical_vector(vector)),
        metadata_sha=_sha(_canonical_metadata(metadata)),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
    if summary.declared_dimension != summary.actual_dimension:
        return summary, Conflict(
            "dimension_mismatch",
            key,
            f"dimension 列 {summary.declared_dimension} != 实际 {summary.actual_dimension}",
            row["id"],
        )
    return summary, None


def read_table(connection: sqlite3.Connection, table: str) -> TableRead:
    """读取一张表的文本空间行，只保留摘要（不把向量正文留在内存里）。"""
    read = TableRead()
    rows = connection.execute(
        f"SELECT {COLUMNS} FROM {table} WHERE {text_space_clause()} ORDER BY id",
        TEXT_SPACES,
    )
    for row in rows:
        summary, conflict = summarize(row)
        if conflict is not None:
            read.conflicts.append(conflict)
        if summary is not None:
            if summary.key in read.rows:
                read.conflicts.append(
                    Conflict("malformed", summary.key, "自然键重复（UNIQUE 约束应已阻止）", summary.row_id)
                )
            else:
                read.rows[summary.key] = summary
    return read


def compare_reads(
    source: TableRead,
    target: TableRead,
    *,
    source_label: str,
    target_label: str,
) -> list[Conflict]:
    """source 为基准，target 为待比对侧。``missing_in_new`` / ``extra_in_new`` 按 target 命名。"""
    conflicts: list[Conflict] = list(source.conflicts) + list(target.conflicts)
    source_ids = {summary.row_id: key for key, summary in source.rows.items()}
    target_ids = {summary.row_id: key for key, summary in target.rows.items()}
    for row_id, key in source_ids.items():
        if row_id in target_ids and target_ids[row_id] != key:
            conflicts.append(
                Conflict("id_mismatch", key, f"同一主键 {row_id} 指向 {target_label} 的另一个自然键", row_id)
            )
    for key, summary in source.rows.items():
        counterpart = target.rows.get(key)
        if counterpart is None:
            conflicts.append(Conflict("missing_in_new", key, f"{target_label} 缺少该行", summary.row_id))
            continue
        if counterpart.row_id != summary.row_id:
            conflicts.append(
                Conflict(
                    "id_mismatch",
                    key,
                    f"id {summary.row_id} != {counterpart.row_id}",
                    summary.row_id,
                )
            )
        if summary.actual_dimension != counterpart.actual_dimension:
            conflicts.append(
                Conflict(
                    "dimension_mismatch",
                    key,
                    f"维度 {summary.actual_dimension} != {counterpart.actual_dimension}",
                    summary.row_id,
                )
            )
        if summary.vector_sha != counterpart.vector_sha:
            conflicts.append(Conflict("vector_mismatch", key, "向量内容哈希不同", summary.row_id))
        if summary.metadata_sha != counterpart.metadata_sha:
            conflicts.append(Conflict("metadata_mismatch", key, "metadata 内容哈希不同", summary.row_id))
        if (summary.created_at, summary.updated_at) != (
            counterpart.created_at,
            counterpart.updated_at,
        ):
            conflicts.append(
                Conflict("timestamp_mismatch", key, "created_at/updated_at 不同", summary.row_id)
            )
    for key, summary in target.rows.items():
        if key not in source.rows:
            conflicts.append(Conflict("extra_in_new", key, f"{source_label} 缺少该行", summary.row_id))
    return conflicts


def verify(connection: sqlite3.Connection, *, direction: str = "forward") -> OperationResult:
    old = read_table(connection, OLD_TABLE)
    new = read_table(connection, NEW_TABLE)
    result = OperationResult(command="verify", dry_run=True)
    result.details["direction"] = direction
    result.source_rows = old.count
    result.target_rows = new.count
    result.details["old"] = {"table": OLD_TABLE, "rows": old.count, "by_space": old.count_by_space()}
    result.details["new"] = {"table": NEW_TABLE, "rows": new.count, "by_space": new.count_by_space()}
    if direction == "forward":
        conflicts = compare_reads(old, new, source_label=OLD_TABLE, target_label=NEW_TABLE)
    else:
        conflicts = compare_reads(new, old, source_label=NEW_TABLE, target_label=OLD_TABLE)
    result.conflicts = conflicts
    result.checks = [
        {"name": "row_count", "ok": old.count == new.count},
        {"name": "conflicts", "ok": not conflicts},
    ]
    return result


def _row_values(row: sqlite3.Row) -> tuple[Any, ...]:
    return tuple(row[column] for column in COLUMNS.split(", "))


def _fetch_row(connection: sqlite3.Connection, table: str, key: NaturalKey) -> sqlite3.Row | None:
    row = connection.execute(
        f"SELECT {COLUMNS} FROM {table} WHERE space = ? AND source_type = ?"
        " AND source_id = ? AND model_name = ?",
        (key.space, key.source_type, key.source_id, key.model_name),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def _update_row(connection: sqlite3.Connection, table: str, key: NaturalKey, row: sqlite3.Row) -> None:
    connection.execute(
        f"UPDATE {table} SET id = ?, scope_id = ?, dimension = ?, vector_json = ?,"
        " metadata_json = ?, created_at = ?, updated_at = ?"
        " WHERE space = ? AND source_type = ? AND source_id = ? AND model_name = ?",
        (
            row["id"],
            row["scope_id"],
            row["dimension"],
            row["vector_json"],
            row["metadata_json"],
            row["created_at"],
            row["updated_at"],
            key.space,
            key.source_type,
            key.source_id,
            key.model_name,
        ),
    )


def _insert_row(connection: sqlite3.Connection, table: str, row: sqlite3.Row) -> None:
    connection.execute(
        f"INSERT INTO {table} ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        _row_values(row),
    )


def _content_conflicts(source: RowSummary, target: RowSummary) -> list[Conflict]:
    conflicts: list[Conflict] = []
    if source.actual_dimension != target.actual_dimension:
        conflicts.append(
            Conflict(
                "dimension_mismatch",
                source.key,
                f"维度 {source.actual_dimension} != {target.actual_dimension}",
                source.row_id,
            )
        )
    if source.vector_sha != target.vector_sha:
        conflicts.append(Conflict("vector_mismatch", source.key, "向量内容哈希不同", source.row_id))
    if source.metadata_sha != target.metadata_sha:
        conflicts.append(Conflict("metadata_mismatch", source.key, "metadata 内容哈希不同", source.row_id))
    if (source.created_at, source.updated_at) != (target.created_at, target.updated_at):
        conflicts.append(
            Conflict("timestamp_mismatch", source.key, "created_at/updated_at 不同", source.row_id)
        )
    return conflicts


def _copy_plan(
    source: TableRead,
    target: TableRead,
    *,
    policy: str,
) -> tuple[list[NaturalKey], list[NaturalKey], list[NaturalKey], list[Conflict]]:
    inserts: list[NaturalKey] = []
    updates: list[NaturalKey] = []
    skips: list[NaturalKey] = []
    conflicts: list[Conflict] = list(source.conflicts) + list(target.conflicts)
    for key, summary in source.rows.items():
        counterpart = target.rows.get(key)
        if counterpart is None:
            inserts.append(key)
            continue
        if counterpart.row_id != summary.row_id:
            conflicts.append(
                Conflict("id_mismatch", key, f"id {summary.row_id} != {counterpart.row_id}", summary.row_id)
            )
            continue
        differences = _content_conflicts(summary, counterpart)
        if not differences:
            skips.append(key)
            continue
        if policy == "prefer-new":
            skips.append(key)
        elif policy == "prefer-old":
            updates.append(key)
        else:
            conflicts.extend(differences)
    return inserts, updates, skips, conflicts


def copy_rows(
    connection: sqlite3.Connection,
    *,
    apply: bool = False,
    policy: str = "abort",
) -> OperationResult:
    """old → new。保留原始 ``id`` 与时间戳；幂等。"""
    if policy not in CONFLICT_POLICIES:
        raise ValueError(f"未知冲突策略: {policy}")
    source = read_table(connection, OLD_TABLE)
    target = read_table(connection, NEW_TABLE)
    inserts, updates, skips, conflicts = _copy_plan(source, target, policy=policy)
    result = OperationResult(command="copy", dry_run=not apply)
    result.source_rows = source.count
    result.target_rows = target.count
    result.skipped = len(skips)
    result.conflicts = conflicts
    result.details["planned_inserts"] = len(inserts)
    result.details["planned_updates"] = len(updates)
    result.checks = [
        {"name": "row_count", "ok": source.count == target.count},
        {"name": "conflicts", "ok": not conflicts},
    ]
    if not apply or conflicts:
        return result

    with connection:
        for key in inserts:
            row = _fetch_row(connection, OLD_TABLE, key)
            if row is not None:
                _insert_row(connection, NEW_TABLE, row)
                result.inserted += 1
        for key in updates:
            row = _fetch_row(connection, OLD_TABLE, key)
            if row is not None:
                _update_row(connection, NEW_TABLE, key, row)
                result.updated += 1

    after = read_table(connection, NEW_TABLE)
    # copy 只要求 old ⊆ new；new 里多出的行是切换后正常新增的，不算冲突。
    post = [
        item
        for item in compare_reads(source, after, source_label=OLD_TABLE, target_label=NEW_TABLE)
        if item.kind != "extra_in_new"
    ]
    if post:
        result.conflicts.extend(post)
        result.checks.append({"name": "post_verify", "ok": False})
    else:
        result.checks.append({"name": "post_verify", "ok": True})
    return result


def reverse_rows(
    connection: sqlite3.Connection,
    *,
    apply: bool = False,
    policy: str = "abort",
    allow_empty_source: bool = False,
) -> OperationResult:
    """new → old 的反向同步：upsert 新行，并删除 old 里已不存在的文本行。"""
    if policy not in CONFLICT_POLICIES:
        raise ValueError(f"未知冲突策略: {policy}")
    source = read_table(connection, NEW_TABLE)
    target = read_table(connection, OLD_TABLE)
    result = OperationResult(command="reverse", dry_run=not apply)
    result.source_rows = source.count
    result.target_rows = target.count
    result.conflicts = list(source.conflicts) + list(target.conflicts)
    if source.count == 0 and target.count > 0 and not allow_empty_source:
        # 反向同步会删除 old 里「new 不存在」的行。new 为空而 old 有行，多半是
        # 复制根本没跑或跑错了库——这时执行等于把旧数据清空。
        result.conflicts.append(
            Conflict(
                "malformed",
                None,
                "text_vectors 为空而 memory_vectors 有文本行；拒绝反向同步"
                "（确认无误后加 --allow-empty-source）",
            )
        )
        result.details["refused"] = "empty source"
        result.checks = [{"name": "source_not_empty", "ok": False}]
        return result

    inserts: list[NaturalKey] = []
    updates: list[NaturalKey] = []
    for key, summary in source.rows.items():
        counterpart = target.rows.get(key)
        if counterpart is None:
            inserts.append(key)
            continue
        if counterpart.row_id != summary.row_id:
            result.conflicts.append(
                Conflict("id_mismatch", key, f"id {summary.row_id} != {counterpart.row_id}", summary.row_id)
            )
            continue
        differences = _content_conflicts(summary, counterpart)
        if not differences:
            result.skipped += 1
            continue
        if policy == "prefer-new":
            updates.append(key)
        elif policy == "prefer-old":
            result.skipped += 1
        else:
            result.conflicts.extend(differences)
    deletes = [key for key in target.rows if key not in source.rows]
    result.details["planned_inserts"] = len(inserts)
    result.details["planned_updates"] = len(updates)
    result.details["planned_deletes"] = len(deletes)
    result.checks = [{"name": "conflicts", "ok": not result.conflicts}]
    if not apply or result.conflicts:
        return result

    with connection:
        for key in inserts:
            row = _fetch_row(connection, NEW_TABLE, key)
            if row is not None:
                _insert_row(connection, OLD_TABLE, row)
                result.inserted += 1
        for key in updates:
            row = _fetch_row(connection, NEW_TABLE, key)
            if row is not None:
                _update_row(connection, OLD_TABLE, key, row)
                result.updated += 1
        for key in deletes:
            summary = target.rows[key]
            connection.execute(f"DELETE FROM {OLD_TABLE} WHERE id = ?", (summary.row_id,))
            result.deleted += 1

    after = read_table(connection, OLD_TABLE)
    post = compare_reads(source, after, source_label=NEW_TABLE, target_label=OLD_TABLE)
    if post:
        result.conflicts.extend(post)
        result.checks.append({"name": "post_verify", "ok": False})
    else:
        result.checks.append({"name": "post_verify", "ok": True})
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(db_path: Path, backup_path: Path) -> dict[str, object]:
    """用 ``Connection.backup`` 生成一致性快照；WAL 模式下直接拷文件不安全。"""
    if backup_path.exists():
        raise FileExistsError(f"备份文件已存在，拒绝覆盖: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
        finally:
            destination.close()
    finally:
        source.close()
    if integrity is None or integrity[0] != "ok":
        raise RuntimeError(f"备份完整性检查失败: {integrity!r}")
    digest = sha256_file(backup_path)
    sidecar = backup_path.with_name(backup_path.name + ".sha256")
    sidecar.write_text(f"{digest}  {backup_path.name}\n", encoding="utf-8")
    return {
        "path": str(backup_path),
        "sha256": digest,
        "sha256_path": str(sidecar),
        "integrity_check": integrity[0],
        "bytes": backup_path.stat().st_size,
    }


def open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def purge_rows(
    connection: sqlite3.Connection,
    *,
    db_path: Path,
    backup_path: Path | None,
    apply: bool = False,
) -> OperationResult:
    """删除 ``memory_vectors`` 的文本空间行。前置校验不通过时拒绝执行。"""
    old = read_table(connection, OLD_TABLE)
    new = read_table(connection, NEW_TABLE)
    result = OperationResult(command="purge", dry_run=not apply)
    result.source_rows = old.count
    result.target_rows = new.count
    result.details["planned_deletes"] = old.count

    pre = compare_reads(old, new, source_label=OLD_TABLE, target_label=NEW_TABLE)
    result.conflicts = pre
    result.checks = [
        {"name": "pre_verify", "ok": not pre},
        {"name": "backup_present", "ok": backup_path is not None},
    ]
    if pre:
        result.details["refused"] = "前置校验未通过（存在冲突），拒绝 purge"
        return result
    if apply and backup_path is None:
        result.conflicts.append(Conflict("malformed", None, "purge --apply 必须提供 --backup-path"))
        result.details["refused"] = "缺少备份路径"
        return result
    if not apply:
        return result
    if backup_path is None:
        result.conflicts.append(Conflict("malformed", None, "缺少备份路径"))
        result.details["refused"] = "缺少备份路径"
        return result

    backup = create_backup(db_path, backup_path)
    result.details["backup"] = backup
    backup_connection = open_readonly(backup_path)
    try:
        backup_read = read_table(backup_connection, OLD_TABLE)
    finally:
        backup_connection.close()
    backup_diff = compare_reads(old, backup_read, source_label=OLD_TABLE, target_label="backup")
    if backup_diff:
        result.conflicts.extend(backup_diff)
        result.details["refused"] = "备份内容与源库快照不一致，拒绝 purge"
        return result

    with connection:
        cursor = connection.execute(f"DELETE FROM {OLD_TABLE} WHERE {text_space_clause()}", TEXT_SPACES)
        result.deleted = int(cursor.rowcount)
    remaining = read_table(connection, OLD_TABLE)
    result.checks.append({"name": "old_space_empty", "ok": remaining.count == 0})
    if remaining.count:
        result.conflicts.append(Conflict("malformed", None, f"purge 后仍剩 {remaining.count} 行文本向量"))
    return result


def restore_old_rows(
    connection: sqlite3.Connection,
    *,
    backup_path: Path,
    apply: bool = False,
    policy: str = "abort",
) -> OperationResult:
    """从备份库把文本空间行 upsert 回 ``memory_vectors``（purge 后的恢复第一步）。"""
    if policy not in CONFLICT_POLICIES:
        raise ValueError(f"未知冲突策略: {policy}")
    backup_connection = open_readonly(backup_path)
    try:
        backup = read_table(backup_connection, OLD_TABLE)
    finally:
        backup_connection.close()
    current = read_table(connection, OLD_TABLE)
    result = OperationResult(command="restore-old", dry_run=not apply)
    result.source_rows = backup.count
    result.target_rows = current.count
    result.conflicts = list(backup.conflicts) + list(current.conflicts)

    inserts: list[NaturalKey] = []
    updates: list[NaturalKey] = []
    for key, summary in backup.rows.items():
        counterpart = current.rows.get(key)
        if counterpart is None:
            inserts.append(key)
            continue
        if counterpart.row_id != summary.row_id:
            result.conflicts.append(
                Conflict("id_mismatch", key, f"id {summary.row_id} != {counterpart.row_id}", summary.row_id)
            )
            continue
        differences = _content_conflicts(summary, counterpart)
        if not differences or policy == "prefer-new":
            result.skipped += 1
        elif policy == "prefer-old":
            updates.append(key)
        else:
            result.conflicts.extend(differences)
    result.details["planned_inserts"] = len(inserts)
    result.details["planned_updates"] = len(updates)
    result.checks = [{"name": "conflicts", "ok": not result.conflicts}]
    if not apply or result.conflicts:
        return result

    backup_connection = open_readonly(backup_path)
    try:
        with connection:
            for key in inserts:
                row = _fetch_row(backup_connection, OLD_TABLE, key)
                if row is not None:
                    _insert_row(connection, OLD_TABLE, row)
                    result.inserted += 1
            for key in updates:
                row = _fetch_row(backup_connection, OLD_TABLE, key)
                if row is not None:
                    _update_row(connection, OLD_TABLE, key, row)
                    result.updated += 1
    finally:
        backup_connection.close()
    return result


def operation_report(
    result: OperationResult,
    *,
    db_path: Path,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    conflicts: dict[str, list[dict[str, str]]] = {}
    for kind in CONFLICT_KINDS:
        items = result.conflicts_of(kind)
        conflicts[kind] = [item.as_dict() for item in items[:MAX_REPORTED_CONFLICTS]]
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "command": result.command,
        "db": str(db_path),
        "dry_run": result.dry_run,
        "result": "ok" if result.ok else "conflict",
        "source_rows": result.source_rows,
        "target_rows": result.target_rows,
        "counts": {
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": result.skipped,
            "deleted": result.deleted,
            "conflict_total": len(result.conflicts),
        },
        "conflict_counts": result.conflict_counts(),
        "conflicts": conflicts,
        "conflicts_truncated": {
            kind: len(result.conflicts_of(kind)) > MAX_REPORTED_CONFLICTS for kind in CONFLICT_KINDS
        },
        "checks": result.checks,
        "details": result.details,
    }
    if extra:
        report.update(extra)
    return report


def write_report(report_dir: Path, report: Mapping[str, object]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    command = str(report.get("command", "report"))
    path = report_dir / f"{stamp}-{command}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

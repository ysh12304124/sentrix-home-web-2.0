#!/usr/bin/env python3
"""文本向量拆分的维护入口：复制、校验、反向同步、purge 与恢复。

默认是 dry-run；任何写操作都要显式 ``--apply``。每次运行都会把审计 JSON 落到
``--report-dir``（默认 ``data/reports/text-vectors/``）。

用法::

    # 看一眼会复制多少行
    .venv/bin/python scripts/maintenance/text_vectors_migrate.py copy

    # 真正复制（先停 API）
    .venv/bin/python scripts/maintenance/text_vectors_migrate.py copy --apply

    # 全量校验（行数 / 自然键 / 主键 / 维度 / 内容哈希 / 时间戳）
    .venv/bin/python scripts/maintenance/text_vectors_migrate.py verify --direction forward

    # 切换后回滚：new → old
    .venv/bin/python scripts/maintenance/text_vectors_migrate.py reverse --apply

    # 稳定后清理旧行（自动备份；purge 后只切 Container 回不去）
    .venv/bin/python scripts/maintenance/text_vectors_migrate.py purge --apply \
        --backup-path data/backups/sentrix-pre-text-purge.db

    # purge 后的恢复第一步：从备份把旧行放回去，再跑一次 reverse 补增量
    .venv/bin/python scripts/maintenance/text_vectors_migrate.py restore-old --apply \
        --backup-path data/backups/sentrix-pre-text-purge.db
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from scripts.maintenance.text_vector_migration import (  # noqa: E402
    CONFLICT_POLICIES,
    OperationResult,
    copy_rows,
    operation_report,
    purge_rows,
    restore_old_rows,
    reverse_rows,
    verify,
    write_report,
)
from sentrix.config import load_settings  # noqa: E402
from sentrix.infrastructure.persistence.database import Database  # noqa: E402

MIGRATIONS_DIR = (
    REPO_ROOT / "apps" / "api" / "src" / "sentrix" / "infrastructure" / "persistence" / "migrations"
)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=None, help="目标 SQLite；默认取 Settings.database_path")
    common.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="审计 JSON 目录；默认 data/reports/text-vectors/",
    )
    common.add_argument("--json", action="store_true", help="同时把报告打印到 stdout")
    write = common.add_mutually_exclusive_group()
    write.add_argument("--apply", action="store_true", help="真正写库（默认只演练）")
    write.add_argument("--dry-run", action="store_true", help="显式演练（默认行为）")
    common.add_argument(
        "--conflict",
        choices=CONFLICT_POLICIES,
        default="abort",
        help="内容冲突策略；id 冲突永远中止",
    )

    parser = argparse.ArgumentParser(description="text_vectors 迁移与维护")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("copy", parents=[common], help="memory_vectors → text_vectors")
    verify_parser = subparsers.add_parser("verify", parents=[common], help="全量比对")
    verify_parser.add_argument(
        "--direction",
        choices=("forward", "reverse"),
        default="forward",
        help="forward = old 为基准；reverse = new 为基准",
    )
    reverse_parser = subparsers.add_parser(
        "reverse", parents=[common], help="text_vectors → memory_vectors（回滚）"
    )
    reverse_parser.add_argument(
        "--allow-empty-source",
        action="store_true",
        help="允许 new 为空时反向同步（会删除全部旧文本行，危险）",
    )
    purge_parser = subparsers.add_parser(
        "purge", parents=[common], help="删除 memory_vectors 的文本行（先备份）"
    )
    purge_parser.add_argument("--backup-path", type=Path, default=None, help="--apply 时必填")
    restore_parser = subparsers.add_parser(
        "restore-old", parents=[common], help="从备份把文本行恢复到 memory_vectors"
    )
    restore_parser.add_argument("--backup-path", type=Path, required=True)
    return parser


def run(args: argparse.Namespace, database: Database, db_path: Path) -> OperationResult:
    with database.connect() as connection:
        if args.command == "copy":
            return copy_rows(connection, apply=args.apply, policy=args.conflict)
        if args.command == "verify":
            return verify(connection, direction=args.direction)
        if args.command == "reverse":
            return reverse_rows(
                connection,
                apply=args.apply,
                policy=args.conflict,
                allow_empty_source=args.allow_empty_source,
            )
        if args.command == "purge":
            if args.apply and args.backup_path is None:
                raise SystemExit("purge --apply 必须提供 --backup-path")
            return purge_rows(
                connection,
                db_path=db_path,
                backup_path=args.backup_path,
                apply=args.apply,
            )
        if args.command == "restore-old":
            return restore_old_rows(
                connection,
                backup_path=args.backup_path,
                apply=args.apply,
                policy=args.conflict,
            )
    raise SystemExit(f"未知子命令: {args.command}")


def summarize(result: OperationResult) -> str:
    return (
        f"{result.command}: {'dry-run' if result.dry_run else 'apply'}  "
        f"old={result.source_rows} new={result.target_rows}  "
        f"inserted={result.inserted} updated={result.updated} "
        f"skipped={result.skipped} deleted={result.deleted}  "
        f"conflicts={len(result.conflicts)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    db_path = Path(args.db) if args.db else settings.database_path
    report_dir = Path(args.report_dir) if args.report_dir else settings.data_dir / "reports" / "text-vectors"

    database = Database(db_path, MIGRATIONS_DIR)
    database.migrate()

    result = run(args, database, db_path)
    extra: dict[str, object] = {}
    if args.command == "purge":
        extra["warning"] = (
            "purge 之后仅把 Container 切回 memory_vectors 无法回滚：旧行已删除。"
            "恢复必须先 restore-old 从备份放回，再 reverse 补齐备份之后的增量。"
        )
    report = operation_report(result, db_path=db_path, extra=extra)
    report_path = write_report(report_dir, report)

    print(summarize(result))
    counts = result.conflict_counts()
    if result.conflicts:
        for kind, count in counts.items():
            if count:
                print(f"  {kind}: {count}")
    print(f"审计报告: {report_path}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

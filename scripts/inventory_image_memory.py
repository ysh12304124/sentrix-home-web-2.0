#!/usr/bin/env python3
"""只读盘点图片记忆状态；不调用模型、不写库。

用法：
    .venv/bin/python scripts/inventory_image_memory.py [--scope album1] [--limit 500]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.media.manage_memory_builds import ManageMemoryBuilds
from sentrix.config import Settings
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_builds import (
    SqliteMemoryBuildRepository,
    SqliteMemoryBuildTaskRepository,
)
from sentrix.infrastructure.persistence.repositories.memory_evidence import (
    SqliteMemoryEvidenceRepository,
)

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1] / "apps/api/src/sentrix/infrastructure/persistence/migrations"
)


def _scopes(database: Database) -> list[str]:
    with database.connect() as connection:
        return [str(row[0]) for row in connection.execute("SELECT id FROM memory_spaces ORDER BY id")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--database", default="")
    args = parser.parse_args()

    settings = Settings()
    database = Database(Path(args.database) if args.database else settings.database_path, MIGRATIONS_DIR)
    use_case = ManageMemoryBuilds(
        assets=SqliteAssetRepository(database),
        observations=SqliteObservationRepository(database),
        builds=SqliteMemoryBuildRepository(database),
        evidence=SqliteMemoryEvidenceRepository(database),
        tasks=SqliteMemoryBuildTaskRepository(database),
    )
    scope_ids = [args.scope] if args.scope else _scopes(database)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": str(database.path),
        "scopes": [
            {
                "scope_id": scope_id,
                **asdict(use_case.inventory(scope_id, args.limit)),
            }
            for scope_id in scope_ids
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

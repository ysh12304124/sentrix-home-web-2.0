"""给已处理但缺少视觉向量的图片补嵌入。

覆盖两类历史缺口：导入时侧车不可用，以及视频关键帧的视觉索引漏写。只处理
``media_type='image'``、``status='processed'`` 且当前方案没有 ``memory_vectors`` 行的资产。
默认 dry-run；``--apply`` 才写并先备份整库。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.retrieval.index_visual import SOURCE_TYPE, IndexAssetVisual
from sentrix.bootstrap.container import Container
from sentrix.config import load_settings
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    container = Container.build(settings)
    database = settings.data_dir / "sentrix.db"
    if not database.is_file():
        print(f"missing database: {database}", file=sys.stderr)
        return 1
    indexer = IndexAssetVisual(
        embedder=container.visual_embedder,
        vectors=container.vector_repository,
        index=container.vector_index,
    )
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT a.id FROM assets a"
        " LEFT JOIN memory_vectors v ON v.source_id = a.id"
        "  AND v.space = 'visual'"
        "  AND v.model_name = ?"
        "  AND v.dimension = ?"
        " WHERE a.media_type = 'image' AND a.status = 'processed' AND v.id IS NULL",
        (container.visual_embedder.model_id, container.visual_embedder.dimension),
    ).fetchall()
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: missing={len(rows)}")
    if not args.apply or not rows:
        connection.close()
        return 0
    backup_dir = BACKUP_ROOT / f"visual-vectors-backfill-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(backup_dir / f"{database.parent.parent.name}.db")
    connection.backup(destination)
    destination.close()
    done = failed = 0
    for row in rows:
        asset = container.asset_repository.get(str(row["id"]))
        if asset is None:
            continue
        try:
            outcome = indexer.index(asset)
            if outcome.indexed:
                done += 1
                continue
            # 手机 HEIC 原图侧车可能解不了；浏览器预览已经是同画面的 JPEG，
            # 用它补视觉向量不会让检索到的东西变样。
            preview = settings.preview_dir / f"{asset.id}-1600.jpg"
            if outcome.reason == "embedding_unavailable" and preview.is_file():
                vector = container.visual_embedder.embed_image(preview)
                if vector and len(vector) == container.visual_embedder.dimension:
                    record = VectorRecord(
                        scope_id=asset.scope_id,
                        space=scheme.VISUAL_SPACE,
                        source_type=SOURCE_TYPE,
                        source_id=asset.id,
                        model_name=container.visual_embedder.model_id,
                        vector=tuple(float(value) for value in vector),
                        metadata={"file_name": asset.file_name, "embedding_source": "preview"},
                    )
                    container.vector_repository.upsert(record)
                    container.vector_index.upsert([record])
                    done += 1
                    continue
            failed += 1
            print(f"skip {asset.id}: {outcome.reason}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - 单张失败不阻断其余
            failed += 1
            print(f"fail {asset.id}: {error}", file=sys.stderr)
    connection.close()
    container.vector_index.close()
    print(f"DONE: indexed={done} failed={failed} backup={backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

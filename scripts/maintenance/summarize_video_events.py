"""给已有视频事件生成事件级标题与摘要。

视频事件此前直接拿文件名当标题、把逐帧描述拼成摘要。批次后处理里的
``SummarizeEvent`` 本来就会综合全部观察生成标题/摘要；这个脚本对历史视频事件补跑一次。
默认 dry-run，``--apply`` 才写，并在写前备份整库。
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.memories.summarize_event import SummarizeEvent
from sentrix.bootstrap.container import Container
from sentrix.config import load_settings

DEMO_ROOT = Path("/home/asus/sentrix-demo")
BACKUP_ROOT = DEMO_ROOT / "backups"

log = logging.getLogger("sentrix.maintenance.summarize_video_events")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    settings = load_settings()
    container = Container.build(settings)
    container.database.migrate()
    database = settings.data_dir / "sentrix.db"
    if not database.is_file():
        print(f"missing database: {database}", file=sys.stderr)
        return 1

    events = [
        event
        for event in container.event_repository.list_by_scope(None, limit=100000)
        if event.source_type == "video_scene"
        and (
            event.title in {"", "待总结事件"}
            or event.title.startswith("视频 ·")
            or event.summary.startswith(("视频场景", "视频："))
        )
    ]
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: video_events={len(events)}")
    if not args.apply or not events:
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"video-summary-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(backup_dir / f"{database.parent.parent.name}.db")
    source = sqlite3.connect(database)
    source.backup(destination)
    source.close()
    destination.close()

    summarizer = SummarizeEvent(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        participants=container.event_participant_repository,
        model=container.chat_model,
    )

    done = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(summarizer.execute, event.id): event for event in events}
        for future in as_completed(futures):
            done += 1
            try:
                updated = future.result()
                print(f"[{done}/{len(events)}] {updated.title} :: {updated.summary[:80]}")
            except Exception as error:  # noqa: BLE001 - 单条失败不阻断其余
                failed += 1
                log.warning("视频事件总结失败 event=%s: %s", futures[future].id, error)
    print(f"DONE: done={done} failed={failed} backup={backup_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

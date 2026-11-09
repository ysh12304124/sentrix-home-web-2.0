"""启动对账与临时文件清理。

顺序固定：先对账（清掉崩溃留下的上传预留），再清理超过宽限期且无人引用的
staging 临时文件与媒体目录孤儿件。已确认接收的原件绝不因进程重启而删除。
"""

from __future__ import annotations

import time
from pathlib import Path

from sentrix.domain.media.import_jobs import ImportJobRepository


def reconcile_uploads(
    *,
    media_dir: Path,
    staging_dir: Path,
    jobs: ImportJobRepository,
    grace_s: int,
) -> dict[str, int]:
    reservations = jobs.clear_reservations()
    referenced = jobs.referenced_paths()
    cutoff = time.time() - grace_s
    removed_staging = 0
    removed_orphans = 0

    if staging_dir.is_dir():
        for path in staging_dir.glob("*.part"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed_staging += 1
            except OSError:
                continue

    if media_dir.is_dir():
        for path in media_dir.glob("asset_*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                if str(path) in referenced or path.stat().st_mtime >= cutoff:
                    continue
                path.unlink(missing_ok=True)
                removed_orphans += 1
            except OSError:
                continue

    return {
        "reservations_cleared": reservations,
        "staging_removed": removed_staging,
        "orphans_removed": removed_orphans,
    }

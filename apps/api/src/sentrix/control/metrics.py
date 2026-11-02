"""控制服务自身的轻量运行指标。

10 秒采样、nvidia-smi 与 systemd 归属在任务 10 扩展；这里只保留不会拖慢
登录/转发路径的进程级快照，并把不可读取的项标成 ``None`` 而不是 0。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import psutil


@dataclass(frozen=True)
class HostSnapshot:
    sampled_at: str
    process_rss_mb: float | None
    process_cpu_percent: float | None
    load_1m: float | None
    disk_free_gb: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def collect_host_snapshot() -> HostSnapshot:
    process = psutil.Process()
    load_1m: float | None
    try:
        load_1m = psutil.getloadavg()[0]
    except (AttributeError, OSError):
        load_1m = None
    try:
        disk_free_gb: float | None = psutil.disk_usage("/").free / (1024**3)
    except OSError:
        disk_free_gb = None
    try:
        rss_mb: float | None = process.memory_info().rss / (1024**2)
    except psutil.Error:
        rss_mb = None
    try:
        cpu_percent: float | None = process.cpu_percent(interval=None)
    except psutil.Error:
        cpu_percent = None
    return HostSnapshot(
        sampled_at=datetime.now(UTC).isoformat(),
        process_rss_mb=round(rss_mb, 1) if rss_mb is not None else None,
        process_cpu_percent=cpu_percent,
        load_1m=round(load_1m, 2) if load_1m is not None else None,
        disk_free_gb=round(disk_free_gb, 1) if disk_free_gb is not None else None,
    )

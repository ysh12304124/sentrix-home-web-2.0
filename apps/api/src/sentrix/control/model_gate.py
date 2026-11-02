"""共享主模型准入的配置与槽位口径。

真正的排队/转发在任务 3 实现；这里固定“槽位数来自实测运行值”与可公开的元数据，
避免把当前 2 个槽位当成永久常量散落在各处。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelTarget:
    upstream_url: str
    model_id: str
    slots: int

    def __post_init__(self) -> None:
        if not self.upstream_url.rstrip("/"):
            raise ValueError("model upstream url is required")
        if self.slots < 1:
            raise ValueError("model slots must be at least 1")

    def describe(self) -> dict[str, object]:
        from urllib.parse import urlsplit

        parts = urlsplit(self.upstream_url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return {
            "upstream": f"{parts.scheme}://{host}{port}{parts.path.rstrip('/')}",
            "model_id": self.model_id,
            "slots": self.slots,
        }


class ModelGate:
    """准入状态；任务 3 在这里加入优先级队列与流式占用/释放。"""

    def __init__(self, target: ModelTarget) -> None:
        self.target = target

    def waiting(self) -> dict[str, int]:
        return {"chat": 0, "import": 0, "benchmark": 0}

    def describe(self) -> dict[str, object]:
        return {**self.target.describe(), "waiting": self.waiting()}

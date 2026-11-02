"""共享主模型准入：槽位、优先级与同级用户轮转。

槽位数来自主模型实际 ``--parallel`` 的运行值（153 当前 2），不写死为常量。
等待队列只在内存里；控制服务重启后由用户 API 的持久作业/回合重新发起。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

KIND_CHAT = "chat"
KIND_IMPORT = "import"
KIND_BENCHMARK = "benchmark"
KINDS = (KIND_CHAT, KIND_IMPORT, KIND_BENCHMARK)

# 导入等满 120 秒后，每放行 4 个新问答就放行 1 个最老导入，避免离线作业饿死。
IMPORT_STARVATION_S = 120.0
CHAT_ADMISSIONS_PER_IMPORT = 4


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


@dataclass(frozen=True)
class ModelClient:
    """一个持有模型入口令牌的进程身份。"""

    user_id: str
    kind: str  # user | benchmark


class ModelClientTable:
    """令牌 → 进程身份。文件必须 0600；令牌不进日志或浏览器响应。"""

    def __init__(self, clients: dict[str, ModelClient], error: str = "") -> None:
        self._clients = clients
        self._error = error

    @classmethod
    def load(cls, path: Path) -> ModelClientTable:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return cls({}, f"model clients unavailable: {error}")
        entries = document.get("clients") if isinstance(document, dict) else None
        if not isinstance(entries, dict):
            return cls({}, "model clients unavailable: missing clients object")
        clients: dict[str, ModelClient] = {}
        for token, value in entries.items():
            if not isinstance(value, dict):
                return cls({}, "model clients invalid")
            kind = str(value.get("kind") or "user")
            user_id = str(value.get("user_id") or "")
            if kind not in {"user", "benchmark"} or not user_id:
                return cls({}, f"model client invalid for {user_id or '(unknown)'}")
            clients[str(token)] = ModelClient(user_id=user_id, kind=kind)
        return cls(clients)

    def resolve(self, token: str) -> ModelClient | None:
        if not token:
            return None
        return self._clients.get(token)

    @property
    def error(self) -> str:
        return self._error

    @property
    def ready(self) -> bool:
        return not self._error


@dataclass
class _Waiter:
    kind: str
    user_id: str
    seq: int
    enqueued_at: float


@dataclass
class ModelGate:
    target: ModelTarget
    _active: int = 0
    _seq: int = 0
    _waiters: list[_Waiter] = field(default_factory=list)
    _active_by_user: dict[str, int] = field(default_factory=dict)
    _last_admitted: dict[str, float] = field(default_factory=dict)
    _chat_since_import: int = 0
    _last_ok_at: float = 0.0
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def __post_init__(self) -> None:
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def lease(self, kind: str, user_id: str) -> AsyncIterator[None]:
        if kind not in KINDS:
            raise ValueError(f"unknown work kind: {kind}")
        waiter = _Waiter(kind=kind, user_id=user_id, seq=0, enqueued_at=0.0)
        admitted = False
        try:
            async with self._condition:
                self._seq += 1
                waiter.seq = self._seq
                waiter.enqueued_at = time.monotonic()
                self._waiters.append(waiter)
                # 新到达者可能改变优先级判定（例如导入等满 120 秒后的 4:1 规则），
                # 让已有等待者重新判定一次。
                self._condition.notify_all()
                while not self._can_admit(waiter):
                    await self._condition.wait()
                self._waiters.remove(waiter)
                self._active += 1
                self._active_by_user[waiter.user_id] = (
                    self._active_by_user.get(waiter.user_id, 0) + 1
                )
                self._note_admitted(waiter)
                admitted = True
                # 还有空槽时让下一个等待者立刻重新判定，否则它要等下一次释放才被唤醒。
                self._condition.notify_all()
            yield
        finally:
            if not admitted:
                async with self._condition:
                    if waiter in self._waiters:
                        self._waiters.remove(waiter)
                    self._condition.notify_all()
                return
            async with self._condition:
                self._active -= 1
                remaining = self._active_by_user.get(waiter.user_id, 0) - 1
                if remaining > 0:
                    self._active_by_user[waiter.user_id] = remaining
                else:
                    self._active_by_user.pop(waiter.user_id, None)
                self._condition.notify_all()

    def _can_admit(self, waiter: _Waiter) -> bool:
        if self._active >= self.target.slots:
            return False
        return self._pick() is waiter

    def _pick(self) -> _Waiter | None:
        if not self._waiters:
            return None
        now = time.monotonic()
        chats = [item for item in self._waiters if item.kind == KIND_CHAT]
        imports = [item for item in self._waiters if item.kind == KIND_IMPORT]
        benchmarks = [item for item in self._waiters if item.kind == KIND_BENCHMARK]
        if chats:
            oldest_import = min(imports, key=lambda item: item.seq) if imports else None
            if (
                oldest_import is not None
                and now - oldest_import.enqueued_at >= IMPORT_STARVATION_S
                and self._chat_since_import >= CHAT_ADMISSIONS_PER_IMPORT
            ):
                return oldest_import
            return self._round_robin(chats)
        if imports:
            return self._round_robin(imports)
        if benchmarks:
            return self._round_robin(benchmarks)
        return None

    def _round_robin(self, waiters: list[_Waiter]) -> _Waiter:
        return min(
            waiters,
            key=lambda item: (self._last_admitted.get(item.user_id, 0.0), item.seq),
        )

    def _note_admitted(self, waiter: _Waiter) -> None:
        now = time.monotonic()
        self._last_admitted[waiter.user_id] = now
        if waiter.kind == KIND_CHAT:
            self._chat_since_import += 1
        elif waiter.kind == KIND_IMPORT:
            self._chat_since_import = 0

    def mark_ok(self) -> None:
        self._last_ok_at = time.time()

    def waiting(self) -> dict[str, int]:
        counts = {kind: 0 for kind in KINDS}
        for item in self._waiters:
            counts[item.kind] += 1
        return counts

    def describe(self) -> dict[str, object]:
        now = time.monotonic()
        oldest = min((item.enqueued_at for item in self._waiters), default=0.0)
        waiting_by_user: dict[str, dict[str, int]] = {}
        for item in self._waiters:
            counts = waiting_by_user.setdefault(item.user_id, {})
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return {
            **self.target.describe(),
            "active": self._active,
            "waiting": self.waiting(),
            "waiting_total": len(self._waiters),
            "active_by_user": dict(self._active_by_user),
            "waiting_by_user": waiting_by_user,
            "oldest_wait_s": round(now - oldest, 1) if oldest else 0.0,
            "last_ok_at": self._last_ok_at or None,
        }

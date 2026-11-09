"""共享主模型准入：槽位、优先级与按活跃用户均分。

槽位数来自主模型实际 ``--parallel`` 的运行值（153 当前 4），不写死为常量。
问答先锁定自己需要的槽位，剩余槽位按当前活跃/等待的导入用户数均分；没有任何用户
固定占几个槽。等待队列只在内存里；控制服务重启后由用户 API 的持久作业/回合重新发起。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

KIND_CHAT = "chat"
KIND_IMPORT = "import"
KIND_BENCHMARK = "benchmark"
KINDS = (KIND_CHAT, KIND_IMPORT, KIND_BENCHMARK)


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
    _active_by_kind: dict[str, int] = field(default_factory=dict)
    # 每个用户、每种工作的活跃数。公平分配看的是这一格，不是跨类型合计。
    _active_by_user_kind: dict[tuple[str, str], int] = field(default_factory=dict)
    _last_admitted: dict[tuple[str, str], float] = field(default_factory=dict)
    _last_ok_at: float = 0.0
    _last_error_at: float = 0.0
    _last_error: str = ""
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
                self._active_by_kind[waiter.kind] = (
                    self._active_by_kind.get(waiter.kind, 0) + 1
                )
                user_kind = (waiter.user_id, waiter.kind)
                self._active_by_user_kind[user_kind] = (
                    self._active_by_user_kind.get(user_kind, 0) + 1
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
                remaining_kind = self._active_by_kind.get(waiter.kind, 0) - 1
                remaining_kind = self._active_by_kind.get(waiter.kind, 0) - 1
                if remaining_kind > 0:
                    self._active_by_kind[waiter.kind] = remaining_kind
                else:
                    self._active_by_kind.pop(waiter.kind, None)
                user_kind = (waiter.user_id, waiter.kind)
                remaining_user_kind = self._active_by_user_kind.get(user_kind, 0) - 1
                if remaining_user_kind > 0:
                    self._active_by_user_kind[user_kind] = remaining_user_kind
                else:
                    self._active_by_user_kind.pop(user_kind, None)
                self._condition.notify_all()

    def _can_admit(self, waiter: _Waiter) -> bool:
        if self._active >= self.target.slots:
            return False
        budget = self._budget(waiter.kind)
        if budget <= 0:
            return False
        if self._active_by_kind.get(waiter.kind, 0) >= budget:
            return False
        if self._active_by_user_kind.get((waiter.user_id, waiter.kind), 0) >= self._fair_cap(
            waiter.kind, budget
        ):
            return False
        return self._pick() is waiter

    def _chat_demand(self) -> int:
        """正在占槽的问答 + 正在等槽的问答。导入必须为这些需求让路。"""
        active = self._active_by_kind.get(KIND_CHAT, 0)
        waiting = sum(1 for item in self._waiters if item.kind == KIND_CHAT)
        return active + waiting

    def _budget(self, kind: str) -> int:
        slots = self.target.slots
        if kind != KIND_IMPORT:
            return slots
        # 问答优先：每个活跃/等待的问答请求先锁定一个槽位，剩下的才分给导入。
        # 正在上游跑的调用无法强杀，所以新问答最多等一次现有导入请求结束。
        return max(0, slots - min(slots, self._chat_demand()))

    def _users_for_kind(self, kind: str) -> set[str]:
        users = {
            user_id
            for (user_id, item_kind), count in self._active_by_user_kind.items()
            if item_kind == kind and count > 0
        }
        users.update(item.user_id for item in self._waiters if item.kind == kind)
        return users

    def _fair_cap(self, kind: str, budget: int) -> int:
        """按当前活跃/等待该类型工作的用户数均分；一个用户不能固定多占。"""
        users = self._users_for_kind(kind)
        if not users:
            return max(1, budget)
        return max(1, math.ceil(budget / len(users)))

    def _eligible(self, kind: str, budget: int) -> list[_Waiter]:
        if budget <= 0:
            return []
        if self._active_by_kind.get(kind, 0) >= budget:
            return []
        cap = self._fair_cap(kind, budget)
        return [
            item
            for item in self._waiters
            if item.kind == kind
            and self._active_by_user_kind.get((item.user_id, kind), 0) < cap
        ]

    def _pick(self) -> _Waiter | None:
        if not self._waiters:
            return None
        chats = self._eligible(KIND_CHAT, self._budget(KIND_CHAT))
        imports = self._eligible(KIND_IMPORT, self._budget(KIND_IMPORT))
        benchmarks = self._eligible(KIND_BENCHMARK, self._budget(KIND_BENCHMARK))
        # 同级用户轮转；跨类型严格按 问答 > 导入 > 评测。
        if chats:
            return self._round_robin(chats)
        if imports:
            return self._round_robin(imports)
        if benchmarks:
            return self._round_robin(benchmarks)
        return None

    def _round_robin(self, waiters: list[_Waiter]) -> _Waiter:
        return min(
            waiters,
            key=lambda item: (self._last_admitted.get((item.kind, item.user_id), 0.0), item.seq),
        )

    def _note_admitted(self, waiter: _Waiter) -> None:
        self._last_admitted[(waiter.kind, waiter.user_id)] = time.monotonic()

    def mark_ok(self) -> None:
        self._last_ok_at = time.time()
        self._last_error = ""

    def mark_error(self, message: str) -> None:
        self._last_error_at = time.time()
        self._last_error = message[:300]

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
            "active_by_kind": dict(self._active_by_kind),
            "active_by_user": dict(self._active_by_user),
            "active_by_user_kind": {
                f"{user}:{kind}": count
                for (user, kind), count in self._active_by_user_kind.items()
            },
            "budgets": {kind: self._budget(kind) for kind in KINDS},
            "fair_caps": {
                kind: self._fair_cap(kind, self._budget(kind)) for kind in KINDS
            },
            "waiting_by_user": waiting_by_user,
            "oldest_wait_s": round(now - oldest, 1) if oldest else 0.0,
            "last_ok_at": self._last_ok_at or None,
            "last_error_at": self._last_error_at or None,
            "last_error": self._last_error,
        }

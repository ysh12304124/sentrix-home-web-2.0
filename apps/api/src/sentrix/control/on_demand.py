"""每用户 API 的按需启动与空闲回收。

体验版同一时间活跃用户很少，48 个 uvicorn 常驻会长期占掉约 25 GiB。控制面在
转发用户请求前确保对应 systemd 单元已启动，空闲超过 TTL 后再停掉；启动/停止经
root 辅助脚本执行，控制面本身仍以 asus 运行。

**只在主入口启用**。独立管理台（admin_only）不转发用户 API，也不能替用户启动服务。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable

import httpx

from sentrix.control.proxy import RouteTable, UserRoute

log = logging.getLogger(__name__)

_ACCOUNT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_HEALTH_PATH = "/api/v1/health/live"


class UserApiSupervisor:
    def __init__(
        self,
        routes: RouteTable,
        *,
        helper: str,
        idle_ttl_s: float,
        start_timeout_s: float,
        enabled: bool = True,
        sweep_interval_s: float = 30.0,
    ) -> None:
        self._routes = routes
        self._helper = helper
        self._idle_ttl_s = idle_ttl_s
        self._start_timeout_s = start_timeout_s
        self._sweep_interval_s = max(1.0, min(sweep_interval_s, idle_ttl_s / 2))
        self._enabled = enabled
        self._running: set[str] = set()
        self._last_used: dict[str, float] = {}
        self._inflight: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _lock(self, account_id: str) -> asyncio.Lock:
        lock = self._locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_id] = lock
        return lock

    async def _systemctl(self, action: str, account_id: str) -> None:
        if not _ACCOUNT_ID.fullmatch(account_id):
            raise RuntimeError("invalid account id")
        process = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            self._helper,
            action,
            account_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=20.0)
        except TimeoutError as error:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            raise RuntimeError(f"systemctl {action} {account_id} timed out") from error
        if process.returncode:
            detail = stderr.decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(f"systemctl {action} {account_id} failed: {detail}")

    async def _wait_ready(self, route: UserRoute, client: httpx.AsyncClient) -> None:
        deadline = time.monotonic() + self._start_timeout_s
        url = f"{route.api_url}{_HEALTH_PATH}"
        headers = {"x-sentrix-internal-token": route.api_token}
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = await client.get(url, headers=headers, timeout=2.0)
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as error:
                last_error = f"{type(error).__name__}: {error}"
            await asyncio.sleep(0.25)
        raise RuntimeError(f"user API {route.account_id} did not become ready: {last_error}")

    async def ensure_started(
        self, account_id: str, route: UserRoute, client: httpx.AsyncClient
    ) -> None:
        """确保这个账号的 API 已启动；并发请求只启动一次。"""
        if not self._enabled:
            return
        self._last_used[account_id] = time.monotonic()
        if account_id in self._running:
            return
        async with self._lock(account_id):
            if account_id in self._running:
                return
            log.info("starting idle user API account=%s", account_id)
            await self._systemctl("start", account_id)
            await self._wait_ready(route, client)
            self._running.add(account_id)
            self._last_used[account_id] = time.monotonic()

    def mark_stopped(self, account_id: str) -> None:
        self._running.discard(account_id)
        self._last_used.pop(account_id, None)

    def begin(self, account_id: str) -> None:
        if not self._enabled:
            return
        self._inflight[account_id] = self._inflight.get(account_id, 0) + 1
        self._last_used[account_id] = time.monotonic()

    def end(self, account_id: str) -> None:
        if not self._enabled:
            return
        remaining = self._inflight.get(account_id, 0) - 1
        if remaining > 0:
            self._inflight[account_id] = remaining
        else:
            self._inflight.pop(account_id, None)
        self._last_used[account_id] = time.monotonic()

    def end_callback(self, account_id: str) -> Callable[[], None]:
        return lambda: self.end(account_id)

    async def _sweep_once(self) -> None:
        now = time.monotonic()
        for account_id in list(self._running):
            if self._inflight.get(account_id, 0) > 0:
                continue
            if now - self._last_used.get(account_id, now) < self._idle_ttl_s:
                continue
            async with self._lock(account_id):
                if self._inflight.get(account_id, 0) > 0:
                    continue
                if time.monotonic() - self._last_used.get(account_id, now) < self._idle_ttl_s:
                    continue
                log.info("stopping idle user API account=%s", account_id)
                try:
                    await self._systemctl("stop", account_id)
                except RuntimeError:
                    log.warning("stop idle user API failed account=%s", account_id, exc_info=True)
                finally:
                    self._running.discard(account_id)
                    self._last_used.pop(account_id, None)

    async def _sweep_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._sweep_interval_s)
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    await self._sweep_once()
                except Exception:  # noqa: BLE001 - 回收任务不能因此退出
                    log.warning("idle user API sweep failed", exc_info=True)

    def start(self) -> None:
        if self._enabled and self._task is None:
            self._closed = False
            self._task = asyncio.create_task(self._sweep_loop())

    async def shutdown(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if not self._enabled:
            return
        for account_id in list(self._running):
            try:
                await self._systemctl("stop", account_id)
            except RuntimeError:
                log.warning("stop user API on shutdown failed account=%s", account_id, exc_info=True)
        self._running.clear()
        self._last_used.clear()

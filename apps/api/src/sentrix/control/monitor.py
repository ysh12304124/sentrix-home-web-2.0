"""控制面的轻量监测：主机资源、依赖探活、事件与告警。

采样失败只影响面板的新鲜度，绝不阻塞登录、反馈或模型转发。
"""

from __future__ import annotations

import csv
import io
import subprocess
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import psutil

from sentrix.control.config import ControlSettings
from sentrix.control.metrics import collect_host_snapshot
from sentrix.control.model_gate import ModelGate
from sentrix.control.proxy import RouteTable
from sentrix.control.store import ControlStore

STALE_AFTER_S = 30
FAIL_THRESHOLD = 3
DISK_WARN_GIB = 150
DISK_STOP_GIB = 100


class Monitor:
    def __init__(
        self,
        *,
        settings: ControlSettings,
        store: ControlStore,
        routes: RouteTable,
        gate: ModelGate,
    ) -> None:
        self._settings = settings
        self._store = store
        self._routes = routes
        self._gate = gate
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {"sampled_at": None, "stale": True}
        self._streaks: dict[str, int] = {}
        self._since: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="control-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._snapshot)
        sampled = data.get("sampled_at")
        if sampled is None:
            data["stale"] = True
        else:
            age = (datetime.now(UTC) - datetime.fromisoformat(str(sampled))).total_seconds()
            data["stale"] = age > STALE_AFTER_S
        return data

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = self._collect()
                with self._lock:
                    self._snapshot = snapshot
                self._store.record_metric(
                    sampled_at=str(snapshot["sampled_at"]),
                    source="control",
                    payload={
                        "host": snapshot["host"],
                        "services": {
                            key: value.get("ok")
                            for key, value in snapshot["services"].items()
                            if isinstance(value, dict)
                        },
                    },
                )
            except Exception as error:  # noqa: BLE001 - 采样失败只标记过期
                with self._lock:
                    self._snapshot = {"sampled_at": None, "stale": True, "error": str(error)}
            self._stop.wait(max(1.0, self._settings.sample_interval_s))

    def _collect(self) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        host = self._host()
        services = self._services()
        units = self._units()
        self._evaluate(services, host, now)
        return {
            "sampled_at": now,
            "stale": False,
            "host": host,
            "services": services,
            "units": units,
            "model_queue": self._gate.describe(),
            "open_incidents": len(
                [item for item in self._store.list_incidents(limit=200) if item["status"] == "open"]
            ),
        }

    def _host(self) -> dict[str, Any]:
        base = collect_host_snapshot().as_dict()
        memory = psutil.virtual_memory()
        host: dict[str, Any] = {
            **base,
            "ram_used_gb": round(memory.used / 1024**3, 1),
            "ram_total_gb": round(memory.total / 1024**3, 1),
            "gpu": {"status": "unavailable"},
        }
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            row = next(csv.reader(io.StringIO(completed.stdout)), None)
            if completed.returncode == 0 and row and len(row) >= 6:
                host["gpu"] = {
                    "status": "ok",
                    "name": row[0].strip(),
                    "utilization_percent": _num(row[1]),
                    "memory_used_mib": _num(row[2]),
                    "memory_total_mib": _num(row[3]),
                    "temperature_c": _num(row[4]),
                    "power_w": _num(row[5]),
                }
        except (OSError, subprocess.SubprocessError):
            pass
        return host

    def _services(self) -> dict[str, Any]:
        services: dict[str, Any] = {}
        with httpx.Client(timeout=3.0) as client:
            services["control"] = {"ok": self._store.ping(), "ready": self._store.ping()}
            services["model"] = _probe(
                client, f"{self._settings.model_upstream_url.rstrip('/')}/models"
            )
            services["bge"] = _probe(client, self._settings.bge_health_url)
            services["clip"] = _probe(client, self._settings.clip_health_url)
            users: dict[str, Any] = {}
            for account_id, route in _routes(self._routes):
                result = _probe(
                    client,
                    f"{route.api_url}/api/v1/health/live",
                    headers={"x-sentrix-internal-token": route.api_token},
                )
                try:
                    status_response = client.get(
                        f"{route.api_url}/api/v1/internal/status",
                        headers={"x-sentrix-internal-token": route.api_token},
                    )
                    if status_response.is_success:
                        result["status"] = status_response.json()
                except httpx.HTTPError:
                    pass
                users[account_id] = result
            services["users"] = users
        return services

    def _units(self) -> dict[str, Any]:
        units: dict[str, Any] = {}
        for unit in self._settings.restart_unit_list():
            try:
                completed = subprocess.run(
                    [
                        "systemctl",
                        "show",
                        unit,
                        "--property=ActiveState",
                        "--property=MainPID",
                        "--property=NRestarts",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                fields = {}
                for line in completed.stdout.splitlines():
                    key, _, value = line.partition("=")
                    fields[key] = value
                units[unit] = {
                    "active_state": fields.get("ActiveState", "unknown"),
                    "main_pid": _num(fields.get("MainPID")),
                    "n_restarts": _num(fields.get("NRestarts")),
                }
            except (OSError, subprocess.SubprocessError):
                units[unit] = {"active_state": "unavailable"}
        return units

    def _evaluate(self, services: dict[str, Any], host: dict[str, Any], now: str) -> None:
        checks = {
            "model": bool(services.get("model", {}).get("ok")),
            "bge": bool(services.get("bge", {}).get("ok")),
            "clip": bool(services.get("clip", {}).get("ok")),
        }
        for source, healthy in checks.items():
            self._track(source, healthy, now, f"{source} 不可用")
        users = services.get("users", {})
        for account_id, value in users.items():
            self._track(
                f"user:{account_id}",
                bool(value.get("ok")),
                now,
                f"用户 API {account_id} 不可用",
                user_id=account_id,
            )
        free_gib = host.get("disk_free_gb")
        if isinstance(free_gib, (int, float)):
            if free_gib < DISK_STOP_GIB:
                self._store.open_incident(
                    source="host",
                    category="disk_critical",
                    severity="error",
                    message=f"磁盘可用 {free_gib} GiB，低于 {DISK_STOP_GIB} GiB，已暂停接收新上传",
                    now=now,
                )
            elif free_gib < DISK_WARN_GIB:
                self._store.open_incident(
                    source="host",
                    category="disk_low",
                    severity="warning",
                    message=f"磁盘可用 {free_gib} GiB，低于 {DISK_WARN_GIB} GiB",
                    now=now,
                )
            else:
                self._store.close_incidents(source="host", category="disk_low", now=now, result="recovered")
                self._store.close_incidents(
                    source="host", category="disk_critical", now=now, result="recovered"
                )
        gpu = host.get("gpu", {})
        if gpu.get("status") != "ok":
            self._store.open_incident(
                source="host",
                category="gpu_sensor_missing",
                severity="warning",
                message="GPU 传感器不可读",
                now=now,
            )

    def _track(
        self,
        source: str,
        healthy: bool,
        now: str,
        message: str,
        *,
        user_id: str = "",
    ) -> None:
        streak = 0 if healthy else self._streaks.get(source, 0) + 1
        self._streaks[source] = streak
        if not healthy and streak >= FAIL_THRESHOLD:
            self._store.open_incident(
                source=source,
                category="service_down",
                severity="error",
                message=message,
                now=now,
                user_id=user_id,
            )
        elif healthy and streak == 0:
            self._store.close_incidents(
                source=source, category="service_down", now=now, result="recovered"
            )


def _probe(client: httpx.Client, url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        response = client.get(url, headers=headers or {})
        return {"ok": response.is_success, "status_code": response.status_code, "checked_at": time.time()}
    except httpx.HTTPError as error:
        return {"ok": False, "error": str(error), "checked_at": time.time()}


def _routes(routes: RouteTable):
    for account_id in routes.ids():
        route = routes.get(account_id)
        if route is not None:
            yield account_id, route


def _num(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None

"""控制面的轻量监测：主机资源、依赖探活、事件与告警。

采样失败只影响面板的新鲜度，绝不阻塞登录、反馈或模型转发。
"""

from __future__ import annotations

import csv
import io
import logging
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psutil

from sentrix.control.config import ControlSettings
from sentrix.control.metrics import collect_host_snapshot
from sentrix.control.model_gate import ModelGate
from sentrix.control.proxy import RouteTable
from sentrix.control.store import ControlStore

log = logging.getLogger("sentrix.control.monitor")

STALE_AFTER_S = 30
FAIL_THRESHOLD = 3
DISK_WARN_GIB = 150
DISK_STOP_GIB = 100
ROLLUP_INTERVAL_S = 60.0
RETENTION_INTERVAL_S = 6 * 3600.0
ACTIVE_JOB_STATUSES = (
    "accepted",
    "queued",
    "running",
    "waiting_dependency",
    "retry_scheduled",
)


class Monitor:
    def __init__(
        self,
        *,
        settings: ControlSettings,
        store: ControlStore,
        routes: RouteTable,
        gate: ModelGate,
        # 独立管理入口只做采集展示：告警、趋势、保留期清理与真实推理探针
        # 由用户入口那一个主监测器独占，避免同一份控制库被两个进程重复写。
        primary: bool = True,
    ) -> None:
        self._settings = settings
        self._store = store
        self._routes = routes
        self._gate = gate
        self._primary = primary
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {"sampled_at": None, "stale": True}
        self._streaks: dict[str, int] = {}
        self._since: dict[str, float] = {}
        self._last_encoding_check = 0.0
        self._last_inference_check = 0.0
        self._encoding: dict[str, dict[str, Any]] = {}
        self._inference: dict[str, Any] = {}
        self._unit_history: dict[str, list[tuple[float, float]]] = {}
        self._gpu_temp_since: float | None = None
        self._gpu_vram_since: float | None = None
        self._rollup_samples: list[dict[str, Any]] = []
        self._last_rollup = time.monotonic()
        self._last_retention = 0.0
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
                if self._primary:
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
                            # 独立管理入口的准入队列永远为空（它不转发模型流量），
                            # 从主监测器落库的这条记录里读真实队列。
                            "model_queue": snapshot["model_queue"],
                        },
                    )
                    self._rollup_samples.append(snapshot)
                    if time.monotonic() - self._last_rollup >= ROLLUP_INTERVAL_S:
                        self._record_rollup(str(snapshot["sampled_at"]))
                    self._run_retention()
            except Exception as error:  # noqa: BLE001 - 采样失败只标记过期
                log.warning("控制面采样失败", exc_info=True)
                with self._lock:
                    self._snapshot = {"sampled_at": None, "stale": True, "error": str(error)}
            self._stop.wait(max(1.0, self._settings.sample_interval_s))

    def _collect(self) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        host = self._host()
        services = self._services()
        units = self._units()
        queue = self._gate.describe()
        self._merge_encoding(services)
        if self._primary:
            self._maybe_inference(services)
            self._evaluate(services, host, now)
            self._evaluate_queue(queue, now)
            self._evaluate_gpu(host, now)
            self._evaluate_restarts(units, now)
            self._evaluate_stalled(services, now)
        return {
            "sampled_at": now,
            "stale": False,
            "host": host,
            "services": services,
            "units": units,
            "model_queue": queue,
            "open_incidents": len(
                [item for item in self._store.list_incidents(limit=200) if item["status"] == "open"]
            ),
        }

    def _evaluate_queue(self, queue: dict[str, Any], now: str) -> None:
        oldest = queue.get("oldest_wait_s")
        wait_s = float(oldest) if isinstance(oldest, (int, float)) else 0.0
        if wait_s >= self._settings.queue_wait_warn_s:
            self._store.open_incident(
                source="model",
                category="queue_long",
                severity="warning",
                message=f"模型排队最久 {wait_s:.0f} 秒，超过 {self._settings.queue_wait_warn_s:.0f} 秒",
                now=now,
            )
        else:
            self._store.close_incidents(
                source="model", category="queue_long", now=now, result="recovered"
            )

    def _record_rollup(self, sampled_at: str) -> None:
        samples = self._rollup_samples
        self._rollup_samples = []
        self._last_rollup = time.monotonic()
        if not samples:
            return
        self._store.record_metric(
            sampled_at=sampled_at, source="minute", payload=_aggregate(samples)
        )

    def _run_retention(self) -> None:
        if time.monotonic() - self._last_retention < RETENTION_INTERVAL_S:
            return
        self._last_retention = time.monotonic()
        settings = self._settings
        try:
            self._store.purge_metrics(older_than_days=settings.metric_retention_days)
            self._store.purge_audit(older_than_days=settings.audit_retention_days)
            self._store.purge_client_events(
                older_than_days=settings.client_event_retention_days
            )
            for path in self._store.purge_feedback(
                older_than_days=settings.feedback_retention_days
            ):
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    log.warning("反馈截图清理失败: %s", path)
        except Exception:  # noqa: BLE001 - 清理失败不影响采样
            log.warning("控制库保留期清理失败", exc_info=True)

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
                # 用户 API 不再做独立健康探测：服务状态面板只展示共享服务。
                # 这里仍读一次 internal status，用于“按用户运行”的作业/回合数据；
                # 读不到只把该用户标成异常，不生成可用性告警。
                result: dict[str, Any] = {"ok": False, "api_url": route.api_url}
                try:
                    status_response = client.get(
                        f"{route.api_url}/api/v1/internal/status",
                        headers={"x-sentrix-internal-token": route.api_token},
                    )
                    result["ok"] = status_response.is_success
                    if status_response.is_success:
                        result["status"] = status_response.json()
                except httpx.HTTPError as error:
                    result["error"] = type(error).__name__
                users[account_id] = result
            services["users"] = users
        return services

    def _merge_encoding(self, services: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_encoding_check >= self._settings.encoding_probe_interval_s:
            self._last_encoding_check = now
            self._encoding = self._probe_encodings()
        for name, result in self._encoding.items():
            entry = services.get(name)
            if not isinstance(entry, dict):
                continue
            entry["encoding"] = result
            if not result.get("ok", False):
                entry["ok"] = False
                entry.setdefault("error", "encoding probe failed")

    def _probe_encodings(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        settings = self._settings
        with httpx.Client(timeout=10.0) as client:
            for name, url, payload, dimension in (
                ("bge", settings.bge_embed_url, {"texts": ["健康检查"]}, 1024),
                ("clip", settings.clip_embed_url, {"text": "健康检查"}, 768),
            ):
                try:
                    response = client.post(url, json=payload)
                    body = response.json()
                    vector = body.get("vector") or []
                    ok = response.is_success and isinstance(vector, list) and len(vector) == dimension
                    result[name] = {
                        "ok": ok,
                        "dimension": len(vector) if isinstance(vector, list) else 0,
                        "expected": dimension,
                        "checked_at": time.time(),
                    }
                except (httpx.HTTPError, ValueError, TypeError) as error:
                    result[name] = {"ok": False, "error": str(error), "checked_at": time.time()}
        return result

    def _maybe_inference(self, services: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_inference_check < self._settings.inference_probe_interval_s:
            if self._inference:
                services["model"]["inference"] = self._inference
            return
        self._last_inference_check = now
        if self._gate.describe().get("active", 0):
            # 只在空闲槽位做真实推理探针，避免监控抢占问答。
            services["model"]["inference"] = {"ok": True, "skipped": "busy"}
            return
        settings = self._settings
        payload = {
            "model": settings.model_id or "default",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{settings.model_upstream_url.rstrip('/')}/chat/completions",
                    json=payload,
                )
            self._inference = {"ok": response.is_success, "checked_at": time.time()}
        except httpx.HTTPError as error:
            self._inference = {"ok": False, "error": str(error), "checked_at": time.time()}
        services["model"]["inference"] = self._inference
        if not self._inference.get("ok", False):
            services["model"]["ok"] = False

    def _evaluate_gpu(self, host: dict[str, Any], now: str) -> None:
        gpu = host.get("gpu") or {}
        if gpu.get("status") != "ok":
            return
        moment = time.monotonic()
        temperature = gpu.get("temperature_c")
        if isinstance(temperature, (int, float)) and temperature > 80:
            self._gpu_temp_since = self._gpu_temp_since or moment
            if moment - self._gpu_temp_since >= 120:
                self._store.open_incident(
                    source="host",
                    category="gpu_hot",
                    severity="error",
                    message=f"GPU 温度 {temperature}°C 持续超过 80°C",
                    now=now,
                )
        else:
            self._gpu_temp_since = None
            self._store.close_incidents(source="host", category="gpu_hot", now=now, result="recovered")
        used = gpu.get("memory_used_mib")
        total = gpu.get("memory_total_mib")
        if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total > 0:
            ratio = used / total * 100
            if ratio > 95:
                self._gpu_vram_since = self._gpu_vram_since or moment
                if moment - self._gpu_vram_since >= 300:
                    self._store.open_incident(
                        source="host",
                        category="gpu_vram_high",
                        severity="warning",
                        message=f"整卡显存占用 {ratio:.0f}% 持续超过 95%",
                        now=now,
                    )
            else:
                self._gpu_vram_since = None
                self._store.close_incidents(
                    source="host", category="gpu_vram_high", now=now, result="recovered"
                )

    def _evaluate_restarts(self, units: dict[str, Any], now: str) -> None:
        moment = time.monotonic()
        for unit, value in units.items():
            restarts = value.get("n_restarts")
            if not isinstance(restarts, (int, float)):
                continue
            history = [item for item in self._unit_history.get(unit, []) if moment - item[0] <= 600]
            history.append((moment, float(restarts)))
            self._unit_history[unit] = history
            baseline = min(count for _, count in history)
            if float(restarts) - baseline >= 3:
                self._store.open_incident(
                    source=f"unit:{unit}",
                    category="restart_loop",
                    severity="error",
                    message=f"{unit} 10 分钟内重启 {int(float(restarts) - baseline)} 次",
                    now=now,
                )
            elif len(history) > 1 and float(restarts) <= baseline:
                self._store.close_incidents(
                    source=f"unit:{unit}", category="restart_loop", now=now, result="stable"
                )

    def _evaluate_stalled(self, services: dict[str, Any], now: str) -> None:
        moment = datetime.now(UTC)
        for account, value in (services.get("users") or {}).items():
            status = value.get("status") if isinstance(value, dict) else None
            if not isinstance(status, dict):
                continue
            for key, category, label in (
                ("oldest_active_job", "job_stalled", "导入作业"),
                ("oldest_active_turn", "turn_stalled", "问答回合"),
            ):
                stamp = status.get(key)
                source = f"user:{account}"
                if isinstance(stamp, str):
                    try:
                        age = (moment - datetime.fromisoformat(stamp)).total_seconds()
                    except ValueError:
                        age = 0
                    if age > 600:
                        self._store.open_incident(
                            source=source,
                            category=category,
                            severity="warning",
                            message=f"{account} 的{label}超过 10 分钟无进展",
                            now=now,
                            user_id=account,
                        )
                        continue
                self._store.close_incidents(
                    source=source, category=category, now=now, result="recovered"
                )

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
        # 用户 API 不做独立探活，因此不在这里生成可用性告警；
        # internal status 仍驱动 _evaluate_stalled 的导入/问答停滞告警。
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


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _avg(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 2) if present else None


def _aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """一分钟窗口的聚合：资源取均值，队列与在飞数量取峰值。"""
    hosts = [item.get("host") or {} for item in samples]
    gpus = [host.get("gpu") or {} for host in hosts]
    queues = [item.get("model_queue") or {} for item in samples]

    users: dict[str, dict[str, int]] = {}
    for sample in samples:
        services = sample.get("services") or {}
        for account, value in (services.get("users") or {}).items():
            status = value.get("status") if isinstance(value, dict) else None
            if not isinstance(status, dict):
                continue
            jobs = status.get("jobs") or {}
            turns = status.get("turns") or {}
            entry = users.setdefault(
                account, {"active_jobs": 0, "waiting_jobs": 0, "active_turns": 0}
            )
            active_jobs = sum(int(jobs.get(key, 0)) for key in ACTIVE_JOB_STATUSES)
            waiting_jobs = int(jobs.get("waiting_dependency", 0)) + int(
                jobs.get("retry_scheduled", 0)
            )
            active_turns = int(turns.get("pending", 0)) + int(turns.get("running", 0))
            entry["active_jobs"] = max(entry["active_jobs"], active_jobs)
            entry["waiting_jobs"] = max(entry["waiting_jobs"], waiting_jobs)
            entry["active_turns"] = max(entry["active_turns"], active_turns)

    last_services = (samples[-1].get("services") or {}) if samples else {}
    return {
        "host": {
            "cpu_percent": _avg([_number(item.get("cpu_percent")) for item in hosts]),
            "ram_used_gb": _avg([_number(item.get("ram_used_gb")) for item in hosts]),
            "disk_free_gb": _avg([_number(item.get("disk_free_gb")) for item in hosts]),
            "load_1m": _avg([_number(item.get("load_1m")) for item in hosts]),
            "gpu": {
                "utilization_percent": _avg(
                    [_number(item.get("utilization_percent")) for item in gpus]
                ),
                "memory_used_mib": _avg([_number(item.get("memory_used_mib")) for item in gpus]),
                "temperature_c": _avg([_number(item.get("temperature_c")) for item in gpus]),
            },
        },
        "queue": {
            "active": max((int(item.get("active") or 0) for item in queues), default=0),
            "waiting_total": max(
                (int(item.get("waiting_total") or 0) for item in queues), default=0
            ),
            "oldest_wait_s": max(
                (float(item.get("oldest_wait_s") or 0) for item in queues), default=0.0
            ),
        },
        "users": users,
        "services": {
            key: value.get("ok")
            for key, value in last_services.items()
            if isinstance(value, dict)
        },
    }


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

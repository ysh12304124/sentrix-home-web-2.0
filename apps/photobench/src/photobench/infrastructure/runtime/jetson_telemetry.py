"""1.0 ``backend/jetson_telemetry.py``（660 行 → 实测 521 行）在 2.0 的重建。

**它的调用者只有一个**：1.0 ``select_runtime_providers``（``benchmark_orchestrator.py:176``）
里「本机是 Jetson 且端点是本机 llama.cpp」那一路会构造
``LocalJetsonLlamaCppTelemetryProvider``。S9a 落地时没搬它（1.0 的
``backend/runtime_providers.py`` **import 了它两个名字却从未使用**，ruff F401），
S9a 施工记录把它裁决给 S9b；S9b 切三段时漏分，S9b-2 施工记录的偏离 1 再次上报，
**S9b-3 收下它**——它与 ``select_runtime_providers`` 是一块（provider 选择 + 遥测）。

**为什么不能与 ``infrastructure/runtime/providers.py`` 合并。** 两份都要读 ``/proc``、
都要按端口/关键词归属进程，但**它们的主机形态不同**：这一份只对本机 Jetson（Orin）
成立，常量里多 ``8100``/``8101``/``6333``，进程归属走 PSS + tegrastats；providers.py
那一份服务「任意 NVIDIA 宿主机」，归属走 nvidia-smi 的 compute-apps。S9a 已按
「Apple 那份不与 providers 合并」的同一理由保留独立性，这里照同一条口径——合并会
悄悄改变两台主机上的归属结果。

**它是基础设施**：起 ``tegrastats`` 子进程、读 ``/proc``、打 llama.cpp 的 ``/metrics``。
按 ``photobench-infrastructure-purity`` 只能落在 infrastructure；端口在 domain。
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from photobench.domain.runtime.metrics import (
    MEMORY_OBSERVATION_TTL_SECONDS,
    RESOURCE_TELEMETRY_VERSION,
    component_total,
    memory_freshness,
    physical_memory_used,
)
from photobench.infrastructure.runtime.bounded_probe import BoundedProbe
from photobench.infrastructure.runtime.hardware import HostCpuSampler
from photobench.infrastructure.runtime.jetson_probe import JetsonMemoryWorker
from photobench.infrastructure.runtime.telemetry_scope import (
    dedupe_rows,
    load_telemetry_services,
    service_resolved,
    service_rows,
)


def _configured_set(env_name: str, defaults: tuple[str, ...]) -> set[str]:
    """1.0 同名函数照搬：环境变量给了就整份替换（不是并入），空项剔除。"""
    raw = os.environ.get(env_name)
    if not raw:
        return set(defaults)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


#: 这份是与 providers.py **独立的一份**（S9a 对 Apple 那份的裁决，同一条理由）：
#: Jetson 上模型进程绑的是 8100/8101，而 providers.py 的默认集里那两条用的是 2.0 的
#: 8772/8191。合并会让一台 Jetson 上的归属结果跟着另一台主机的端口表变。
SYSTEM_PROCESS_PORTS = _configured_set(
    "PHOTOBENCH_SYSTEM_PROCESS_PORTS",
    ("8771", "8091", "8500", "8501", "8100", "8101", "6333"),
)
SYSTEM_PROCESS_KEYWORDS = _configured_set(
    "PHOTOBENCH_SYSTEM_PROCESS_KEYWORDS",
    (
        "photobench",
        "benchmark_orchestrator.py",
        "sentrix",
        "vllm",
        "llama-server",
        "ollama",
        "qdrant",
    ),
)


def _process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        text = raw.replace(bytes([0]), bytes([32])).decode("utf-8", "replace").strip()
        if text:
            return text
    except (OSError, ValueError):
        pass
    # cmdline 读不到（内核线程 / 权限）时退回 comm；两者都读不到才是空串。
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _process_rss_mib(pid: int) -> float | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return round(float(line.split()[1]) / 1024.0, 2)
    except (OSError, ValueError, IndexError):
        return None
    return None


NVMAP_CLIENTS_PATH = os.environ.get("PHOTOBENCH_NVMAP_CLIENTS_PATH", "/sys/kernel/debug/nvmap/iovmm/clients")
NVMAP_CACHE_SECONDS = max(0.0, float(os.environ.get("PHOTOBENCH_NVMAP_CACHE_SECONDS", "2")))

_nvmap_cache: dict[str, Any] = {"at": float("-inf"), "table": None}
_nvmap_lock = threading.RLock()
NVMAP_READ_TIMEOUT_SECONDS = 0.2


def _observation(source: str, started: float, *, available: bool, **facts: Any) -> dict[str, Any]:
    return {
        "source": source,
        "status": "available" if available else "unavailable",
        "observed_at_monotonic": started,
        "finished_at_monotonic": time.monotonic(),
        "ttl_seconds": MEMORY_OBSERVATION_TTL_SECONDS,
        **facts,
    }


def _read_nvmap_clients() -> str | None:
    """Read the Tegra nvmap client table, or None when it is not readable.

    The table lives in debugfs and is root-only. A passwordless sudo rule for
    exactly this one file is the deployment hook (see the README); without it
    the caller must treat GPU memory as *unknown*, never as zero.
    """
    try:
        return Path(NVMAP_CLIENTS_PATH).read_text(encoding="ascii", errors="replace")
    except OSError:
        pass
    sudo = shutil.which("sudo")
    if not sudo:
        return None
    try:
        completed = subprocess.run(
            [sudo, "-n", "cat", NVMAP_CLIENTS_PATH],
            capture_output=True,
            text=True,
            timeout=NVMAP_READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or None


def _nvmap_clients_mib() -> dict[int, float] | None:
    """Per-PID nvmap (GPU/UMA) allocation in MiB, or None when unreadable."""
    with _nvmap_lock:
        return _refresh_nvmap_clients_mib()


def _refresh_nvmap_clients_mib() -> dict[int, float] | None:
    now = time.monotonic()
    if now - float(_nvmap_cache["at"]) < NVMAP_CACHE_SECONDS:
        _nvmap_cache["observation"] = {**(_nvmap_cache.get("observation") or {}), "cache_hit": True}
        cached = _nvmap_cache["table"]
        return cached if isinstance(cached, dict) else None
    text = _read_nvmap_clients()
    table: dict[int, float] | None = None
    if text:
        parsed: dict[int, float] = {}
        for line in text.splitlines():
            parts = line.split()
            # CLIENT PROCESS PID SIZE — the trailing "total" row has 2 fields.
            if (
                len(parts) < 4
                or not parts[2].isdigit()
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[KMG]", parts[3]) is None
            ):
                continue
            try:
                size_mib = float(parts[3][:-1]) * {"K": 1 / 1024, "M": 1, "G": 1024}[parts[3][-1]]
            except ValueError:
                continue
            pid = int(parts[2])
            parsed[pid] = round(parsed.get(pid, 0.0) + size_mib, 3)
        table = parsed if parsed or re.search(r"^\s*total\s+0K\s*$", text, re.MULTILINE) else None
    total = re.search(r"^\s*total\s+(\d+)([KMG])\s*$", text or "", re.MULTILINE)
    _nvmap_cache["total_mib"] = (
        float(total.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[total.group(2)]
        if total and table is not None
        else None
    )
    _nvmap_cache["at"] = now
    _nvmap_cache["table"] = table
    _nvmap_cache["observation"] = _observation(
        "nvmap_iovmm",
        now,
        available=table is not None,
        cache_hit=False,
        reason=None if table is not None else "nvmap_unreadable_or_timeout",
    )
    return table


def _process_gpu_memory_mib(
    model_pids: set[int],
    *,
    ledger: Mapping[int, float] | None | Literal["probe"] = "probe",
) -> tuple[float | None, str]:
    """PID-owned nvmap allocations; unreadable ledgers remain unknown."""
    table = _nvmap_clients_mib() if ledger == "probe" else ledger
    if table is None or not model_pids:
        return None, "unavailable"
    total = component_total([table.get(int(pid), 0.0) for pid in model_pids])
    return total, "nvmap_iovmm" if total is not None else "unavailable"


def apply_jetson_memory_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """New observations separate PSS and GPU allocations, including missing values."""
    data.update(
        protocol_version=RESOURCE_TELEMETRY_VERSION,
        memory_accounting="jetson_pss_and_nvmap_v3",
        memory_unit="process_pss_mib",
        memory_scope="process_pss_separate_gpu_allocation",
        memory_profile={
            "method": "jetson_pss_and_nvmap_v3",
            "memory_unit": "process_pss_mib",
            "scope": "process_pss_separate_gpu_allocation",
        },
    )
    return data


_listener_cache: dict[tuple[str, ...], dict[str, Any]] = {}
_listener_lock = threading.Lock()


def _process_start_ticks(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class _SocketInodes(set[str]):
    def __init__(self) -> None:
        super().__init__()
        self.descriptors: dict[str, str] = {}


def _socket_inodes(pid: int) -> set[str]:
    inodes = _SocketInodes()
    try:
        fds = list(Path(f"/proc/{pid}/fd").iterdir())
    except OSError:
        return inodes
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            inodes.add(match.group(1))
            inodes.descriptors[match.group(1)] = fd.name
    return inodes


def _owns_listener_sockets(pid: int, held: set[str], descriptors: dict[str, str]) -> bool:
    if set(descriptors) != held:
        return held <= _socket_inodes(pid)
    try:
        return all(
            os.readlink(Path(f"/proc/{pid}/fd") / fd) == f"socket:[{inode}]"
            for inode, fd in descriptors.items()
        )
    except OSError:
        return False


def _listening_ports_by_pid(target_ports: set[str] | None = None) -> dict[int, set[str]]:
    """``/proc/net/tcp`` 的监听 socket → 持有它的 PID。只收 ``target_ports`` 里的端口。

    socket inode 是两张表之间的桥：``/proc/net/tcp`` 给 inode→端口，
    ``/proc/*/fd`` 的 readlink 给 fd→inode。不给 ``target_ports`` 过滤的话，一台
    跑满连接的机器上这一步会扫遍所有 fd。限定端口时，2秒内仅重查已知持有者的
    socket与启动标识；监听集合/身份变化立即全扫，避免把固定PID当长期身份。
    缓存只保存定位结果，每次都重新核验所有权，不因固定时间到期重复全机fd扫描。
    """
    target_ports = {str(port) for port in (target_ports or set())}
    socket_ports: dict[str, str] = {}
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":  # 0A = LISTEN
                continue
            try:
                port = str(int(parts[1].rsplit(":", 1)[1], 16))
            except (ValueError, IndexError):
                continue
            if target_ports and port not in target_ports:
                continue
            socket_ports[parts[9]] = port
    if not socket_ports:
        return {}
    signature = tuple(sorted(socket_ports.items()))
    selection = tuple(sorted(target_ports))
    with _listener_lock:
        entry = _listener_cache.get(selection, {})
        cached = entry.get("owners", {})
        identities = entry.get("identities", {})
        descriptors = entry.get("descriptors", {})
        if (
            target_ports
            and entry.get("signature") == signature
            and cached
            and all(
                identities.get(pid) is not None
                and _process_start_ticks(pid) == identities[pid]
                and _owns_listener_sockets(pid, held, descriptors.get(pid, {}))
                for pid, held in cached.items()
            )
        ):
            entry["expires"] = time.monotonic() + 2.0  # idle retention, not observation freshness
            return {pid: {socket_ports[inode] for inode in held} for pid, held in cached.items()}
    owners: dict[int, set[str]] = {}
    listener_descriptors: dict[int, dict[str, str]] = {}
    result: dict[int, set[str]] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        before = _process_start_ticks(int(proc.name))
        sockets = _socket_inodes(int(proc.name))
        held = sockets & socket_ports.keys()
        if held and before is not None and before == _process_start_ticks(int(proc.name)):
            owners[int(proc.name)] = held
            listener_descriptors[int(proc.name)] = {
                inode: fd for inode, fd in getattr(sockets, "descriptors", {}).items() if inode in held
            }
            result[int(proc.name)] = {socket_ports[inode] for inode in held}
    with _listener_lock:
        now = time.monotonic()
        # The device/model and full product probes use different port sets.
        # Keep their independently validated entries, with bounded retention.
        for key in list(_listener_cache):
            if _listener_cache[key].get("expires", 0) <= now:
                del _listener_cache[key]
        if selection not in _listener_cache and len(_listener_cache) >= 32:
            del _listener_cache[next(iter(_listener_cache))]
        _listener_cache[selection] = {
            "signature": signature,
            "owners": owners,
            "identities": {pid: _process_start_ticks(pid) for pid in owners},
            "descriptors": listener_descriptors,
            "expires": now + 2.0,
        }
    return result


def _process_identity(pid: int) -> tuple[str, int]:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[19], int(fields[1])


def _process_parent_snapshot() -> dict[int, int]:
    # The AGX kernel does not expose /proc/<pid>/task/<tid>/children.
    # ps walks proc in native code outside the sampler process's GIL; do not
    # read hundreds of unrelated stat files from Python probe threads.
    completed = subprocess.run(
        ["ps", "-e", "-o", "pid=,ppid="],
        capture_output=True,
        text=True,
        check=True,
        timeout=0.5,
    )
    return {int(pid): int(parent) for pid, parent in (line.split() for line in completed.stdout.splitlines())}


class _ProcessRows(list[dict[str, Any]]):
    def __init__(self, rows: list[dict[str, Any]], discovery: dict[str, Any]) -> None:
        super().__init__(rows)
        self.discovery = discovery


def _host_process_rows(target_ports: set[str] | None = None) -> list[dict[str, Any]]:
    started = time.monotonic()
    discovery: dict[str, Any] = {"path": "full_proc"}
    identities: dict[int, tuple[str, int]] | None = None
    ports_by_pid: dict[int, set[str]] = {}
    if target_ports:
        try:
            parents = _process_parent_snapshot()
            discovery.update(path="native_pid_parent", parent_seconds=time.monotonic() - started)
            listener_started = time.monotonic()
            ports_by_pid = _listening_ports_by_pid(target_ports)
            discovery["listener_seconds"] = time.monotonic() - listener_started
            selected = set(ports_by_pid)
            while True:
                expanded = selected | {pid for pid, parent in parents.items() if parent in selected}
                if expanded == selected:
                    break
                selected = expanded
            identities = {}
            for pid in selected:
                try:
                    identity = _process_identity(pid)
                except (OSError, ValueError, IndexError):
                    continue  # The snapshot may include a child that already exited.
                if identity[1] == parents.get(pid):
                    identities[pid] = identity
            # A reused listener PID cannot inherit old port ownership.
            if ports_by_pid != _listening_ports_by_pid(target_ports):
                identities = None
                discovery.update(path="full_proc_fallback", reason="listener_changed")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            # Failed native discovery falls back to the complete proc snapshot.
            identities = None
            discovery.update(
                path="full_proc_fallback",
                reason=type(error).__name__,
                parent_seconds=time.monotonic() - started,
            )
    if identities is None:
        identities = {}
        for proc in Path("/proc").iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                identities[int(proc.name)] = _process_identity(int(proc.name))
            except (OSError, ValueError, IndexError):
                continue
        ports_by_pid = _listening_ports_by_pid(target_ports)
    selected = set(ports_by_pid) if target_ports else set(identities)
    while True:
        expanded = selected | {pid for pid, (_, parent) in identities.items() if parent in selected}
        if expanded == selected:
            break
        selected = expanded
    rows = []
    for pid, (before, parent) in identities.items():
        if pid not in selected:
            continue
        rss_mib = _process_rss_mib(pid)
        cmdline = _process_cmdline(pid)
        if (rss_mib is None and not cmdline) or before != _process_start_ticks(pid):
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": parent,
                "start_ticks": before,
                "process_name": Path(cmdline.split(" ", 1)[0]).name if cmdline else "",
                "rss_mib": rss_mib,
                "listening_ports": sorted(ports_by_pid.get(pid, set()), key=lambda value: int(value)),
                "identity": cmdline.lower()[:1000],
            }
        )
    discovery["total_seconds"] = time.monotonic() - started
    return _ProcessRows(rows, discovery)


def _is_system_related_process(row: dict[str, Any]) -> bool:
    identity = str(row.get("identity") or "").lower()
    ports = {str(port) for port in row.get("listening_ports") or []}
    if ports & SYSTEM_PROCESS_PORTS:
        return True
    return any(keyword and keyword in identity for keyword in SYSTEM_PROCESS_KEYWORDS)


def _sum_mib(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows or any(not isinstance(row.get(key), (int, float)) for row in rows):
        return None
    return round(sum(float(row[key]) for row in rows), 2)


def _process_smaps_memory_mib(pid: int) -> tuple[float | None, float | None]:
    """Read PSS and RSS in one kernel walk, so both describe the same observation."""
    values: dict[str, float] = {}
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as file:
            for line in file:
                key = line.split(":", 1)[0]
                if key in {"Pss", "Rss"}:
                    values[key] = round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError, IndexError):
        return None, None
    return values.get("Pss"), values.get("Rss")


def _is_model_process(row: dict[str, Any], *, model_pid: int | None, endpoint_port: str) -> bool:
    identity = str(row.get("identity") or "")
    return (
        (model_pid is not None and row.get("pid") == model_pid)
        or "llama-server" in identity
        or f"--port {endpoint_port}" in identity
        or f"--port {endpoint_port} " in identity
    )


def _augment_local_process_memory(
    data: dict[str, Any] | None,
    *,
    model_pid: int | None,
    endpoint_port: str,
    telemetry_services: Mapping[str, Any] | None = None,
    probe: BoundedProbe | None = None,
    topology_probe: BoundedProbe | None = None,
    topology_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Independently observed process PSS and PID-owned nvmap allocations."""
    # Never let a disappeared PID or failed smaps read inherit a previous total.
    data = {
        key: value
        for key, value in (data or {}).items()
        if not key.startswith(
            ("model_process_", "model_system_", "sentrix_", "product_stack_", "benchmark_process_")
        )
    }
    data.update(
        model_process_system_memory_used_mib=None,
        model_process_host_memory_mib=None,
        model_process_gpu_memory_mib=None,
        model_process_gpu_memory_source="unavailable",
        memory_accounting="jetson_pss_and_nvmap_v3",
    )
    scope = telemetry_services if telemetry_services is not None else load_telemetry_services()
    started = time.monotonic()
    target_ports: set[str] = {str(endpoint_port)}
    port_scoped = bool(scope)
    if scope:
        embedding_spec = dict(scope.get("embedding") or {})
        profile_spec = dict((embedding_spec.get("profiles") or {}).get(embedding_spec.get("mode")) or {})
        if embedding_spec.get("local") is False:
            profile_spec = {}
        for spec in [
            scope.get("main_model"),
            scope.get("sentrix"),
            profile_spec,
            *(scope.get("product") or []),
        ]:
            if isinstance(spec, Mapping) and spec.get("local") is not False:
                target_ports.update(str(port) for port in spec.get("ports") or [])
                if spec.get("pids") or (spec.get("keywords") and not spec.get("ports")):
                    port_scoped = False
    topology_result: dict[str, Any] = {}
    try:
        # Legacy keyword matching and explicit PID selectors require the full
        # inventory; narrowing them to listener trees would silently omit RAM.
        ports = target_ports if port_scoped else None
        if topology_probe is None:
            rows = _host_process_rows(ports)
        else:
            # Once a tree is known, start/harvest discovery without blocking
            # current memory reads. Waiting a second here serialized the two
            # walks and left too little of the total window for a fresh tree.
            topology_deadline = started + (0.0 if port_scoped and topology_snapshot else 1.0)
            discovery = {"topology": lambda: _host_process_rows(ports)}
            ready_topology = topology_probe.collect(
                discovery, budget=max(0.0, topology_deadline - time.monotonic())
            )
            if float(ready_topology.get("topology", {}).get("started", started)) < started:
                # Harvesting a previous callback's completed job does not launch
                # its successor. Start a current discovery within the SAME
                # deadline, so a slow memory walk cannot age that old topology
                # beyond TTL while a newer discovery could have completed.
                current_topology = topology_probe.collect(
                    discovery, budget=max(0.0, topology_deadline - time.monotonic())
                )
                ready_topology.update(current_topology)
            if "topology" in ready_topology and topology_snapshot is not None:
                topology_snapshot.clear()
                topology_snapshot.update(ready_topology["topology"])
            topology_result = dict(topology_snapshot or ready_topology.get("topology") or {})
            # Retain original times; memory_freshness invalidates slow/old
            # discoveries. No accumulating topology jobs or artificial refresh.
            rows = topology_result.get("value") or []
    except Exception:
        rows = []
    topology_observation = _observation(
        "proc_listener_topology",
        float(topology_result.get("started", started)),
        available=bool(rows),
        discovery=getattr(rows, "discovery", {}),
    )
    if topology_result:
        topology_observation["finished_at_monotonic"] = topology_result.get("finished")
    if scope:
        main_spec = dict(scope.get("main_model") or {})
        model_rows = service_rows(rows, main_spec) if main_spec.get("local") is not False else []
        model_pids = {row["pid"] for row in model_rows}
        embedding = dict(scope.get("embedding") or {})
        mode = str(embedding.get("mode") or "")
        profile = dict((embedding.get("profiles") or {}).get(mode) or {})
        if embedding.get("local") is False or profile.get("local") is False:
            profile = {}
        auxiliary_rows = [row for row in service_rows(rows, profile) if row["pid"] not in model_pids]
        auxiliary_pids = {row["pid"] for row in auxiliary_rows}
        product_rows: list[dict[str, Any]] = []
        for product_spec in scope.get("product") or []:
            if isinstance(product_spec, dict):
                product_rows.extend(service_rows(rows, product_spec))
        product_rows.extend(service_rows(rows, dict(scope.get("sentrix") or {})))
        product_rows = [
            row
            for row in dedupe_rows(product_rows)
            if row["pid"] not in model_pids and row["pid"] not in auxiliary_pids
        ]
        sentrix_rows = dedupe_rows(product_rows + auxiliary_rows)
    else:
        model_rows = [
            row
            for row in rows
            if _is_model_process(row, model_pid=model_pid, endpoint_port=str(endpoint_port))
        ]
        product_rows = [row for row in rows if _is_system_related_process(row)]
        sentrix_rows = [
            row for row in product_rows if row.get("pid") not in {item.get("pid") for item in model_rows}
        ]
        auxiliary_rows = []
    observations: dict[int, tuple[float | None, float | None]] = {}
    memory_observations: dict[str, Any] = {"topology": topology_observation}
    selected_rows = dedupe_rows(model_rows + sentrix_rows)
    ready: dict[str, dict[str, Any]] = {}
    if probe is not None:
        readers = {
            f"{row['pid']}:{row.get('start_ticks')}": (
                lambda pid=int(row["pid"]): (
                    _process_start_ticks(pid),
                    _process_smaps_memory_mib(pid),
                    _process_start_ticks(pid),
                )
            )
            for row in selected_rows
        }
        # On AGX, a real smaps_rollup walk during visual inference can exceed
        # 0.6s. Allow topology and current reads a bounded 2.5s total window,
        # still below the unchanged 3s source TTL/coverage requirement. Do not
        # substitute a prior memory value when a read exceeds this deadline.
        ready = probe.collect(readers, budget=max(0.0, 2.5 - (time.monotonic() - started)))
    if topology_probe is not None and probe is not None and port_scoped:
        # Discovery that exceeded its initial 1s wait may finish DURING smaps.
        # It can also finish just AFTER smaps. Use only the remainder of the
        # same 2.5s window; returning early could age the prior topology past
        # TTL even though its current replacement needs only a short wait.
        # launch=False keeps this a join of existing work, never a new probe.
        latest = topology_probe.collect(
            {"topology": lambda: _host_process_rows(target_ports)},
            budget=max(0.0, 2.5 - (time.monotonic() - started)),
            launch=False,
        ).get("topology")
        if latest is not None and float(latest["started"]) < started:
            # A previous callback's job can finish while memory is read. Its
            # successor was not launchable at entry; start it now within the
            # SAME deadline, retaining the older observation if still pending.
            current = topology_probe.collect(
                {"topology": lambda: _host_process_rows(target_ports)},
                budget=max(0.0, 2.5 - (time.monotonic() - started)),
            ).get("topology")
            if current is not None:
                latest = current
        if latest is not None:
            if topology_snapshot is not None:
                topology_snapshot.clear()
                topology_snapshot.update(latest)
            latest_rows = latest.get("value") or []
            fields = ("ppid", "start_ticks", "listening_ports", "identity")
            previous_members = {row["pid"]: tuple(row.get(key) for key in fields) for row in rows}
            current_members = {row["pid"]: tuple(row.get(key) for key in fields) for row in latest_rows}
            if previous_members == current_members and latest_rows:
                topology_observation.clear()
                topology_observation.update(
                    _observation(
                        "proc_listener_topology",
                        float(latest["started"]),
                        available=True,
                        discovery=getattr(latest_rows, "discovery", {}),
                    )
                )
                topology_observation["finished_at_monotonic"] = latest.get("finished")
                # VmRSS belongs to that new topology read. PSS/smaps RSS keep
                # their own original probe times; no old value is relabelled.
                current_rss = {row["pid"]: row.get("rss_mib") for row in latest_rows}
                for row in model_rows + sentrix_rows + product_rows + auxiliary_rows + selected_rows:
                    row["rss_mib"] = current_rss[row["pid"]]
            else:
                # A new worker / reused PID cannot inherit the old membership's
                # memory totals. The next callback observes the new tree.
                topology_observation.update(
                    status="unavailable", reason="topology_changed_during_memory_read"
                )
    for row in selected_rows:
        pid = int(row["pid"])
        observed = time.monotonic()
        over_budget = observed - started >= 1.0
        if probe is None:
            before = _process_start_ticks(pid)
            pss, rss = (None, None) if over_budget else _process_smaps_memory_mib(pid)
            after = _process_start_ticks(pid)
        else:
            result = ready.get(f"{pid}:{row.get('start_ticks')}", {})
            over_budget = not result or "value" not in result
            observed = float(result.get("started", observed))
            before, (pss, rss), after = result.get("value", (None, (None, None), None))
        same_process = before is not None and before == after and row.get("start_ticks") == before
        observations[pid] = (pss, rss) if same_process else (None, None)
        for basis, value in (("pss", pss), ("rss", rss), ("identity", before)):
            memory_observations[f"process:{pid}:{basis}"] = _observation(
                f"proc_{basis}",
                observed,
                available=same_process and value is not None and not over_budget,
                pid=pid,
                start_ticks=before,
                reason="process_probe_budget_exhausted"
                if over_budget
                else (
                    "process_identity_changed_or_unreadable"
                    if not same_process
                    else ("smaps_component_unreadable" if value is None else None)
                ),
            )
    for row in selected_rows + sentrix_rows + model_rows + product_rows + auxiliary_rows:
        row["pss_mib"], row["smaps_rss_mib"] = observations[int(row["pid"])]
    model_pids = {row["pid"] for row in model_rows}
    model_pss = _sum_mib(model_rows, "pss_mib")
    sentrix_pss = _sum_mib(sentrix_rows, "pss_mib")
    auxiliary_pids = {row["pid"] for row in auxiliary_rows}
    # Share exactly one ledger observation across main and auxiliary totals.
    # Keep its original timestamp even when the global cache is reused.
    with _nvmap_lock:
        identities = {row["pid"]: row.get("start_ticks") for row in selected_rows}
        previous_identities = _nvmap_cache.get("process_identities") or {}
        if any(previous_identities.get(pid) != identity for pid, identity in identities.items()):
            _nvmap_cache["at"] = float("-inf")
        ledger = _nvmap_clients_mib()
        _nvmap_cache["process_identities"] = identities
        nvmap_observation = dict(_nvmap_cache.get("observation") or {})
        model_gpu, gpu_source = _process_gpu_memory_mib(model_pids, ledger=ledger)
        auxiliary_gpu, _ = _process_gpu_memory_mib(auxiliary_pids, ledger=ledger)
        product_gpu, _ = _process_gpu_memory_mib({row["pid"] for row in selected_rows}, ledger=ledger)
        device_gpu = _nvmap_cache.get("total_mib") if ledger is not None else None

    memory_observations["nvmap"] = nvmap_observation
    for row in dedupe_rows(model_rows + sentrix_rows):
        if _process_start_ticks(int(row["pid"])) != row.get("start_ticks"):
            memory_observations[f"process:{row['pid']}:identity"].update(
                status="unavailable", reason="process_identity_changed_after_observation"
            )
    auxiliary_expected = (
        bool(
            embedding.get("local") is not False
            and (profile.get("ports") or profile.get("pids") or profile.get("keywords"))
        )
        if scope
        else bool(auxiliary_rows)
    )
    auxiliary_uma = auxiliary_gpu if auxiliary_rows or auxiliary_expected else 0.0
    main_complete = bool(model_rows) and (not scope or service_resolved(rows, main_spec))
    data["root_pid"] = next(
        (
            row["pid"]
            for row in model_rows
            if str(endpoint_port) in {str(p) for p in row.get("listening_ports") or []}
        ),
        None,
    )
    data["process_memory_used_mib"] = model_pss if main_complete else None
    data["model_process_memory_used_mib"] = data["process_memory_used_mib"]
    data["model_process_system_memory_used_mib"] = data["process_memory_used_mib"]
    data["model_process_host_memory_mib"] = data["process_memory_used_mib"]
    data["model_process_system_memory_scope"] = "selected_model_pss"
    data["model_process_host_memory_scope"] = "selected_model_pss"
    data["model_process_gpu_memory_mib"] = model_gpu if main_complete else None
    data["model_process_gpu_memory_source"] = gpu_source
    data["device_gpu_memory_mib"] = device_gpu
    data["model_system_processes"] = [
        {key: row.get(key) for key in ("pid", "process_name", "rss_mib", "pss_mib", "listening_ports")}
        for row in model_rows
    ]
    roles_complete = not scope or all(
        service_resolved(rows, spec)
        for spec in [dict(scope.get("sentrix") or {}), profile, *(scope.get("product") or [])]
        if isinstance(spec, dict)
    )
    sentrix_pss_complete = (
        roles_complete
        and (bool(sentrix_rows) or bool(scope))
        and all(row.get("pss_mib") is not None for row in sentrix_rows)
    )
    if not sentrix_rows and sentrix_pss_complete:
        sentrix_pss = 0.0
    model_pss_complete = main_complete and all(row.get("pss_mib") is not None for row in model_rows)
    if sentrix_pss is not None and sentrix_pss_complete:
        data["sentrix_stack_pss_mib"] = sentrix_pss
        product_pss = _sum_mib(product_rows, "pss_mib")
        auxiliary_pss = _sum_mib(auxiliary_rows, "pss_mib")
        data["sentrix_product_pss_mib"] = (
            product_pss if product_pss is not None else (0.0 if not product_rows else None)
        )
        data["sentrix_auxiliary_model_pss_mib"] = (
            auxiliary_pss if auxiliary_pss is not None else (0.0 if not auxiliary_rows else None)
        )
        data["sentrix_auxiliary_model_uma_mib"] = (
            round(float(auxiliary_uma), 2) if auxiliary_uma is not None else None
        )
        data["sentrix_stack_pss_scope"] = "sentrix_related_pss_excluding_llama"
        data["sentrix_processes"] = [
            {key: row.get(key) for key in ("pid", "process_name", "rss_mib", "pss_mib", "listening_ports")}
            for row in sentrix_rows[:50]
        ]
    data["sentrix_auxiliary_model_uma_mib"] = auxiliary_uma
    if auxiliary_expected and not auxiliary_rows:
        data["sentrix_auxiliary_model_pss_mib"] = None
    data["product_stack_memory_mib"] = None
    if sentrix_pss_complete and model_pss_complete:
        product = component_total([sentrix_pss, model_pss])
        data["product_stack_memory_mib"] = product
        data["benchmark_process_memory_used_mib"] = product
    data["benchmark_process_memory_scope"] = "all_related_pss"
    data["product_stack_gpu_memory_mib"] = product_gpu if roles_complete and main_complete else None
    data["benchmark_process_gpu_memory_mib"] = data["product_stack_gpu_memory_mib"]
    data["benchmark_process_gpu_memory_scope"] = "nvmap_related_pid_allocations"
    data["benchmark_processes"] = [
        {
            key: row.get(key)
            for key in (
                "pid",
                "start_ticks",
                "process_name",
                "rss_mib",
                "smaps_rss_mib",
                "pss_mib",
                "listening_ports",
            )
        }
        for row in selected_rows
    ]
    data["memory_complete"] = (
        data.get("model_process_system_memory_used_mib") is not None
        and data.get("product_stack_memory_mib") is not None
    )

    def refs(selected: list[dict[str, Any]], basis: str = "pss") -> list[str]:
        if not selected:
            # Confirmed absence also expires with the topology observation.
            return ["topology"]
        return list(
            dict.fromkeys(
                [
                    "topology",
                    *(f"process:{row['pid']}:identity" for row in selected),
                    *(f"process:{row['pid']}:{basis}" for row in selected),
                ]
            )
        )

    main_refs, stack_refs, auxiliary_refs = refs(model_rows), refs(sentrix_rows), refs(auxiliary_rows)
    main_gpu_refs = [*refs(model_rows, "identity"), "nvmap"]
    auxiliary_gpu_refs = [*refs(auxiliary_rows, "identity"), "nvmap"]
    product_gpu_refs = [*refs(selected_rows, "identity"), "nvmap"]
    dependencies = {
        "model_process_memory_used_mib": main_refs,
        "model_process_host_memory_mib": main_refs,
        "model_process_gpu_memory_mib": main_gpu_refs,
        "model_process_system_memory_used_mib": main_refs,
        "sentrix_stack_pss_mib": stack_refs,
        "sentrix_product_pss_mib": refs(product_rows),
        "sentrix_auxiliary_model_pss_mib": auxiliary_refs,
        "sentrix_auxiliary_model_uma_mib": auxiliary_gpu_refs,
        "product_stack_memory_mib": list(dict.fromkeys(main_refs + stack_refs)),
    }
    dependencies["benchmark_process_memory_used_mib"] = dependencies["product_stack_memory_mib"]
    dependencies["product_stack_gpu_memory_mib"] = product_gpu_refs
    dependencies["benchmark_process_gpu_memory_mib"] = product_gpu_refs
    dependencies["device_gpu_memory_mib"] = ["nvmap"]
    data.update(
        memory_observation_version=1,
        memory_observations=memory_observations,
        memory_dependencies=dependencies,
        process_probe_seconds=time.monotonic() - started,
        process_probe_pending=probe.pending_work() if probe is not None else [],
        pss_sampled_at_monotonic=started,
    )
    return apply_jetson_memory_metadata(data)


def is_jetson_host() -> bool:
    """本机是不是 Jetson：读 ``/proc/device-tree/model`` 里有没有 ``jetson``。

    这是**能力信号**，不是配置项——1.0 早期用主机名/固定地址判，换台机器就错。
    """
    try:
        return (
            "jetson"
            in Path("/proc/device-tree/model")
            .read_bytes()
            .replace(b"\0", b"")
            .decode("utf-8", "replace")
            .lower()
        )
    except OSError:
        return False


JETSON_SAMPLE_INTERVAL_SECONDS = max(
    0.1, float(os.environ.get("PHOTOBENCH_JETSON_SAMPLE_INTERVAL_SECONDS", "1.0"))
)


def _meminfo_mib() -> dict[str, Any]:
    values: dict[str, float] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            name, _, rest = line.partition(":")
            if name in {"MemTotal", "MemAvailable", "MemFree"}:
                values[name] = float(rest.split()[0]) / 1024.0
    except (OSError, ValueError, IndexError):
        return {}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    used = physical_memory_used(total, values.get("MemFree"))
    if used is None or total is None:
        return {}
    return {
        "system_memory_used_mib": used,
        "system_memory_total_mib": round(total, 3),
        "system_memory_available_mib": round(available, 3) if available is not None else None,
        "system_memory_free_mib": values.get("MemFree"),
        "system_memory_scope": "linux_physical_nonfree_including_cache_v2",
    }


def _parse_tegrastats_line(line: str) -> dict[str, Any]:
    sample: dict[str, Any] = {}
    for key, pattern, scale in (
        ("system_memory_used_mib", r"RAM\s+(\d+)/(\d+)MB", 1),
        ("gpu_utilization_pct", r"GR3D_FREQ\s+(\d+)%", 1),
        ("temperature_c", r"\bgpu@([\d.]+)C", 1),
    ):
        match = re.search(pattern, line)
        if not match:
            continue
        sample[key] = round(float(match.group(1)) * scale, 3)
        if key == "system_memory_used_mib":
            sample["system_memory_total_mib"] = round(float(match.group(2)) * scale, 3)
            sample["system_memory_available_mib"] = round(
                max(0.0, sample["system_memory_total_mib"] - sample[key]), 3
            )
            sample["system_memory_scope"] = "tegrastats_ram_legacy"
    rails = {
        name: round(float(current) / 1000, 3)
        for name, current in re.findall(r"((?:VDD|VIN)_[A-Z0-9_]+)\s+(\d+)mW", line)
    }
    if rails:
        sample["power_rails_average_w"] = {
            name: round(float(average) / 1000, 3)
            for name, average in re.findall(r"((?:VDD|VIN)_[A-Z0-9_]+)\s+\d+mW/(\d+)mW", line)
        }
        sample["power_rails_w"] = rails
        agx_inputs = ("VDD_GPU_SOC", "VDD_CPU_CV", "VIN_SYS_5V0")
        if "VDD_IN" in rails:
            sample.update(power_draw_w=rails["VDD_IN"], power_rail="VDD_IN", power_scope="module_input")
        elif all(name in rails for name in agx_inputs):
            sample.update(
                power_draw_w=round(sum(rails[name] for name in agx_inputs), 3),
                power_rail="+".join(agx_inputs),
                power_scope="module_estimate",
            )
        else:
            sample.update(power_draw_w=None, power_scope="module_inputs_incomplete")
    cpu = re.search(r"cpu@([\d.]+)C", line)
    if cpu:
        sample["cpu_temperature_c"] = round(float(cpu.group(1)), 3)
    return sample


def _gpu_temperature(root: Path | None = None) -> dict[str, Any]:
    """Read the GPU thermal zone; idle EAGAIN remains an explicit missing value."""
    result: dict[str, Any] = {
        "temperature_c": None,
        "gpu_temperature_method": "jetson_gpu_thermal_sysfs",
        "gpu_temperature_observed_at_monotonic": time.monotonic(),
    }
    reason = "gpu_temperature_sensor_missing"
    for zone in sorted((root or Path("/sys/class/thermal")).glob("thermal_zone*")):
        try:
            if (zone / "type").read_text(encoding="ascii").strip().lower() != "gpu-thermal":
                continue
            result["gpu_temperature_sensor"] = str(zone / "temp")
            # Buffered text reads can turn a sysfs EAGAIN into TypeError on
            # Python 3.10. os.read preserves the kernel's actual errno.
            fd = os.open(zone / "temp", os.O_RDONLY)
            try:
                celsius = float(os.read(fd, 128).decode("ascii").strip()) / 1000
            finally:
                os.close(fd)
            if not 0 < celsius <= 150:
                reason = "gpu_temperature_invalid_reading"
                break
            result["temperature_c"] = round(celsius, 3)
            return result
        except OSError as error:
            reason = (
                "gpu_temperature_sensor_temporarily_unavailable"
                if error.errno == errno.EAGAIN
                else "gpu_temperature_sensor_read_failed"
            )
        except (ValueError, UnicodeError):
            reason = "gpu_temperature_invalid_reading"
    result["resource_missing_reasons"] = {"temperature_c": reason}
    return result


class _TegrastatsStream:
    """一个长驻的 ``tegrastats`` 进程，0.5 秒的采样不再每次都 fork 一个子进程。"""

    def __init__(self, interval_ms: int):
        self.interval_ms = max(100, int(interval_ms))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._latest = ""
        self._observed_at = float("-inf")
        self._restart_at = 0.0
        self._failures = 0

    def latest_line(self) -> str:
        self._ensure()
        with self._lock:
            if (
                self._proc is None
                or self._proc.poll() is not None
                or time.monotonic() - self._observed_at > 3 * self.interval_ms / 1000
            ):
                return ""
            return self._latest

    def _ensure(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if time.monotonic() < self._restart_at:
                return
            self._latest = ""
            self._stop.clear()
            self._restart_at = time.monotonic() + min(30, 2 ** min(self._failures, 5))
            self._failures += 1
            try:
                self._proc = subprocess.Popen(
                    ["tegrastats", "--interval", str(self.interval_ms)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except OSError:
                # 没有 tegrastats（比如非 Jetson 或没装）——不抛，留给调用方按「没有
                # 设备读数」处理。起不来一个可选的采样器不该让评测起不来。
                self._proc = None
                return
            self._thread = threading.Thread(target=self._read, daemon=True, name="tegrastats-reader")
            self._thread.start()

    def _read(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if self._stop.is_set():
                break
            text = line.strip()
            if text:
                with self._lock:
                    if self._proc is proc:
                        self._latest = text
                        self._observed_at = time.monotonic()
                        self._failures = 0


_TEGRASTATS = _TegrastatsStream(int(JETSON_SAMPLE_INTERVAL_SECONDS * 1000))


class LocalJetsonLlamaCppTelemetryProvider:
    """采样一个**被核验过**的本机 llama-server 进程，绝不对一个任意远端 PID 采样。

    设备读数与 PSS 走与 153 NVML 相同的 1s 节奏；tegrastats 保持单个长驻进程。
    **缺 PSS / KV 就留空**，绝不把整机 RAM 标成 GPU VRAM（Orin 是 UMA，本来就没有
    独立 VRAM——这是这一路与 ``HostNvidiaTelemetryProvider`` 最根本的区别）。
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        pid_file: str | None = None,
        pss_interval: float | None = None,
        telemetry_services: Mapping[str, Any] | None = None,
    ):
        self.telemetry_services = telemetry_services
        self._cpu = HostCpuSampler()
        self._memory_probe = BoundedProbe(limit=4)
        self._topology_probe = BoundedProbe(limit=1)
        self._topology_snapshot: dict[str, Any] = {}
        self._optional_probe = BoundedProbe(limit=1)
        self._kv_observation: dict[str, Any] = {}
        self._memory_worker: JetsonMemoryWorker | None = None
        self._worker_requested = False
        self._worker_error: str | None = None
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.port:
            raise ValueError("a local llama.cpp endpoint with an explicit port is required")
        self.endpoint = f"{parsed.scheme}://{parsed.netloc}"
        self.port = parsed.port
        self.pid_file = Path(
            pid_file
            or os.environ.get(
                "PHOTOBENCH_LLAMA_PID_FILE",
                "/home/orin/VLM/gemma4/results/orin/server_photobench_8100.pid",
            )
        )
        self.pss_interval = (
            JETSON_SAMPLE_INTERVAL_SECONDS if pss_interval is None else max(0.0, float(pss_interval))
        )
        self.device_interval = self.pss_interval
        self._last_device_probe = 0.0
        self._device_sample: dict[str, Any] = {}
        self._last_pid: int | None = None
        self._system_baseline_mib: float | None = None
        self._last_memory: dict[str, Any] = {"status": "unavailable", "reason": "not_sampled"}

    def _pid_from_proc(self, pid: int) -> int:
        """只认「comm 是 llama-server 且 ``--port`` 就是选中端点那个端口」的 PID。

        1.0 的四道闸门之一：PID 文件可能过期、可能被别的进程复用了那个号。
        """
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if not args or Path(os.fsdecode(args[0])).name != "llama-server":
            raise ValueError("PID does not belong to llama-server")
        command = [os.fsdecode(arg) for arg in args if arg]
        if "--port" not in command or command[command.index("--port") + 1] != str(self.port):
            raise ValueError("PID is not serving the selected endpoint port")
        if str(self.port) not in _listening_ports_by_pid({str(self.port)}).get(pid, set()):
            raise ValueError("PID no longer owns the selected listening socket")
        return pid

    def _pid_from_port(self) -> int:
        # PID-file writes can be denied (or the file can be stale). The socket
        # inventory already knows the candidates; do not inspect every cmdline.
        for pid in _listening_ports_by_pid({str(self.port)}):
            try:
                return self._pid_from_proc(pid)
            except (ValueError, FileNotFoundError, PermissionError, OSError):
                continue
        raise ValueError(f"no llama-server is listening on port {self.port}")

    def _pid(self) -> int:
        try:
            raw = self.pid_file.read_text(encoding="ascii").strip()
            if raw.isdecimal():
                return self._pid_from_proc(int(raw))
        except (OSError, ValueError):
            pass
        pid = self._pid_from_port()
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(f"{pid}\n", encoding="ascii")
        except OSError:
            pass
        return pid

    def _llama_args(self, pid: int) -> dict[str, Any]:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        args = [os.fsdecode(item) for item in raw if item]

        def flag(*names: str) -> str | None:
            for name in names:
                if name in args:
                    index = args.index(name)
                    if index + 1 < len(args):
                        return args[index + 1]
            return None

        return {
            "ctx_size": flag("--ctx-size", "-c"),
            "parallel": flag("--parallel", "-np"),
            "n_gpu_layers": flag("--n-gpu-layers", "-ngl"),
            "cache_ram": flag("--cache-ram"),
            "model": flag("--model", "-m"),
            "cmdline": " ".join(args)[:2000],
        }

    def _metrics(self) -> dict[str, Any]:
        """llama.cpp 的 ``/metrics`` 里那几个 ``llamacpp:kv_cache_*``。探不到就什么都不加。"""
        data: dict[str, Any] = {}
        try:
            # Optional KV counters must not consume the core-memory sampling
            # budget when the inference server is busy with a visual request.
            response = httpx.get(
                f"{self.endpoint}/metrics", timeout=0.2, trust_env=False, follow_redirects=False
            )
            response.raise_for_status()
            values: dict[str, float] = {}
            for line in response.text.splitlines():
                if line.startswith("llamacpp:kv_cache_") and "{" not in line:
                    name, _, value = line.partition(" ")
                    try:
                        values[name] = float(value.strip().split()[0])
                    except (ValueError, IndexError):
                        continue
            ratio = values.get("llamacpp:kv_cache_usage_ratio")
            if ratio is not None and 0 <= ratio <= 1:
                data["vllm_metrics"] = {"kv_cache_usage_pct": round(ratio * 100, 2)}
            tokens = values.get("llamacpp:kv_cache_tokens")
            if tokens is not None and tokens >= 0:
                data["kv_cache_used_tokens"] = int(tokens)
        except (httpx.HTTPError, ValueError):
            pass
        return data

    def _device(self) -> dict[str, Any]:
        # Revalidate stream liveness on every read; a device cache must not extend
        # the source TTL or keep a terminated tegrastats process alive on paper.
        self._last_device_probe = time.monotonic()
        sample = _meminfo_mib()
        line = _TEGRASTATS.latest_line()
        if line:
            parsed = _parse_tegrastats_line(line)
            parsed.pop("system_memory_used_mib", None)
            parsed.pop("system_memory_total_mib", None)
            parsed.pop("system_memory_available_mib", None)
            parsed.pop("system_memory_scope", None)
            sample.update(parsed)
            sample["device_observed_at_monotonic"] = _TEGRASTATS._observed_at
            sample["device_observation_age_seconds"] = time.monotonic() - _TEGRASTATS._observed_at
            sample["device_observation_ttl_seconds"] = 3 * _TEGRASTATS.interval_ms / 1000
        sample.update(_gpu_temperature())
        self._device_sample = sample
        return self._device_sample

    def gpu_stats(self) -> dict[str, Any]:
        # Optional HTTP is isolated from the core sampler; at most one request in flight.
        ready = self._optional_probe.collect({"kv": self._metrics}, budget=0.0)
        if "kv" in ready:
            self._kv_observation = ready["kv"]
        observation = self._kv_observation
        fresh = time.monotonic() - float(observation.get("started", float("-inf"))) <= 3 * self.pss_interval
        data: dict[str, Any] = dict(observation.get("value") or {}) if fresh else {}
        data["kv_observed_at_monotonic"] = observation.get("started")
        self._last_memory = {"status": "available", "source": "jetson_local_pss", "data": data}
        return {
            "status": "available",
            "source": "jetson_local_pss",
            "data": {
                "gpus": [
                    {
                        "index": 0,
                        "protocol_version": RESOURCE_TELEMETRY_VERSION,
                        "memory_accounting": "jetson_pss_and_nvmap_v3",
                        "memory_unit": "process_pss_mib",
                        "memory_scope": "process_pss_separate_gpu_allocation",
                        **self._device(),
                    }
                ]
            },
        }

    def pending_work(self) -> list[str]:
        return [
            *self._memory_probe.pending_work(),
            *self._topology_probe.pending_work(),
            *self._optional_probe.pending_work(),
            *(self._memory_worker.pending_work() if self._memory_worker is not None else []),
        ]

    def start_sampling(self) -> None:
        self._worker_requested = True
        self._worker_error = None
        try:
            if self._memory_worker is not None and self._memory_worker.pending_work():
                raise RuntimeError("previous Jetson memory worker is still running")
            self._memory_worker = JetsonMemoryWorker(str(self.port), self.telemetry_services)
            self._memory_worker.start()
        except Exception as error:
            self._worker_error = f"{type(error).__name__}: {error}"

    def stop_sampling(self) -> None:
        if self._memory_worker is not None:
            self._memory_worker.close()

    def process_memory(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = self._last_memory.get("data") or {}
            if self._worker_requested:
                if self._worker_error is not None or self._memory_worker is None:
                    raise RuntimeError(self._worker_error or "Jetson memory worker unavailable")
                augmented = {**data, **self._memory_worker.sample()}
            else:
                augmented = _augment_local_process_memory(
                    data,
                    model_pid=None,
                    endpoint_port=str(self.port),
                    telemetry_services=self.telemetry_services,
                    probe=self._memory_probe,
                    topology_probe=self._topology_probe,
                    topology_snapshot=self._topology_snapshot,
                )
            pid = augmented.get("root_pid")
            identity = (pid, _process_start_ticks(pid)) if isinstance(pid, int) else None
            if identity != getattr(self, "_baseline_identity", None):
                self._baseline_identity = identity
                self._system_baseline_mib = None
            used = self._device_sample.get("system_memory_used_mib")
            if self._system_baseline_mib is None and isinstance(used, (int, float)):
                self._system_baseline_mib = float(used)
            if self._system_baseline_mib is not None and isinstance(used, (int, float)):
                augmented["system_memory_baseline_mib"] = self._system_baseline_mib
                augmented["system_memory_delta_mib"] = round(float(used) - self._system_baseline_mib, 2)
            if isinstance(pid, int):
                with contextlib.suppress(OSError, ValueError, IndexError):
                    augmented["llama_server_args"] = self._llama_args(pid)
            augmented, _ = memory_freshness({**augmented, "_t": time.monotonic()})
            augmented.pop("_t", None)
            augmented["process_memory_used_mib"] = augmented.get(
                "model_process_memory_used_mib", augmented.get("process_memory_used_mib")
            )
            return {
                "status": "available",
                "source": "jetson_local_pss",
                "data": apply_jetson_memory_metadata(augmented),
            }
        except Exception as error:
            return {"status": "unavailable", "source": "jetson_local_pss", "reason": str(error)}

    def system_memory(self) -> dict[str, Any]:
        started = time.monotonic()
        data: dict[str, Any] = _meminfo_mib()
        keys = {
            "system_memory_used_mib",
            "system_memory_total_mib",
            "system_memory_available_mib",
            "system_memory_scope",
        }
        free = data.get("system_memory_free_mib")
        data = {key: value for key, value in data.items() if key in keys}
        if "system_memory_used_mib" not in data:
            return {"status": "unavailable", "source": "proc_meminfo", "reason": "ram_sample_unavailable"}
        data.update(cpu_utilization_pct=self._cpu.sample(), cpu_observed_at_monotonic=time.monotonic())
        data["system_observation"] = _observation(
            "proc_meminfo",
            started,
            available=True,
            total_mib=data.get("system_memory_total_mib"),
            free_mib=free,
        )
        return {"status": "available", "source": "proc_meminfo", "data": data}

    def kv_cache(self) -> dict[str, Any]:
        data: dict[str, Any] = self._last_memory.get("data") or {}
        observed = data.get("kv_observed_at_monotonic")
        if observed is None or time.monotonic() - float(observed) > 3 * self.pss_interval:
            return {"status": "unavailable", "source": "llamacpp_metrics", "reason": "kv_metrics_stale"}
        if "kv_cache_used_tokens" not in data and "vllm_metrics" not in data:
            return {"status": "unavailable", "source": "llamacpp_metrics", "reason": "kv_metrics_not_exposed"}
        return {
            "status": "available",
            "source": "llamacpp_metrics",
            "data": {
                "used_tokens": data.get("kv_cache_used_tokens"),
                "usage_pct": (data.get("vllm_metrics") or {}).get("kv_cache_usage_pct"),
                "used_bytes": None,
            },
        }


__all__ = [
    "JETSON_SAMPLE_INTERVAL_SECONDS",
    "LocalJetsonLlamaCppTelemetryProvider",
    "SYSTEM_PROCESS_KEYWORDS",
    "SYSTEM_PROCESS_PORTS",
    "is_jetson_host",
]

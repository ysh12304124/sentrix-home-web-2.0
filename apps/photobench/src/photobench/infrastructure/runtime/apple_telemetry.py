"""Apple Silicon telemetry with separately versioned resident group accounting.

System RAM includes resident caches. Model/product estimates use footprint's
cross-process deduplicated summary: dirty - swapped + clean + reclaimable.
Compressed/swapped logical pages and file mapping lengths are never added.
The physical compressor store remains part of system RAM, without inventing
per-process physical attribution. GPU counters remain device diagnostics.
"""

from __future__ import annotations

import atexit
import ctypes
import json
import logging
import math
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from photobench.domain.runtime.metrics import (
    APPLE_POWER_LIMIT_W,
    APPLE_TEMPERATURE_MAX_C,
    APPLE_TEMPERATURE_MIN_C,
    MEMORY_OBSERVATION_TTL_SECONDS,
    RESOURCE_TELEMETRY_VERSION,
    apple_power,
    apple_resident_bytes,
    apple_temperature,
    memory_freshness,
    physical_memory_used,
)
from photobench.infrastructure.runtime.bounded_probe import BoundedProbe
from photobench.infrastructure.runtime.hardware import HostCpuSampler
from photobench.infrastructure.runtime.telemetry_scope import (
    dedupe_rows as _dedupe_rows,
)
from photobench.infrastructure.runtime.telemetry_scope import (
    load_telemetry_services as _load_telemetry_services,
)
from photobench.infrastructure.runtime.telemetry_scope import service_resolved
from photobench.infrastructure.runtime.telemetry_scope import (
    service_rows as _service_rows,
)

# 与 providers.py 的同名常量**刻意不同源**：1.0 这里是一份写死的 set/tuple，
# 不读 PHOTOBENCH_SYSTEM_PROCESS_* 环境变量，而且关键词多一个 "mlx"。照搬这个差别，
# 不合并——合并会悄悄改变 Apple 主机上的进程归属结果。
# **端口跟着 S9b-1 改过（8771 → 8772、8091 → 8191）**，理由与 ``providers.py`` 的同名
# 常量一样：2.0 的 PhotoBench 是 8772、主 API 是 8191（设计文档 §5.1）。「不与
# providers 共用」这条差别说的是**关键词**（多一个 "mlx"、不读环境变量），端口不是
# 差别的一部分——留 1.0 的端口号只会让 Apple 主机上的归属判据在 2.0 上过期。
SYSTEM_PROCESS_PORTS = {"8772", "8191", "8500", "8501", "8100", "8101", "6333"}
SYSTEM_PROCESS_KEYWORDS = (
    "photobench",
    "benchmark_orchestrator.py",
    "sentrix",
    "vllm",
    "llama-server",
    "ollama",
    "qdrant",
    "mlx",
)

log = logging.getLogger("photobench.apple_telemetry")

# macmon 0.8.2 把 IOReport 的 "Energy Model" 原始计数器差分除以采样间隔算成功率，
# 全程没有校验；macOS 27 冻结该计数器后，下一次 flush 会把积压的巨大增量倒进一个
# 很短的间隔。实测同一台 Mac mini M4 空闲样本出现 cpu_power 8825 W、ram_power
# 413 W，而 100% GPU 持续负载的合法峰值只有约 66 W。Mac mini 不可能超过电源功率。
POWER_LIMIT_W = APPLE_POWER_LIMIT_W
# macmon 对读不到的传感器返回 -1；0.8.2 的 GPU 温度还会偶发 0.8 °C（实测
# gpu_temp_avg=0.8000000715255737，同时 cpu_temp_avg=41.1）。有源芯片不可能低于
# 环境温度，0 °C 下限太松——0.8 会被当成合理值，在曲线上留下一个可见的坑。
TEMPERATURE_MIN_C = APPLE_TEMPERATURE_MIN_C
TEMPERATURE_MAX_C = APPLE_TEMPERATURE_MAX_C

# 这些功率键都来自 macmon 未校验的能量通路，按**一组**校验：任何一个不可能值
# 都证明这个间隔的能量记账坏了。DRAM 计数器与 sys_power 独立，正是有用的金丝雀。
MACMON_POWER_KEYS = ("sys_power", "cpu_power", "gpu_power", "ane_power", "ram_power", "gpu_ram_power")

# 0.8.2 是最后一个仍走未校验 IOReport 能量通路的版本；0.9.0 引入 CLPC 源与
# finite/非负守卫。低于这个版本就完全不发布功率读数是必须的：POWER_LIMIT_W 只能
# 拒绝不可能的量级，一次恰好落在 150 W 以下的 flush 无法与真实 100 W 负载区分。
MACMON_MIN_VERSION = (0, 9, 0)

# parse_macmon_sample 从 macmon 功率通道派生的字段，即旧版本必须整组丢弃的那些。
# 温度刻意不在其中：0.9.0 与 0.8.2 的温度读数一致，版本门禁只针对能量计数器。
MACMON_POWER_OUTPUT_KEYS = ("power_draw_w", "power_scope", "cpu_power_w", "gpu_power_w")


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}


def parse_vm_stat(text: str, page_size: int, total_bytes: int | None = None) -> dict[str, Any] | None:
    """Physical non-free RAM including caches; missing counters stay unknown."""
    pages: dict[str, int] = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        token = rest.strip().rstrip(".").split()
        if not token:
            continue
        try:
            pages[name.strip().lower()] = int(token[0])
        except ValueError:
            continue
    free = pages.get("pages free")
    used_mib = physical_memory_used(
        total_bytes / 1048576 if total_bytes else None,
        free * page_size / 1048576 if free is not None and page_size > 0 else None,
    )
    if used_mib is None or total_bytes is None or free is None:
        return None
    return {
        "system_memory_used_mib": used_mib,
        "system_memory_total_mib": round(total_bytes / 1048576, 2),
        "system_memory_available_mib": round(free * page_size / 1048576, 2),
        "system_memory_scope": "apple_physical_nonfree_including_cache_v2",
    }


def _finite_float(value: Any) -> float | None:
    """有限浮点，其余（bool / 字符串 / NaN / inf）一律 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _plausible_power(value: Any) -> float | None:
    """丢掉 IOReport 哨兵（负数）与物理上不可能的功耗。"""
    return apple_power(value)


def _plausible_temperature(value: Any) -> float | None:
    """Same validation in fresh sampling and historical projections."""
    return apple_temperature(value)


_power_reject_count = 0
_power_reject_lock = threading.Lock()
_macmon_version_warned = False
_macmon_version_lock = threading.Lock()


def _warn_implausible_power(payload: dict[str, Any], rejected: list[str]) -> None:
    """丢弃功率样本时记一条日志，但不刷屏。"""
    global _power_reject_count
    with _power_reject_lock:
        _power_reject_count += 1
        count = _power_reject_count
    if count <= 5 or count % 100 == 0:
        detail = ", ".join(f"{key}={payload.get(key)}" for key in rejected)
        log.warning(
            "macmon power reading implausible (%s); dropped power for this sample (%d rejected so far)",
            detail,
            count,
        )


def parse_macmon_version(text: str) -> tuple[int, int, int] | None:
    """从 ``macmon --version`` 输出读 ``(major, minor, patch)``；读不出返回 None。"""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def macmon_power_is_trusted(version: tuple[int, int, int] | None) -> bool:
    """只有**已知**低于 MACMON_MIN_VERSION 的版本才不可信。

    读不出版本不是「旧版本」的证据（未来版本可能改 flag），所以保留功率、仅由
    POWER_LIMIT_W 兜底。读到且低于下限才是证据。
    """
    if version is None:
        return True
    return tuple(version) >= MACMON_MIN_VERSION


def _drop_power_readings(data: dict[str, Any]) -> dict[str, Any]:
    """去掉 macmon 的功率字段，保留温度与利用率。"""
    return {key: value for key, value in data.items() if key not in MACMON_POWER_OUTPUT_KEYS}


def macmon_version(binary: str) -> tuple[int, int, int] | None:
    """向已安装的 macmon 问版本；读不出返回 None。"""
    return parse_macmon_version(_run_text([binary, "--version"], timeout=5))


def parse_macmon_sample(payload: Any) -> dict[str, Any]:
    """一行 ``macmon pipe`` 的 JSON → PhotoBench 的采样字段。非 dict 输入返回空表。

    功率按**一组**校验：任何一个不可能值都说明这个间隔的能量记账坏了，整组丢弃。
    """
    if not isinstance(payload, dict):
        return {}
    data: dict[str, Any] = {}
    temp = payload.get("temp")
    if not isinstance(temp, dict):
        temp = {}
    cpu_temp = _plausible_temperature(temp.get("cpu_temp_avg"))
    gpu_temp = _plausible_temperature(temp.get("gpu_temp_avg"))
    if cpu_temp is not None:
        data["cpu_temperature_c"] = round(cpu_temp, 3)
    if gpu_temp is not None:
        data["temperature_c"] = round(gpu_temp, 3)

    power: dict[str, float] = {}
    rejected: list[str] = []
    for key in MACMON_POWER_KEYS:
        if key not in payload:
            continue
        value = _plausible_power(payload[key])
        if value is None:
            rejected.append(key)
        else:
            power[key] = value
    if rejected:
        _warn_implausible_power(payload, rejected)
    else:
        if "sys_power" in power:
            data["power_draw_w"] = round(power["sys_power"], 3)
            data["power_scope"] = "macmon_sys_power"
        for source_key, target_key in (("cpu_power", "cpu_power_w"), ("gpu_power", "gpu_power_w")):
            if source_key in power:
                data[target_key] = round(power[source_key], 3)

    ratio = _finite_float(payload.get("gpu_active_ratio"))
    if ratio is not None:
        data["gpu_utilization_pct"] = round(min(1.0, max(0.0, ratio)) * 100, 3)
    return data


class _MacmonStream:
    """一个长命的 ``macmon pipe -i 1000``。

    用 ``-s`` 起一次有限采样不行：那个进程会自己结束，之后的读数就永远是最后一行。
    读数线程只把**最新**一行留下——采样是「当前值」不是「历史」。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {}
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        # 版本探测通过前 fail closed：绝不能发布一个尚未核验的 macmon 样本。
        self._trust_power = False
        self._observed_at = float("-inf")
        self._restart_at = 0.0
        self._failures = 0

    def start(self) -> None:
        binary = shutil.which("macmon") or "/opt/homebrew/bin/macmon"
        if (
            not Path(binary).is_file()
            or (self._proc is not None and self._proc.poll() is None)
            or time.monotonic() < self._restart_at
        ):
            return
        self._restart_at = time.monotonic() + min(30, 2 ** min(self._failures, 5))
        self._failures += 1
        self._trust_power = self._check_version(binary)
        with self._lock:
            self._latest = {}
        try:
            self._proc = subprocess.Popen(
                [binary, "pipe", "-i", "1000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            log.warning("macmon failed to start", exc_info=True)
            self._proc = None
            return
        self._thread = threading.Thread(target=self._read, name="macmon-pipe", daemon=True)
        self._thread.start()
        atexit.register(self.stop)

    def _check_version(self, binary: str) -> bool:
        """这个 macmon 能否发布功率；不能时只警告一次。"""
        version = macmon_version(binary)
        trusted = macmon_power_is_trusted(version)
        if trusted and version is not None:
            return True
        global _macmon_version_warned
        with _macmon_version_lock:
            if _macmon_version_warned:
                return trusted
            _macmon_version_warned = True
        if version is None:
            log.warning(
                "could not read a version from %s (`macmon --version`); keeping power "
                "readings, bounded only by POWER_LIMIT_W",
                binary,
            )
            return True
        log.warning(
            "macmon %s is older than the required %s; its IOReport 'Energy Model' path "
            "divides a frozen macOS 27 energy delta by one short interval with no "
            "validation, so this process will not publish power readings (temperature "
            "is unaffected). Run `brew upgrade macmon`.",
            ".".join(str(part) for part in version),
            ".".join(str(part) for part in MACMON_MIN_VERSION),
        )
        return trusted

    def stop(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            proc.kill()

    def latest(self) -> dict[str, Any]:
        # 首次取数时懒启动：没装 macmon 的机器一次子进程都不会起。
        if self._proc is None or self._proc.poll() is not None:
            self.start()
        with self._lock:
            age = time.monotonic() - self._observed_at
            if self._proc is None or self._proc.poll() is not None or age > 3.0:
                return {}
            return {
                **self._latest,
                "device_observed_at_monotonic": self._observed_at,
                "device_observation_age_seconds": age,
                "device_observation_ttl_seconds": 3.0,
            }

    def _read(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            parsed = parse_macmon_sample(payload)
            if not parsed:
                continue
            if not self._trust_power:
                parsed = _drop_power_readings(parsed)
                if not parsed:
                    continue
            with self._lock:
                if self._proc is proc:
                    self._latest = parsed
                    self._observed_at = time.monotonic()
                    self._failures = 0


_MACMON = _MacmonStream()


def parse_agx_performance(text: str) -> dict[str, Any]:
    """读集成 GPU 的计数器。**缺席的计数器就是不写**，不是 0。"""
    data: dict[str, Any] = {}
    util = re.search(r'Device Utilization %"=(\d+(?:\.\d+)?)', text)
    if util:
        data["gpu_utilization_pct"] = round(float(util.group(1)), 3)
    allocated = re.search(r'"Alloc system memory"=(\d+)', text)
    if allocated:
        data["gpu_allocated_unified_memory_mib"] = round(int(allocated.group(1)) / 1048576, 2)
    in_use = re.search(r'"In use system memory"=(\d+)', text)
    if in_use:
        data["gpu_in_use_unified_memory_mib"] = round(int(in_use.group(1)) / 1048576, 2)
    return data


class _RUsageInfoV0(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
    ]


_LIBPROC: Any = None
_LIBPROC_LOCK = threading.Lock()


def _libproc() -> Any:
    global _LIBPROC
    if _LIBPROC is None:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_pid_rusage.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(_RUsageInfoV0),
        ]
        library.proc_pid_rusage.restype = ctypes.c_int
        _LIBPROC = library
    return _LIBPROC


def _process_identity(pid: int) -> int | None:
    try:
        info = _RUsageInfoV0()
        with _LIBPROC_LOCK:
            result = _libproc().proc_pid_rusage(int(pid), 0, ctypes.byref(info))
        return int(info.ri_proc_start_abstime) if result == 0 and info.ri_proc_start_abstime else None
    except (OSError, AttributeError, ValueError):
        return None


def parse_footprint(payload: Any, pids: set[int]) -> dict[str, int] | None:
    """Accept only complete, matching group summaries; normalize units once."""
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    processes = payload.get("processes")
    if not isinstance(processes, list) or any(not isinstance(row, dict) for row in processes):
        return None
    actual = [row.get("pid") for row in processes]
    if any(type(pid) is not int for pid in actual) or set(actual) != pids or len(actual) != len(pids):
        return None
    scale = payload.get("bytes per unit")
    expected = {"byte": 1, "bytes": 1, "page": payload.get("page size"), "pages": payload.get("page size")}
    if type(scale) is not int or scale <= 0 or expected.get(str(payload.get("unit"))) != scale:
        return None
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return None
    total = summary.get("total")
    if not isinstance(total, dict):
        return None
    components = {}
    for key in ("dirty", "swapped", "clean", "reclaimable"):
        value = total.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        components[key] = value * scale
    return components if apple_resident_bytes(components) is not None else None


def _resident_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.monotonic()
    pids = {int(row["pid"]) for row in rows}
    identities = {pid: _process_identity(pid) for pid in pids}
    result: dict[str, Any] = {
        "source": "footprint_group",
        "status": "unavailable",
        "observed_at_monotonic": started,
        "ttl_seconds": MEMORY_OBSERVATION_TTL_SECONDS,
        "pids": sorted(pids),
        "process_identities": identities,
    }
    try:
        if not pids or any(identity is None for identity in identities.values()):
            result["reason"] = "process_identity_unavailable"
            return result
        with tempfile.TemporaryDirectory(prefix="photobench-footprint-") as directory:
            target = Path(directory) / "group.json"
            command = ["/usr/bin/footprint", "--swapped", "-f", "bytes", "-j", str(target)]
            for pid in sorted(pids):
                command.extend(["-p", str(pid)])
            completed = subprocess.run(command, capture_output=True, text=True, timeout=0.8, check=False)
            if completed.returncode != 0:
                result["reason"] = "footprint_failed"
                return result
            components = parse_footprint(json.loads(target.read_text()), pids)
        if components is None:
            result["reason"] = "footprint_incomplete_or_invalid"
        elif any(_process_identity(pid) != identity for pid, identity in identities.items()):
            result["reason"] = "process_identity_changed"
        else:
            result.update(
                status="available",
                components_bytes=components,
                resident_bytes=apple_resident_bytes(components),
            )
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        result["reason"] = "footprint_unavailable_or_timeout"
        return result
    finally:
        result["finished_at_monotonic"] = time.monotonic()


def _run_text(command: list[str], timeout: float = 3) -> str:
    try:
        if command[:2] == ["ps", "-ax"]:
            # ps includes itself in its output. It exits before footprint collection,
            # so following PB descendants would otherwise invalidate EVERY total.
            # Exclude only this exact observer PID, never arbitrary ps processes.
            with subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            ) as observer:
                try:
                    output, _ = observer.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    observer.kill()
                    observer.communicate()
                    return ""
                return "\n".join(
                    line for line in output.splitlines() if line.split(None, 1)[:1] != [str(observer.pid)]
                )
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""


def _page_size() -> int:
    text = _run_text(["sysctl", "-n", "hw.pagesize"], timeout=0.2).strip()
    try:
        return int(text)
    except ValueError:
        return 0


def _total_bytes() -> int | None:
    text = _run_text(["sysctl", "-n", "hw.memsize"], timeout=0.2).strip()
    try:
        return int(text)
    except ValueError:
        return None


def _process_rows() -> list[dict[str, Any]]:
    text = _run_text(["ps", "-ax", "-o", "pid=", "-o", "ppid=", "-o", "command="], timeout=0.6)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split(None, 2)
        if len(fields) != 3:
            continue
        pid_text, ppid_text, command = fields
        if not pid_text.isdigit() or not ppid_text.isdigit() or not command:
            continue
        rows.append(
            {
                "pid": int(pid_text),
                "ppid": int(ppid_text),
                "command": command,
                "identity": command.lower()[:1000],
                "process_name": Path(command.split(" ", 1)[0]).name,
                "listening_ports": [],
            }
        )
    return rows


def _listening_ports(ports: set[str]) -> dict[int, set[str]]:
    """``lsof`` 的解析。

    ``-Fpn`` 是机器可读格式：``p<pid>`` 开一段，随后的 ``n<地址>`` 是它持有的一项。
    只认端口在本集合里的那些，其余地址行直接跳过。
    """
    command = ["lsof", "-nP", "-sTCP:LISTEN", "-Fpn"]
    for port in sorted(ports):
        command.extend(["-iTCP:" + port])
    text = _run_text(command, timeout=0.6)
    found: dict[int, set[str]] = {}
    pid: int | None = None
    for line in text.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif pid is not None and line.startswith("n"):
            match = re.search(r":(\d+)$", line[1:])
            if match and (not ports or match.group(1) in ports):
                found.setdefault(pid, set()).add(match.group(1))
    return found


def _is_related(row: dict[str, Any]) -> bool:
    ports = {str(port) for port in row.get("listening_ports") or []}
    if ports & SYSTEM_PROCESS_PORTS:
        return True
    identity = str(row.get("identity") or "")
    return any(keyword in identity for keyword in SYSTEM_PROCESS_KEYWORDS)


def _is_model_process(row: dict[str, Any], endpoint_port: str) -> bool:
    identity = str(row.get("identity") or "")
    ports = {str(port) for port in row.get("listening_ports") or []}
    if (
        endpoint_port
        and endpoint_port in ports
        and ("llama-server" in identity or "mlx" in identity or "vllm" in identity)
    ):
        return True
    return bool(endpoint_port and f"--port {endpoint_port}" in identity and "llama-server" in identity)


class LocalAppleUnifiedTelemetryProvider:
    """本机 Apple Silicon 遥测。三个方法的 ``status`` 口径见 domain 的 ``TelemetryProvider``。

    设备读数缓存1秒并保留来源时间；进程与监听端口逐轮发现。
    两个驻留组独立、有界采集，缺失或过期值留空。
    """

    source = "apple_unified"

    def __init__(self, endpoint_url: str = "", *, telemetry_services: Mapping[str, Any] | None = None):
        self.telemetry_services = telemetry_services
        parsed = urlparse(endpoint_url or "")
        self.endpoint_url = f"{parsed.scheme}://{parsed.netloc}"
        self._cpu = HostCpuSampler()
        self.port = str(parsed.port or "")
        self._last_memory: dict[str, Any] = {"status": "unavailable", "reason": "not_sampled"}
        self._device_sample: dict[str, Any] = {}
        self._last_device_probe = 0.0
        self._ports_by_pid: dict[int, set[str]] = {}
        self._last_port_probe = 0.0
        self._system_baseline_mib: float | None = None
        self._memory_probe = BoundedProbe(limit=2)

    def _device(self) -> dict[str, Any]:
        if self._device_sample and time.monotonic() - self._last_device_probe < 1.0:
            sample = {
                key: value
                for key, value in self._device_sample.items()
                if key
                not in {
                    "power_draw_w",
                    "cpu_power_w",
                    "gpu_power_w",
                    "temperature_c",
                    "cpu_temperature_c",
                    "device_observed_at_monotonic",
                    "device_observation_age_seconds",
                }
            }
            sample.update(_MACMON.latest())
            return sample
        self._last_device_probe = time.monotonic()
        gpu_started = time.monotonic()
        sample = parse_agx_performance(_run_text(["ioreg", "-r", "-c", "AGXAccelerator", "-l"], timeout=0.3))
        sample["device_gpu_memory_mib"] = sample.get("gpu_allocated_unified_memory_mib")
        sample["gpu_observation"] = {
            "source": "ioreg_alloc_system_memory",
            "status": "available" if sample.get("device_gpu_memory_mib") is not None else "unavailable",
            "observed_at_monotonic": gpu_started,
            "finished_at_monotonic": time.monotonic(),
            "ttl_seconds": MEMORY_OBSERVATION_TTL_SECONDS,
        }
        sample.update(_MACMON.latest())
        system_started = time.monotonic()
        memory = parse_vm_stat(_run_text(["vm_stat"], timeout=0.3), _page_size(), _total_bytes())
        sample["system_observation"] = {
            "source": "vm_stat",
            "status": "available" if memory else "unavailable",
            "observed_at_monotonic": system_started,
            "finished_at_monotonic": time.monotonic(),
            "ttl_seconds": MEMORY_OBSERVATION_TTL_SECONDS,
            "total_mib": memory.get("system_memory_total_mib") if memory else None,
            "free_mib": memory.get("system_memory_available_mib") if memory else None,
        }
        if memory:
            sample.update(memory)
        self._device_sample = sample
        return sample

    def _ports(self) -> dict[int, set[str]]:
        self._last_port_probe = time.monotonic()
        self._ports_by_pid = _listening_ports(set())
        return self._ports_by_pid

    def _augment(self) -> dict[str, Any]:
        device = self._device()
        topology_started = time.monotonic()
        ports = self._ports()
        rows = _process_rows()
        topology_finished = time.monotonic()
        for row in rows:
            row["listening_ports"] = sorted(ports.get(row["pid"], set()), key=int)
        scope = self.telemetry_services if self.telemetry_services is not None else _load_telemetry_services()
        if scope:
            main_spec = dict(scope.get("main_model") or {})
            model_rows = _service_rows(rows, main_spec) if main_spec.get("local") is not False else []
            model_pids = {row["pid"] for row in model_rows}
            embedding = dict(scope.get("embedding") or {})
            mode = str(embedding.get("mode") or "")
            profile = dict((embedding.get("profiles") or {}).get(mode) or {})
            if embedding.get("local") is False or profile.get("local") is False:
                profile = {}
            auxiliary_rows = [row for row in _service_rows(rows, profile) if row["pid"] not in model_pids]
            auxiliary_pids = {row["pid"] for row in auxiliary_rows}
            product_rows: list[dict[str, Any]] = []
            for product_spec in scope.get("product") or []:
                if isinstance(product_spec, dict):
                    product_rows.extend(_service_rows(rows, product_spec))
            product_rows.extend(_service_rows(rows, dict(scope.get("sentrix") or {})))
            product_rows = [
                row
                for row in _dedupe_rows(product_rows)
                if row["pid"] not in model_pids and row["pid"] not in auxiliary_pids
            ]
            sentrix_rows = _dedupe_rows(product_rows + auxiliary_rows)
        else:
            model_rows = [row for row in rows if _is_model_process(row, self.port)]
            related_rows = [row for row in rows if _is_related(row)]
            model_pids = {row["pid"] for row in model_rows}
            sentrix_rows = [row for row in related_rows if row["pid"] not in model_pids]
            auxiliary_rows = [row for row in sentrix_rows if _is_model_process(row, self.port)]
            product_rows = [
                row for row in sentrix_rows if row["pid"] not in {r["pid"] for r in auxiliary_rows}
            ]
        model_complete = bool(model_rows) and (
            service_resolved(rows, main_spec) if scope else len(model_rows) == 1
        )
        roles_complete = not scope or all(
            service_resolved(rows, spec)
            for spec in [dict(scope.get("sentrix") or {}), profile, *(scope.get("product") or [])]
            if isinstance(spec, dict)
        )
        groups = {"model": model_rows, "product": _dedupe_rows(sentrix_rows + model_rows)}
        eligible = {"model": model_complete, "product": bool(model_complete and roles_complete)}
        readers = {
            name: (lambda group=group: _resident_group(group))
            for name, group in groups.items()
            if eligible[name]
        }
        ready = self._memory_probe.collect(readers, budget=0.9)
        observations: dict[str, Any] = {
            "topology": {
                "source": "ps_lsof",
                "status": "available" if rows else "unavailable",
                "observed_at_monotonic": topology_started,
                "finished_at_monotonic": topology_finished,
                "ttl_seconds": MEMORY_OBSERVATION_TTL_SECONDS,
                "process_count": len(rows),
            }
        }
        data: dict[str, Any] = {
            "protocol_version": RESOURCE_TELEMETRY_VERSION,
            "memory_accounting": "apple_footprint_resident_v2",
            "memory_unit": "apple_resident_mib",
            "memory_profile": {"method": "apple_footprint_resident_v2"},
            "memory_observation_version": 1,
            "memory_observations": observations,
            "memory_dependencies": {
                "model_process_system_memory_used_mib": ["topology", "model"],
                "model_process_memory_used_mib": ["topology", "model"],
                "product_stack_memory_mib": ["topology", "product"],
                "benchmark_process_memory_used_mib": ["topology", "product"],
            },
        }
        for name, group in groups.items():
            observation = dict(ready.get(name, {}).get("value") or {})
            identities = observation.get("process_identities") or {}
            if not eligible[name]:
                observation.update(status="unavailable", reason="service_scope_unresolved")
            elif not observation:
                observation.update(status="unavailable", reason="footprint_pending_or_failed")
            elif observation.get("status") == "available" and (
                set(observation.get("pids") or []) != {row["pid"] for row in group}
                or any(_process_identity(int(pid)) != identity for pid, identity in identities.items())
            ):
                observation.update(status="unavailable", reason="group_membership_or_identity_changed")
            observations[name] = observation
            value = observation.get("resident_bytes") if observation.get("status") == "available" else None
            mib = round(value / 1048576, 3) if value is not None else None
            if name == "model":
                data["model_process_system_memory_used_mib"] = mib
                data["process_memory_used_mib"] = mib
                data["model_process_memory_used_mib"] = mib
                data["model_process_system_memory_scope"] = "apple_footprint_group_resident_v2"
                data["process_memory_scope"] = "apple_footprint_group_resident_v2"
                data["model_system_processes"] = [
                    {key: row.get(key) for key in ("pid", "process_name", "listening_ports")} for row in group
                ]
            else:
                data["product_stack_memory_mib"] = mib
                data["benchmark_process_memory_used_mib"] = mib
                data["benchmark_process_memory_scope"] = "apple_footprint_group_resident_v2"
                data["benchmark_processes"] = [
                    {key: row.get(key) for key in ("pid", "process_name", "listening_ports")} for row in group
                ]
        data["memory_complete"] = all(observations[name].get("status") == "available" for name in groups)
        observations["device_gpu"] = device.get("gpu_observation") or {}
        data["model_process_gpu_memory_mib"] = None
        data["product_stack_gpu_memory_mib"] = None
        data["device_gpu_memory_mib"] = device.get("device_gpu_memory_mib")
        data["resource_missing_reasons"] = {
            "model_process_gpu_memory_mib": "runtime_does_not_expose_process_gpu_bytes",
            "product_stack_gpu_memory_mib": "runtime_does_not_expose_process_gpu_bytes",
        }
        data["memory_dependencies"]["device_gpu_memory_mib"] = ["device_gpu"]

        if model_complete:
            data["root_pid"] = model_rows[0]["pid"]
        used = device.get("system_memory_used_mib")
        # 相对首个有效系统采样的变化，包含本机其他负载。
        if self._system_baseline_mib is None and isinstance(used, (int, float)):
            self._system_baseline_mib = float(used)
        if self._system_baseline_mib is not None and isinstance(used, (int, float)):
            data["system_memory_baseline_mib"] = round(self._system_baseline_mib, 2)
            data["system_memory_delta_mib"] = round(float(used) - self._system_baseline_mib, 2)
        return data

    def pending_work(self) -> list[str]:
        return self._memory_probe.pending_work()

    def gpu_stats(self) -> dict[str, Any]:
        device = self._device()
        gpu: dict[str, Any] = {"index": 0, "memory_unit": "apple_resident_mib"}
        for key in (
            "gpu_utilization_pct",
            "gpu_allocated_unified_memory_mib",
            "device_gpu_memory_mib",
            "gpu_in_use_unified_memory_mib",
            "temperature_c",
            "cpu_temperature_c",
            "power_draw_w",
            "power_scope",
            "cpu_power_w",
            "gpu_power_w",
            "device_observed_at_monotonic",
            "device_observation_age_seconds",
            "device_observation_ttl_seconds",
        ):
            if key in device:
                gpu[key] = device[key]
        gpu["memory_observations"] = {"device_gpu": device.get("gpu_observation") or {}}
        gpu["memory_dependencies"] = {"device_gpu_memory_mib": ["device_gpu"]}
        return {
            "status": "available",
            "source": self.source,
            "data": {
                "gpus": [gpu],
                "protocol_version": RESOURCE_TELEMETRY_VERSION,
                "memory_accounting": "apple_footprint_resident_v2",
            },
        }

    def process_memory(self) -> dict[str, Any]:
        data = self._augment()
        data.update({key: value for key, value in self._device().items() if key not in data})
        observed = [item.get("observed_at_monotonic") for item in data["memory_observations"].values()]
        data["pss_sampled_at_monotonic"] = min(
            (t for t in observed if isinstance(t, (int, float))), default=None
        )
        data["memory_observations"]["system"] = data.get("system_observation") or {}
        for key in ("system_memory_used_mib", "system_memory_total_mib", "system_memory_available_mib"):
            data["memory_dependencies"][key] = ["system"]
        data, _ = memory_freshness({**data, "_t": time.monotonic()})
        data.pop("_t", None)
        data["process_memory_used_mib"] = data.get("model_process_memory_used_mib")
        self._last_memory = {"status": "available", "source": self.source, "data": data}
        return self._last_memory

    def system_memory(self) -> dict[str, Any]:
        data = self._device()
        if "system_memory_used_mib" not in data:
            return {"status": "unavailable", "source": "vm_stat", "reason": "vm_stat_unavailable"}
        keys = (
            "system_memory_used_mib",
            "system_memory_total_mib",
            "system_memory_available_mib",
            "system_memory_scope",
            "system_observation",
        )
        result = {key: data[key] for key in keys if key in data}
        result.update(cpu_utilization_pct=self._cpu.sample(), cpu_observed_at_monotonic=time.monotonic())
        return {"status": "available", "source": "vm_stat", "data": result}

    def kv_cache(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "source": "apple_unified",
            "reason": "runtime_does_not_expose_kv_bytes",
        }

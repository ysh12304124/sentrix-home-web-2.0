"""Platform accounting rules and validity; no operating-system access."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

APPLE_POWER_LIMIT_W = 150.0
APPLE_TEMPERATURE_MIN_C = 10.0
APPLE_TEMPERATURE_MAX_C = 130.0
MEMORY_OBSERVATION_TTL_SECONDS = 3.0
RESOURCE_TELEMETRY_VERSION = "photobench-resource-telemetry-v1"
MEMORY_OBSERVATION_FIELDS = (
    "model_process_memory_used_mib",
    "model_process_host_memory_mib",
    "model_process_gpu_memory_mib",
    "product_stack_gpu_memory_mib",
    "benchmark_process_gpu_memory_mib",
    "device_gpu_memory_mib",
    "model_process_system_memory_used_mib",
    "sentrix_stack_pss_mib",
    "sentrix_product_pss_mib",
    "sentrix_auxiliary_model_pss_mib",
    "sentrix_auxiliary_model_uma_mib",
    "product_stack_memory_mib",
    "benchmark_process_memory_used_mib",
    "system_memory_used_mib",
    "system_memory_total_mib",
    "system_memory_available_mib",
)

DEVICE_FIELDS = (
    "gpu_utilization_pct",
    "memory_used_mib",
    "memory_total_mib",
    "temperature_c",
    "cpu_temperature_c",
    "power_draw_w",
    "cpu_power_w",
    "gpu_power_w",
    "sm_clock_mhz",
    "gpu_allocated_unified_memory_mib",
    "gpu_in_use_unified_memory_mib",
    "power_rails_w",
    "power_rails_average_w",
)


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def apple_power(value: object) -> float | None:
    number = finite_number(value)
    return number if number is not None and 0 <= number <= APPLE_POWER_LIMIT_W else None


def apple_temperature(value: object) -> float | None:
    number = finite_number(value)
    return (
        number if number is not None and APPLE_TEMPERATURE_MIN_C < number <= APPLE_TEMPERATURE_MAX_C else None
    )


def component_total(values: Sequence[float | None]) -> float | None:
    """Unknown necessary components invalidate a total; an explicitly empty set is zero."""
    if any(value is None or finite_number(value) is None or value < 0 for value in values):
        return None
    return round(sum(value for value in values if value is not None), 3)


def system_used(total: object, available: object) -> float | None:
    """Legacy working-use metric: capacity minus estimated available memory."""
    capacity, free = finite_number(total), finite_number(available)
    if capacity is None or free is None or not 0 <= free <= capacity:
        return None
    return round(capacity - free, 2)


def physical_memory_used(total: object, free: object) -> float | None:
    """Current non-free physical RAM, including resident reclaimable caches."""
    capacity, idle = finite_number(total), finite_number(free)
    if capacity is None or idle is None or capacity <= 0 or not 0 <= idle <= capacity:
        return None
    return round(capacity - idle, 2)


def apple_resident_bytes(components: dict[str, Any]) -> int | None:
    """footprint group totals, normalized to bytes; wired is a dirty subset."""
    values = {key: finite_number(components.get(key)) for key in ("dirty", "swapped", "clean", "reclaimable")}
    if any(value is None or value < 0 for value in values.values()):
        return None
    dirty, swapped, clean, reclaimable = (values[key] for key in ("dirty", "swapped", "clean", "reclaimable"))
    assert dirty is not None and swapped is not None and clean is not None and reclaimable is not None
    if swapped > dirty:
        return None
    return int(dirty - swapped + clean + reclaimable)


def device_freshness(sample: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Rebase source age at persistence/read time; retained timestamps are the facts."""
    result = dict(sample)
    captured = finite_number(sample.get("_t", sample.get("t")))
    own_gpu_temperature = "gpu_temperature_observed_at_monotonic" in sample
    gpu_observed = finite_number(sample.get("gpu_temperature_observed_at_monotonic"))
    rejected = []
    if own_gpu_temperature and (
        captured is None or gpu_observed is None or not 0 <= captured - gpu_observed <= 3
    ):
        result["temperature_c"] = None
        result["resource_missing_reasons"] = {
            **(sample.get("resource_missing_reasons") or {}),
            "temperature_c": "gpu_temperature_observation_expired",
        }
        rejected.append("temperature_c")
    observed = finite_number(sample.get("device_observed_at_monotonic"))
    if captured is None or observed is None:
        return result, rejected
    age = captured - observed
    # Old observations did not record source periods. This explicit compatibility
    # policy is versioned; it does not reconstruct an unknown refresh cadence.
    ttl = finite_number(sample.get("device_observation_ttl_seconds", 3.0))
    result.update(device_observation_age_seconds=age, device_freshness_version=1)
    if ttl is not None and ttl > 0 and 0 <= age <= ttl:
        return result, rejected
    rejected += [
        key for key in DEVICE_FIELDS if key in result and not (key == "temperature_c" and own_gpu_temperature)
    ]
    for key in rejected:
        result[key] = None
    status = dict(result.get("source_status") or {})
    status["device"] = "unavailable"
    result["source_status"] = status
    diagnostics = dict(result.get("source_diagnostics") or {})
    diagnostics["device"] = {
        **(diagnostics.get("device") or {}),
        "reason": "device_observation_expired_at_sample",
    }
    result["source_diagnostics"] = diagnostics
    return result, rejected


def memory_freshness(sample: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate recorded dependencies, without inventing observations for legacy data."""
    if type(sample.get("memory_observation_version")) is not int or sample["memory_observation_version"] != 1:
        return sample, []
    result = dict(sample)
    captured = finite_number(sample.get("_t", sample.get("t")))
    observations = sample.get("memory_observations") or {}
    dependencies = sample.get("memory_dependencies") or {}
    if not isinstance(observations, dict):
        observations = {}
    if not isinstance(dependencies, dict):
        dependencies = {}
    derived: dict[str, Any] = {}
    for key, raw in observations.items():
        row = dict(raw) if isinstance(raw, dict) else {}
        observed = finite_number(row.get("observed_at_monotonic"))
        finished = finite_number(row.get("finished_at_monotonic"))
        ttl = finite_number(row.get("ttl_seconds"))
        age = captured - observed if captured is not None and observed is not None else None
        fresh = (
            row.get("status") == "available"
            and age is not None
            and ttl is not None
            and 0 < ttl <= MEMORY_OBSERVATION_TTL_SECONDS
            and 0 <= age <= ttl
            and finished is not None
            and observed is not None
            and captured is not None
            and observed <= finished <= captured
        )
        row.update(age_seconds=age, fresh=fresh)
        derived[key] = row
    result["memory_observations"] = derived
    raw_validity = sample.get("component_validity")
    validity = dict(raw_validity) if isinstance(raw_validity, dict) else {}
    rejected: list[str] = []
    process_times: list[float] = []
    for key in MEMORY_OBSERVATION_FIELDS:
        if key not in sample and key not in dependencies:
            continue
        refs = dependencies.get(key)
        value = finite_number(sample.get(key))
        valid = (
            isinstance(refs, list)
            and value is not None
            and value >= 0
            and bool(refs)
            and all(isinstance(ref, str) and derived.get(ref, {}).get("fresh") is True for ref in refs)
        )
        if not valid:
            result[key] = None
            validity[key] = "unavailable"
            rejected.append(key)
        else:
            validity[key] = "valid"
            if not key.startswith("system_") and isinstance(refs, list):
                process_times.extend(float(derived[ref]["observed_at_monotonic"]) for ref in refs)
    result["component_validity"] = validity
    result["memory_complete"] = all(
        finite_number(result.get(key)) is not None
        for key in ("model_process_system_memory_used_mib", "product_stack_memory_mib")
    )
    oldest = min(process_times) if process_times else None
    result["process_observed_at_monotonic"] = oldest if result["memory_complete"] else None
    result["pss_sampled_at_monotonic"] = result["process_observed_at_monotonic"]
    if rejected:
        raw_diagnostics = result.get("source_diagnostics")
        diagnostics = dict(raw_diagnostics) if isinstance(raw_diagnostics, dict) else {}
        diagnostics["memory"] = {"reason": "memory_dependency_unavailable_or_expired", "fields": rejected}
        result["source_diagnostics"] = diagnostics
    return result, rejected


# One normalized record per scope and resource; values use bytes at the export boundary.
RESOURCE_FIELDS = (
    (
        "model_process_memory",
        "model_process_system_memory_used_mib",
        "process_memory_bytes",
        "main_model",
        "bytes",
    ),
    ("product_process_memory", "product_stack_memory_mib", "process_memory_bytes", "product", "bytes"),
    ("device_memory", "system_memory_used_mib", "device_memory_nonfree_bytes", "device", "bytes"),
    ("model_gpu_memory", "model_process_gpu_memory_mib", "gpu_memory_bytes", "main_model", "bytes"),
    ("product_gpu_memory", "product_stack_gpu_memory_mib", "gpu_memory_bytes", "product", "bytes"),
    ("device_gpu_memory", "device_gpu_memory_mib", "gpu_memory_bytes", "device", "bytes"),
    ("cpu_utilization", "cpu_utilization_pct", "cpu_utilization_percent", "device", "%"),
    ("gpu_utilization", "gpu_utilization_pct", "gpu_utilization_percent", "device", "%"),
    ("cpu_temperature", "cpu_temperature_c", "cpu_temperature_celsius", "device", "celsius"),
    ("gpu_temperature", "temperature_c", "gpu_temperature_celsius", "device", "celsius"),
    ("power", "power_draw_w", "power_watts", "device", "W"),
)


def resource_records(sample: dict[str, Any]) -> dict[str, Any]:
    """Export only explicitly versioned observations; legacy values retain their definition."""
    if sample.get("protocol_version") != RESOURCE_TELEMETRY_VERSION:
        return {}
    accounting = str(sample.get("memory_accounting") or "")
    apple = accounting.startswith("apple_")
    jetson = accounting == "jetson_pss_and_nvmap_v3"
    process_method = "apple_footprint_group_resident_v2" if apple else "proc_group_pss"
    gpu_method = "not_exposed_by_runtime" if apple else "nvmap_pid_allocations" if jetson else "nvml_pid_vram"
    captured = finite_number(sample.get("_t", sample.get("t")))
    epoch = finite_number(sample.get("observed_at_epoch"))
    observations = sample.get("memory_observations") or {}
    dependencies = sample.get("memory_dependencies") or {}
    reasons = sample.get("resource_missing_reasons") or {}
    output: dict[str, Any] = {}
    for name, key, metric, scope, unit in RESOURCE_FIELDS:
        value = finite_number(sample.get(key))
        method = process_method
        if name == "model_process_memory" and accounting == "apple_phys_footprint_v1":
            method = "proc_pid_rusage_phys_footprint"
        if name == "device_memory":
            method = str(sample.get("system_memory_scope") or "physical_nonfree")
        elif "gpu_memory" in name:
            method = gpu_method
            if scope == "device":
                method = (
                    "ioreg_alloc_system_memory"
                    if apple
                    else "nvmap_reported_total"
                    if jetson
                    else "nvml_device_used"
                )
        elif name == "cpu_utilization":
            method = "psutil_cpu_percent"
        elif name in {"gpu_utilization", "cpu_temperature", "gpu_temperature"}:
            method = (
                "macmon"
                if apple
                else "tegrastats"
                if jetson
                else "nvml"
                if name != "cpu_temperature"
                else "hwmon_cpu"
            )
        elif name == "power":
            method = str(sample.get("power_scope") or "scope_unrecorded")
        if name == "gpu_temperature" and sample.get("gpu_temperature_method"):
            method = str(sample["gpu_temperature_method"])
        refs = dependencies.get(key) or []
        times = [finite_number((observations.get(ref) or {}).get("observed_at_monotonic")) for ref in refs]
        valid_times = [t for t in times if t is not None]
        observed = (
            min(valid_times)
            if valid_times
            else finite_number(
                sample.get(
                    "cpu_observed_at_monotonic"
                    if name == "cpu_utilization"
                    else "device_observed_at_monotonic"
                    if scope == "device"
                    else "process_observed_at_monotonic"
                )
            )
        )
        if name == "gpu_temperature" and sample.get("gpu_temperature_method"):
            observed = finite_number(sample.get("gpu_temperature_observed_at_monotonic"))
        status = "available" if value is not None and value >= 0 else "unavailable"
        reason = None if status == "available" else reasons.get(key, "source_not_available")
        output[name] = {
            "metric_id": metric,
            "scope": scope,
            "unit": unit,
            "value": int(round(value * 1048576)) if value is not None and unit == "bytes" else value,
            "status": status,
            "reason": reason,
            "method": method,
            "source": str(sample.get("source") or ""),
            "observed_at_monotonic": observed,
            "observed_at_epoch": epoch - (captured - observed)
            if epoch is not None and captured is not None and observed is not None
            else None,
        }
    return output


def cpu_freshness(sample: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if sample.get("protocol_version") != RESOURCE_TELEMETRY_VERSION:
        return sample, []
    result = dict(sample)
    captured = finite_number(sample.get("_t", sample.get("t")))
    observed = finite_number(sample.get("cpu_observed_at_monotonic"))
    value = finite_number(sample.get("cpu_utilization_pct"))
    if (
        captured is not None
        and observed is not None
        and 0 <= captured - observed <= 3
        and value is not None
        and 0 <= value <= 100
    ):
        return result, []
    result["cpu_utilization_pct"] = None
    return result, ["cpu_utilization_pct"]

"""Whole-run counters and optional disk previews with bounded resident state."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from photobench.domain.runs.analysis import (
    GPU_AGGREGATE_KEYS,
    _is_telemetry_series_key,
    aggregate_gpu_samples,
    validated_telemetry_sample,
)
from photobench.domain.runtime.metrics import RESOURCE_TELEMETRY_VERSION, finite_number


@dataclass
class _Counter:
    count: int = 0
    total: float = 0.0
    peak: float | None = None

    def add(self, value: float | None) -> None:
        if value is not None:
            self.count += 1
            self.total += value
            self.peak = value if self.peak is None else max(self.peak, value)

    def snapshot(self) -> dict[str, Any]:
        if not self.count:
            return {}
        return {
            "peak": self.peak,
            "mean": round(self.total / self.count, 1),
            "valid_count": self.count,
            "mean_kind": "sample_mean",
            "peak_kind": "sampled_peak",
            "quantiles_status": "not_recorded_streaming",
        }


class TelemetrySummary:
    """Exact peaks, means and covered-interval integrals without a distribution.

    Keep two timestamp groups and the latest observation. As in
    resource_series_stats, the last GPU row at a shared timestamp supplies
    time-weighted values; sample statistics include every GPU row.
    """

    def __init__(self) -> None:
        self.count = 0
        self.latest: dict[str, Any] = {}
        self._accounting_sample: dict[str, Any] | None = None
        self.counters: dict[str, _Counter] = {}
        self.intervals = _Counter()
        self.phases: dict[str, dict[str, int]] = {}
        self._first_t: float | None = None
        self._previous: dict[str, Any] | None = None
        self._current: dict[str, Any] | None = None
        self._integrals: dict[str, float] = {}
        self._covered: dict[str, float] = {}
        self._gaps = self._out_of_order = self._pss_count = self._over_limit = 0
        self._pss_time: float | None = None
        self._idle_memory: float | None = None
        self._limit: float | None = None
        self._limit_observed = False

    def add(self, sample: dict[str, Any]) -> None:
        # The sampler validates each observation before calling add().
        self.count += 1
        self.latest = sample
        if sample.get("memory_accounting"):
            self._accounting_sample = sample
        keys = set(GPU_AGGREGATE_KEYS) | {key for key in sample if key.startswith(("runtime_", "kv_cache_"))}
        for key in keys:
            self.counters.setdefault(key, _Counter()).add(finite_number(sample.get(key)))
        phase = str(sample.get("phase") or "unassigned")
        row = self.phases.setdefault(phase, {"samples": 0, "core_memory_valid": 0})
        row["samples"] += 1
        row["core_memory_valid"] += int(
            all(
                (value := finite_number(sample.get(key))) is not None and value >= 0
                for key in (
                    "system_memory_used_mib",
                    "model_process_system_memory_used_mib",
                    "product_stack_memory_mib",
                )
            )
        )
        pss_time = finite_number(sample.get("pss_sampled_at_monotonic"))
        if pss_time is not None and pss_time != self._pss_time:
            self._pss_count += 1
            self._pss_time = pss_time
        memory = finite_number(sample.get("model_process_memory_used_mib"))
        if sample.get("kv_cache_usage_pct") == 0 and memory is not None:
            self._idle_memory = memory if self._idle_memory is None else min(self._idle_memory, memory)
        limit = finite_number(sample.get("model_process_memory_limit_mib"))
        if limit is not None:
            self._limit = limit
        if isinstance(sample.get("model_process_memory_over_limit"), bool):
            self._limit_observed = True
            self._over_limit += int(sample["model_process_memory_over_limit"])
        timestamp = finite_number(sample.get("t", sample.get("_t")))
        if timestamp is None:
            return
        point: dict[str, Any] = {key: finite_number(sample.get(key)) for key in keys}
        point["t"] = timestamp
        point["scope"] = tuple(
            sample.get(key)
            for key in (
                "phase",
                "index",
                "protocol_version",
                "memory_accounting",
                "power_scope",
            )
        )
        if self._current is None:
            self._first_t = timestamp
        elif timestamp > self._current["t"]:
            for key in self.counters:
                integral, covered = self._interval(key)
                self._integrals[key] = self._integrals.get(key, 0.0) + integral
                self._covered[key] = self._covered.get(key, 0.0) + covered
            interval = timestamp - self._current["t"]
            self.intervals.add(interval)
            self._gaps += int(interval > 3)
            self._previous = self._current
        elif timestamp < self._current["t"]:
            self._out_of_order += 1
            return
        self._current = point

    def _interval(self, key: str) -> tuple[float, float]:
        left, right = self._previous, self._current
        if left is None or right is None or left["scope"] != right["scope"]:
            return 0.0, 0.0
        duration = right["t"] - left["t"]
        a, b = left.get(key), right.get(key)
        if a is None or b is None or not 0 < duration <= 3:
            return 0.0, 0.0
        return (a + b) * 0.5 * duration, duration

    def snapshot(self) -> dict[str, Any]:
        context = [self.latest] if self.count else []
        if self._accounting_sample is not None and self._accounting_sample is not self.latest:
            context.insert(0, self._accounting_sample)
        result = aggregate_gpu_samples(context)
        elapsed = self._current["t"] - self._first_t if self._current is not None else None
        for key, counter in self.counters.items():
            stats = counter.snapshot()
            if result.get("protocol_version") == RESOURCE_TELEMETRY_VERSION:
                pending_integral, pending_covered = self._interval(key)
                integral = self._integrals.get(key, 0.0) + pending_integral
                covered = self._covered.get(key, 0.0) + pending_covered
                stats.update(
                    mean=integral / covered if covered else None,
                    mean_kind="time_weighted_trapezoid",
                    covered_seconds=covered,
                    coverage_ratio=covered / elapsed if elapsed else None,
                )
                if key == "power_draw_w":
                    stats.update(
                        energy_joules=integral if covered else None,
                        energy_wh=integral / 3600 if covered else None,
                        energy_scope="covered_intervals",
                    )
            result[key] = stats
        result.update(
            samples_count=self.count,
            statistics_mode="streaming_v1",
            statistics_scope="whole_run",
            quantiles_status="not_recorded_streaming",
            sampling={
                "elapsed_seconds": elapsed,
                "interval_seconds": self.intervals.snapshot(),
                "gaps_over_3_seconds": self._gaps,
                "out_of_order_samples": self._out_of_order,
                "phases": {key: dict(value) for key, value in self.phases.items()},
            },
        )
        if "pss_samples_count" in result:
            result["pss_samples_count"] = self._pss_count
        if "model_process_memory_limit_mib" in result:
            result["model_process_memory_limit_mib"] = self._limit
            result["model_process_over_limit_samples"] = self._over_limit if self._limit_observed else None
        profile = result.get("memory_profile") or {}
        for metric, target in (
            ("kv_cache_used_tokens", "kv_cache_used_peak_tokens"),
            ("kv_cache_usage_pct", "kv_cache_usage_peak_pct"),
        ):
            if target in profile:
                profile[target] = (result.get(metric) or {}).get("peak")
        if profile.get("method") == "idle_process_minus_reserved_kv_plus_peak_used_kv_v1":
            capacity = finite_number(profile.get("kv_cache_capacity_gib"))
            idle = self._idle_memory / 1024 if self._idle_memory is not None else None
            base = max(0.0, idle - capacity) if idle is not None and capacity is not None else None
            usage_peak = (result.get("kv_cache_usage_pct") or {}).get("peak")
            kv_peak = capacity * usage_peak / 100 if capacity is not None and usage_peak is not None else None
            profile.update(
                idle_process_memory_gib=idle,
                fixed_base_memory_gib=base,
                kv_cache_used_peak_gib=kv_peak,
                comparable_workload_memory_gib=base + kv_peak
                if base is not None and kv_peak is not None
                else None,
                idle_baseline_status="observed" if idle is not None else "not_observed",
            )
        return result


def attach_recorded_history(live: dict[str, Any], lines: Iterable[str], limit: int = 240) -> None:
    """Decimate a disk stream; saved whole-run metrics remain authoritative."""
    points: list[dict[str, Any]] = []
    stride, count = 1, 0
    latest: dict[str, Any] | None = None
    previous_t: float | None = None
    previous_validity: tuple[bool, ...] | None = None
    pending_gap = False
    try:
        for line in lines:
            try:
                raw = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            item, _ = validated_telemetry_sample(raw)
            timestamp = finite_number(item.get("_t"))
            validity = tuple(finite_number(item.get(key)) is not None for key in GPU_AGGREGATE_KEYS)
            pending_gap |= (
                timestamp is not None and previous_t is not None and timestamp - previous_t > 3
            ) or (previous_validity is not None and validity != previous_validity)
            previous_t, previous_validity = timestamp, validity
            latest = {
                "t": item.get("_t"),
                "phase": item.get("phase", "unassigned"),
                "phase_label": item.get("phase_label", "未分配阶段"),
                "gap_before": pending_gap,
                **{key: value for key, value in item.items() if _is_telemetry_series_key(key)},
            }
            if count % stride == 0:
                points.append(latest)
                pending_gap = False
                if len(points) >= limit:
                    # A discarded point may contain a real gap or missing-value
                    # transition. Keep that break on the next retained segment.
                    for index in range(2, len(points), 2):
                        points[index]["gap_before"] |= points[index - 1]["gap_before"]
                    if len(points) % 2 == 0:
                        pending_gap |= points[-1]["gap_before"]
                    points = points[::2]
                    stride *= 2
            count += 1
    except (OSError, UnicodeError) as error:
        live["history_read_error"] = f"{type(error).__name__}: {error}"
    if latest is not None and (not points or points[-1] is not latest):
        points.append(latest)
    live["history"] = points
    live["history_resolution"] = "uniform_stride"
    live["history_samples_read"] = count

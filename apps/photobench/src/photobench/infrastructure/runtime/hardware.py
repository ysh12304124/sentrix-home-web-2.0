"""Host CPU sampling and temperature reads for local telemetry providers.

CPU utilization is the all-core ratio reported by psutil. Temperature reads
select CPU sensors from hwmon; GPU and storage sensors remain separate.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import psutil

# 153 是 AMD k10temp 的 Tctl，Intel 板子是 coretemp，Orin 可能暴露 cpu-thermal。
# NVMe / 网卡 / amdgpu 的传感器一律忽略——把它们当成 CPU 温度比读不到更糟。
_CPU_HWMON_NAMES = ("k10temp", "coretemp", "cpu_thermal", "cpu-thermal")
# 同一颗芯片可能有多个 temp*_input（Tctl / Tccd / 各核心），按标签挑一个。
_CPU_TEMP_LABELS = ("tctl", "tdie", "package id 0", "cpu")


class HostCpuSampler:
    """All-core CPU busy ratio; the first observation only initializes the interval."""

    def __init__(self) -> None:
        self._primed = False

    def sample(self) -> float | None:
        try:
            value = float(psutil.cpu_percent(interval=None))
        except (OSError, RuntimeError):
            return None
        if not self._primed:
            self._primed = True
            return None
        return value if 0 <= value <= 100 else None


def read_cpu_temperature_c(root: Path | None = None) -> float | None:
    """CPU 封装温度（摄氏度）；传感器不存在时返回 ``None``。

    ``root`` 只为测试可注入（真调用方不传，走 ``/sys/class/hwmon``）。
    """
    base = root or Path("/sys/class/hwmon")
    if not base.is_dir():
        return None
    for hw in sorted(base.iterdir()):
        try:
            name = (hw / "name").read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if name not in _CPU_HWMON_NAMES:
            continue
        chosen = None
        chosen_rank = 99
        for sensor in sorted(hw.glob("temp*_input")):
            label = ""
            label_path = hw / sensor.name.replace("_input", "_label")
            with contextlib.suppress(OSError):
                label = label_path.read_text(encoding="ascii").strip().lower()
            try:
                celsius = round(float(sensor.read_text(encoding="ascii").strip()) / 1000.0, 3)
            except (OSError, ValueError):
                continue
            rank = _CPU_TEMP_LABELS.index(label) if label in _CPU_TEMP_LABELS else 50
            if chosen is None or rank < chosen_rank:
                chosen = celsius
                chosen_rank = rank
        if chosen is not None:
            return chosen
    return None

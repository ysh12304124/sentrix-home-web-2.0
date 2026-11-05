"""``photobench.infrastructure.runtime.apple_telemetry`` 的行为对照。

同样全走假件：``_run_text``（真正起 ioreg / vm_stat / ps / lsof / sysctl 的地方）
被换成一张按命令派发的表，footprint 的 ctypes 调用被换成查表——**用例在 Linux 上
也照样跑**，结果不取决于跑它的机器是不是 Mac。

口径指回 1.0 ``backend/apple_telemetry.py``（基线 2d2cee7c）。
"""

from __future__ import annotations

import pytest

from photobench.infrastructure.runtime import apple_telemetry as apple
from photobench.infrastructure.runtime import providers

MIB = 1048576

VM_STAT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10.
Pages active:                             100.
Pages inactive:                           50.
Pages speculative:                        5.
Pages wired down:                         40.
Pages occupied by compressor:             10.
"""

AGX = (
    '"PerformanceStatistics" = {"Alloc system memory"=1048576,'
    '"Device Utilization %"=25,"In use system memory"=524288}'
)

LSOF = """\
p44429
f13
n*:8091
p21110
f8
n127.0.0.1:8101
p47236
f9
n*:8100
p12051
f10
n*:8771
p999
f3
n*:9999
"""

MACMON_SAMPLE = {
    "gpu_utilization_pct": 12.5,
    "power_draw_w": 4.2,
    "power_scope": "macmon_sys_power",
    "cpu_temperature_c": 41.2,
}


class _FakeMacmon:
    def __init__(self, sample: dict[str, object]) -> None:
        self.sample = sample

    def latest(self) -> dict[str, object]:
        return dict(self.sample)


def make_dispatcher(*, ps: str = "", lsof: str = "", vm_stat: str = VM_STAT):
    """按命令头派发假输出。**没预设的命令直接抛**，避免漏网去起真子进程。"""

    def dispatch(command: list[str], timeout: float = 3) -> str:
        head = command[0]
        if head == "ioreg":
            return AGX
        if head == "vm_stat":
            return vm_stat
        if head == "sysctl":
            return "16384\n" if "hw.pagesize" in command else f"{256 * MIB}\n"
        if head == "ps":
            return ps
        if head == "lsof":
            return lsof
        raise AssertionError(f"用例没有预设这条命令：{command}")

    return dispatch


def test_ps_observer_excludes_only_its_own_pid(monkeypatch):
    class Observer:
        pid = 300

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, timeout=None):
            assert timeout == 0.6
            return (
                " 100 1 python photobench\n"
                " 200 100 ps real-product-worker\n"
                " 300 100 ps -ax -o pid= -o ppid= -o command=\n",
                "",
            )

    monkeypatch.setattr(apple.subprocess, "Popen", lambda *args, **kwargs: Observer())
    rows = apple._process_rows()
    assert [row["pid"] for row in rows] == [100, 200]
    # Preserve a real product child even when it happens to be named ps.
    assert rows[1]["ppid"] == 100


# ---- 解析层（纯函数）--------------------------------------------------------------


def test_vm_stat_includes_resident_caches_and_excludes_only_free() -> None:
    data = apple.parse_vm_stat(VM_STAT, page_size=16384, total_bytes=200 * 16384)
    assert data is not None
    assert data["system_memory_scope"] == "apple_physical_nonfree_including_cache_v2"
    assert data["system_memory_used_mib"] == round(190 * 16384 / MIB, 2)
    assert data["system_memory_available_mib"] == round(10 * 16384 / MIB, 2)
    assert data["system_memory_total_mib"] == round(200 * 16384 / MIB, 2)


def test_vm_stat_missing_counter_is_none_not_zero() -> None:
    assert apple.parse_vm_stat("Pages free: 1.\n", page_size=16384) is None
    assert apple.parse_vm_stat(VM_STAT, page_size=0) is None


def test_agx_counters_and_absent_counters() -> None:
    data = apple.parse_agx_performance(AGX)
    assert data["gpu_utilization_pct"] == 25.0
    assert data["gpu_allocated_unified_memory_mib"] == 1.0
    assert data["gpu_in_use_unified_memory_mib"] == 0.5
    assert apple.parse_agx_performance("no counters") == {}


def test_macmon_sample_maps_temperature_power_and_ratio() -> None:
    data = apple.parse_macmon_sample(
        {
            "sys_power": 4.2,
            "cpu_power": 1.5,
            "gpu_power": 0.8,
            "gpu_active_ratio": 0.125,
            "temp": {"cpu_temp_avg": 41.2, "gpu_temp_avg": 38.5},
        }
    )
    assert data["cpu_temperature_c"] == 41.2
    assert data["temperature_c"] == 38.5
    assert data["power_draw_w"] == 4.2
    assert data["power_scope"] == "macmon_sys_power"
    assert data["cpu_power_w"] == 1.5
    assert data["gpu_power_w"] == 0.8
    assert data["gpu_utilization_pct"] == 12.5


def test_macmon_sample_drops_the_whole_power_set_on_one_impossible_value() -> None:
    """325 W 是 macOS 27 上 macmon 0.8.2 的积压 flush；同组功率必须整组丢。

    只丢 sys_power 会把 413 W 的 ram_power 留下，而 DRAM 计数器正是判断
    「这个间隔的能量记账坏了」的金丝雀。
    """
    data = apple.parse_macmon_sample(
        {
            "sys_power": 325.303,
            "cpu_power": 12.0,
            "gpu_power": 3.0,
            "ram_power": 413.0,
            "temp": {"cpu_temp_avg": 41.2, "gpu_temp_avg": 38.5},
        }
    )
    assert "power_draw_w" not in data
    assert "power_scope" not in data
    assert "cpu_power_w" not in data
    assert "gpu_power_w" not in data
    assert data["cpu_temperature_c"] == 41.2
    assert data["temperature_c"] == 38.5


def test_macmon_temperature_rejects_the_0_8_c_sensor_glitch() -> None:
    data = apple.parse_macmon_sample({"temp": {"cpu_temp_avg": 0.8000000715255737, "gpu_temp_avg": 41.0}})
    assert "cpu_temperature_c" not in data
    assert data["temperature_c"] == 41.0
    assert apple.parse_macmon_sample({"temp": {"cpu_temp_avg": -1}}) == {}


def test_macmon_version_gate_and_power_drop() -> None:
    assert apple.parse_macmon_version("macmon 0.8.2") == (0, 8, 2)
    assert apple.parse_macmon_version("macmon 0.9.0") == (0, 9, 0)
    assert apple.parse_macmon_version("junk") is None
    assert apple.macmon_power_is_trusted(None) is True
    assert apple.macmon_power_is_trusted((0, 8, 2)) is False
    assert apple.macmon_power_is_trusted((0, 9, 0)) is True
    dropped = apple._drop_power_readings({"power_draw_w": 1.0, "temperature_c": 40.0})
    assert dropped == {"temperature_c": 40.0}


def test_macmon_sample_ignores_non_objects() -> None:
    assert apple.parse_macmon_sample([]) == {}
    assert apple.parse_macmon_sample("junk") == {}


def test_listening_ports_parses_lsof_machine_readable_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str], timeout: float = 3) -> str:
        seen.append(command)
        return LSOF

    monkeypatch.setattr(apple, "_run_text", fake_run)
    found = apple._listening_ports({"8091", "8101", "8100", "8771"})
    assert found == {
        44429: {"8091"},
        21110: {"8101"},
        47236: {"8100"},
        12051: {"8771"},
    }
    # 9999 不在目标端口里，那个 pid 一条都不该出现。
    assert 999 not in found
    assert "-Fpn" in seen[0]


def test_is_apple_silicon_requires_darwin_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(apple.platform, "machine", lambda: "arm64")
    assert apple.is_apple_silicon() is True
    monkeypatch.setattr(apple.platform, "machine", lambda: "x86_64")
    assert apple.is_apple_silicon() is False
    monkeypatch.setattr(apple.platform, "system", lambda: "Linux")
    monkeypatch.setattr(apple.platform, "machine", lambda: "aarch64")
    assert apple.is_apple_silicon() is False


def test_process_identity_rules() -> None:
    assert apple._is_related({"listening_ports": ["6333"], "identity": "unrelated"}) is True
    assert apple._is_related({"listening_ports": [], "identity": "python mlx_lm.server"}) is True
    assert apple._is_related({"listening_ports": [], "identity": "random"}) is False

    model_by_port = {"identity": "llama-server --port 8100", "listening_ports": ["8100"]}
    assert apple._is_model_process(model_by_port, "8100") is True
    assert apple._is_model_process(model_by_port, "8101") is False
    assert apple._is_model_process(model_by_port, "") is False
    assert (
        apple._is_model_process({"identity": "python mlx_lm.server", "listening_ports": ["8100"]}, "8100")
        is True
    )
    # 只看命令行那一路**仍然要求是 llama-server**：端口号会出现在别人的参数里。
    assert (
        apple._is_model_process({"identity": "llama-server --port 8100 extra", "listening_ports": []}, "8100")
        is True
    )
    assert (
        apple._is_model_process({"identity": "my-server --port 8100 extra", "listening_ports": []}, "8100")
        is False
    )
    # 端口在听，但名字不在白名单里（既不叫 llama-server / mlx 也不叫 vllm）——不算。
    assert (
        apple._is_model_process({"identity": "python worker.py", "listening_ports": ["8100"]}, "8100")
        is False
    )


def test_apple_keyword_set_stays_the_1_0_one() -> None:
    """Apple 那份常量**刻意不与 providers 共用**：它多一个 "mlx"，且不读环境变量。

    合并两者会悄悄改变 Apple 主机上的进程归属结果（"mlx" 是 Apple 上真实的模型
    进程名）。这条用例把那个差别钉住。
    """
    assert "mlx" in apple.SYSTEM_PROCESS_KEYWORDS
    assert "mlx" not in providers.SYSTEM_PROCESS_KEYWORDS
    # 端口默认值在 S9b-1 跟着 2.0 的端口表改了（8771 → 8772、8091 → 8191）；
    # 这一条改的是数字，不是「不与 providers 共用」那条差别（差别在关键词上）。
    assert {"8772", "8191", "8500", "8501", "8100", "8101", "6333"} == apple.SYSTEM_PROCESS_PORTS


# ---- 提供者 -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_telemetry_services_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """这些用例验的是主机口径；不让设备上的 telemetry_services.json 改变预期。"""
    monkeypatch.setattr(apple, "_load_telemetry_services", lambda: None)


def resident_observation(rows, mib=128):
    import time

    pids = {row["pid"] for row in rows}
    return {
        "status": "available",
        "source": "footprint_group",
        "pids": sorted(pids),
        "process_identities": {pid: pid * 100 for pid in pids},
        "observed_at_monotonic": time.monotonic(),
        "finished_at_monotonic": time.monotonic(),
        "ttl_seconds": 3,
        "resident_bytes": mib * MIB,
        "components_bytes": {"dirty": mib * MIB, "swapped": 0, "clean": 0, "reclaimable": 0},
    }


@pytest.fixture
def apple_host(monkeypatch):
    monkeypatch.setattr(
        apple,
        "_run_text",
        make_dispatcher(
            ps=" 100 1 python benchmark_orchestrator.py\n 200 1 llama-server --port 8100\n",
            lsof="p100\nn*:8771\np200\nn*:8100\n",
        ),
    )
    monkeypatch.setattr(apple, "_MACMON", _FakeMacmon(MACMON_SAMPLE))
    monkeypatch.setattr(apple, "_process_identity", lambda pid: pid * 100)
    # Group product result is deliberately NOT the sum of independent PIDs.
    monkeypatch.setattr(
        apple,
        "_resident_group",
        lambda rows: resident_observation(rows, 640 if len(rows) == 1 and rows[0]["pid"] == 200 else 700),
    )


def footprint_payload(pids=(42,), scale=1):
    return {
        "unit": "byte" if scale == 1 else "page",
        "bytes per unit": scale,
        "page size": scale,
        "processes": [{"pid": pid} for pid in pids],
        "errors": [],
        "summary": {"total": {"dirty": 100, "swapped": 60, "clean": 20, "reclaimable": 10, "wired": 5}},
    }


def test_footprint_normalizes_and_uses_only_summary():
    from photobench.domain.runtime.metrics import apple_resident_bytes

    data = footprint_payload(scale=16384)
    data["processes"][0]["dirty"] = 999999999
    result = apple.parse_footprint(data, {42})
    assert apple_resident_bytes(result) == 70 * 16384
    assert "wired" not in result


@pytest.mark.parametrize(
    "change",
    [
        {"errors": ["permission denied"]},
        {"processes": []},
        {"processes": [{"pid": 42}, {"pid": 42}]},
        {"bytes per unit": 4096},
        {"summary": {"total": {"dirty": 1, "swapped": 2, "clean": 0, "reclaimable": 0}}},
        {"summary": {"total": {"dirty": 1, "clean": 0, "reclaimable": 0}}},
    ],
)
def test_footprint_incomplete_or_invalid_never_becomes_zero(change):
    data = {**footprint_payload(), **change}
    assert apple.parse_footprint(data, {42}) is None


def test_group_detects_identity_change_and_timeout(monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    calls = iter([100, 101])
    monkeypatch.setattr(apple, "_process_identity", lambda pid: next(calls))

    def run(command, **kwargs):
        Path(command[command.index("-j") + 1]).write_text(json.dumps(footprint_payload()))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(apple.subprocess, "run", run)
    assert apple._resident_group([{"pid": 42}])["reason"] == "process_identity_changed"
    monkeypatch.setattr(apple, "_process_identity", lambda pid: 100)

    def timeout(*args, **kwargs):
        raise apple.subprocess.TimeoutExpired("footprint", 0.8)

    monkeypatch.setattr(apple.subprocess, "run", timeout)
    assert apple._resident_group([{"pid": 42}])["status"] == "unavailable"


def test_process_groups_are_independent_not_added(apple_host):
    data = apple.LocalAppleUnifiedTelemetryProvider("http://127.0.0.1:8100").process_memory()["data"]
    assert data["model_process_system_memory_used_mib"] == 640
    assert data["product_stack_memory_mib"] == 700
    assert data["process_memory_used_mib"] == 640
    assert data["memory_accounting"] == "apple_footprint_resident_v2"
    assert "sentrix_stack_pss_mib" not in data
    assert "model_process_mapped_weights_mib" not in data
    assert data["memory_complete"] is True
    assert data["system_memory_delta_mib"] == 0
    assert data["model_process_gpu_memory_mib"] is None
    assert data["product_stack_gpu_memory_mib"] is None
    assert data["device_gpu_memory_mib"] is not None
    assert (
        data["resource_missing_reasons"]["model_process_gpu_memory_mib"]
        == "runtime_does_not_expose_process_gpu_bytes"
    )


def test_missing_product_group_does_not_hide_main(apple_host, monkeypatch):
    def group(rows):
        if len(rows) > 1:
            return {"status": "unavailable", "reason": "permission_denied"}
        return resident_observation(rows, 512)

    monkeypatch.setattr(apple, "_resident_group", group)
    data = apple.LocalAppleUnifiedTelemetryProvider("http://127.0.0.1:8100").process_memory()["data"]
    assert data["model_process_system_memory_used_mib"] == 512
    assert data["product_stack_memory_mib"] is None
    assert data["memory_complete"] is False


def test_expired_observation_keeps_timestamp_and_clears_totals(apple_host, monkeypatch):
    def group(rows):
        result = resident_observation(rows, 500)
        result["observed_at_monotonic"] -= 4
        return result

    monkeypatch.setattr(apple, "_resident_group", group)
    data = apple.LocalAppleUnifiedTelemetryProvider("http://127.0.0.1:8100").process_memory()["data"]
    assert data["product_stack_memory_mib"] is None
    assert data["process_memory_used_mib"] is None
    assert not data["memory_complete"]
    assert data["memory_observations"]["model"]["age_seconds"] >= 4


def test_ambiguous_or_absent_model_has_no_product(apple_host, monkeypatch):
    monkeypatch.setattr(apple, "_process_rows", lambda: [])
    data = apple.LocalAppleUnifiedTelemetryProvider("http://127.0.0.1:8100").process_memory()["data"]
    assert data["product_stack_memory_mib"] is None
    assert data["model_process_system_memory_used_mib"] is None


def test_system_unavailable_with_missing_free_counter(monkeypatch):
    monkeypatch.setattr(apple, "_run_text", make_dispatcher(vm_stat="Pages active: 20."))
    monkeypatch.setattr(apple, "_MACMON", _FakeMacmon({}))
    assert apple.LocalAppleUnifiedTelemetryProvider().system_memory()["status"] == "unavailable"


def test_gpu_stats_retains_device_diagnostics(apple_host):
    data = apple.LocalAppleUnifiedTelemetryProvider().gpu_stats()["data"]["gpus"][0]
    assert data["memory_unit"] == "apple_resident_mib"
    assert data["gpu_in_use_unified_memory_mib"] == 0.5
    assert data["device_gpu_memory_mib"] is not None
    assert data["memory_dependencies"]["device_gpu_memory_mib"] == ["device_gpu"]
    assert data["memory_observations"]["device_gpu"]["source"] == "ioreg_alloc_system_memory"


def test_kv_cache_bytes_are_unavailable_without_runtime_reporting():
    assert apple.LocalAppleUnifiedTelemetryProvider().kv_cache()["status"] == "unavailable"

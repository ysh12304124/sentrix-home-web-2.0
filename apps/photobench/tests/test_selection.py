"""``select_runtime_providers`` 与 Jetson 遥测的用例。

**这是 S9b-2 那条「真机遥测没有可用来源」的回归钉**：153 上跑着 llama-server（8100）、
没有 vLLM Manager，旧的装配只认 manager_url，于是选出来的遥测是
``UnavailableTelemetryProvider``，采样器一个点都采不到。这一份把「四路里选哪一路」
逐路钉死——**尤其是非 Jetson 的本机 llama.cpp（就是 153 那台）**。

主机形态用 monkeypatch 控制（``is_jetson_host`` / ``is_apple_silicon`` / ``local_lan_ip``），
所以这一份在任何机器上跑出的结论都相同。
"""

from __future__ import annotations

import pytest

from photobench.infrastructure.runtime import selection as selection_module
from photobench.infrastructure.runtime.apple_telemetry import LocalAppleUnifiedTelemetryProvider
from photobench.infrastructure.runtime.jetson_telemetry import (
    LocalJetsonLlamaCppTelemetryProvider,
    _parse_tegrastats_line,
)
from photobench.infrastructure.runtime.providers import (
    HostNvidiaTelemetryProvider,
    LlamaCppTelemetryProvider,
    ManagerLifecycleProvider,
    ManagerTelemetryProvider,
    OllamaTelemetryProvider,
    UnavailableLifecycleProvider,
    UnavailableTelemetryProvider,
)
from photobench.infrastructure.runtime.selection import (
    detect_runtime_framework,
    is_orin_telemetry_host,
    resolve_runtime_framework,
    select_runtime_providers,
)


@pytest.fixture(autouse=True)
def _fixed_host(monkeypatch: pytest.MonkeyPatch):
    """把「这台机器是什么」固定成可控输入。**默认：不是 Jetson、不是 Apple、本机 IP 固定。**"""
    monkeypatch.setattr(selection_module, "is_jetson_host", lambda: False)
    monkeypatch.setattr(selection_module, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(selection_module, "local_lan_ip", lambda: "192.168.0.153")
    monkeypatch.delenv("SENTRIX_ORIN_TELEMETRY_HOST", raising=False)
    yield


# ---- 四路选择 -------------------------------------------------------------


def test_a_local_llama_cpp_endpoint_on_a_plain_gpu_host_selects_host_nvidia_smi():
    """**153 这一台的路**：llama.cpp + 本机端点 + 不是 Jetson → 宿主 nvidia-smi 遥测。

    这就是 S9b-2 采不到样本的反面——那时选出来的是 ``UnavailableTelemetryProvider``。
    **区分力**：断的是具体类，不是「不是 Unavailable」——一个错选成 Ollama 的实现
    也能通过后者。
    """
    selected = select_runtime_providers("", "http://127.0.0.1:8100/v1", "llama.cpp")
    assert isinstance(selected.telemetry, LlamaCppTelemetryProvider)
    assert isinstance(selected.lifecycle, UnavailableLifecycleProvider)
    assert selected.source == "host_nvidia_smi"


def test_a_local_endpoint_with_an_unknown_framework_still_gets_host_nvidia():
    """框架探测不出来（``/metrics`` 不通）时，本机端点仍走宿主 nvidia-smi 兜底。

    **区分力**：这条把「框架是 generic 就放弃遥测」的实现与 1.0 的兜底分开。
    """
    selected = select_runtime_providers("", "http://192.168.0.153:8100/v1", "generic")
    assert isinstance(selected.telemetry, HostNvidiaTelemetryProvider)
    assert selected.source == "host_nvidia_smi"


def test_a_local_endpoint_on_a_jetson_is_the_uma_pss_path(monkeypatch: pytest.MonkeyPatch):
    """Jetson + llama.cpp + 本机端点 → ``LocalJetsonLlamaCppTelemetryProvider``。

    **区分力**：这一支与上一支的差别只在 ``is_jetson_host()``，两者返回的 provider
    数字口径不同（UMA PSS vs 显存）——只看 source 字符串也能分开。
    """
    monkeypatch.setattr(selection_module, "is_jetson_host", lambda: True)
    selected = select_runtime_providers("", "http://127.0.0.1:8100/v1", "llama.cpp")
    assert isinstance(selected.telemetry, LocalJetsonLlamaCppTelemetryProvider)
    assert selected.source == "jetson_local_pss"


def test_a_local_endpoint_on_apple_silicon_is_the_unified_memory_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(selection_module, "is_apple_silicon", lambda: True)
    selected = select_runtime_providers("", "http://localhost:8100/v1", "llama.cpp")
    assert isinstance(selected.telemetry, LocalAppleUnifiedTelemetryProvider)
    assert selected.source == "apple_unified"


def test_a_remote_non_manager_endpoint_has_no_local_telemetry():
    """端点在另一台机器上、又没有 Manager：本机遥测与它无关，显式 unavailable。

    **区分力**：把远端端点也当成「本机」去跑 nvidia-smi 的实现，会报出这台机器的
    GPU 数字——那与端点那台机器毫无关系。
    """
    selected = select_runtime_providers("", "http://192.168.0.100:8100/v1", "llama.cpp")
    assert isinstance(selected.telemetry, UnavailableTelemetryProvider)
    assert selected.source == "unavailable"


def test_a_manager_with_a_local_endpoint_owns_both_lifecycle_and_telemetry():
    selected = select_runtime_providers("http://manager:8500", "http://127.0.0.1:8100/v1", "generic")
    assert isinstance(selected.lifecycle, ManagerLifecycleProvider)
    assert isinstance(selected.telemetry, ManagerTelemetryProvider)
    assert selected.source == "vllm_manager"


def test_a_manager_is_not_authoritative_for_a_remote_endpoint():
    """Manager 只管被管的 vLLM；一个远端外部端点不该因为它而假装有本机遥测（1.0 注释）。"""
    selected = select_runtime_providers("http://manager:8500", "http://10.0.0.9:8100/v1", "generic")
    assert isinstance(selected.lifecycle, ManagerLifecycleProvider)
    assert isinstance(selected.telemetry, UnavailableTelemetryProvider)


def test_a_manager_setting_does_not_hide_a_llama_cpp_endpoint():
    """框架是 llama.cpp 时，界面里遗留的 Manager 设置**不能**把这一轮变成 Manager 路径。

    1.0 的注释点名了这件事：外部 llama.cpp 端点的表单里可能还留着一条过期的
    ``manager_url``，那条设置会让 lifecycle 变成 Manager、遥测变成 Manager 遥测。
    """
    selected = select_runtime_providers("http://manager:8500", "http://127.0.0.1:8100/v1", "llama.cpp")
    assert isinstance(selected.lifecycle, UnavailableLifecycleProvider)
    assert isinstance(selected.telemetry, LlamaCppTelemetryProvider)


# ---- 框架探测 -------------------------------------------------------------


def test_port_11434_is_ollama_and_a_local_ollama_endpoint_gets_ollama_telemetry():
    assert resolve_runtime_framework("", "http://127.0.0.1:11434") == "ollama"
    selected = select_runtime_providers("", "http://127.0.0.1:11434", "ollama")
    assert isinstance(selected.telemetry, OllamaTelemetryProvider)


def test_an_explicit_framework_wins_over_the_port():
    assert resolve_runtime_framework("vllm", "http://127.0.0.1:11434") == "vllm"


def test_an_orin_host_on_port_8100_is_reported_as_llama_cpp(monkeypatch: pytest.MonkeyPatch):
    """1.0 ``:147``：8100 + 显式声明的 Orin 主机 → llama.cpp（118 上跑的就是它）。"""
    monkeypatch.setenv("SENTRIX_ORIN_TELEMETRY_HOST", "orin@192.168.0.118")
    assert is_orin_telemetry_host("192.168.0.118") is True
    assert resolve_runtime_framework("", "http://192.168.0.118:8100") == "llama.cpp"


def test_framework_detection_reads_the_metrics_endpoint():
    """``/metrics`` 里写着谁的指标就认谁（1.0 ``:160``）。

    **区分力**：注入的取文函数只认这两个特征串；一个不探测、直接返回 generic 的实现
    在这两条上都是 generic。
    """
    assert (
        detect_runtime_framework(
            "", "http://m:8000", request_text=lambda _url, _timeout: "vllm:num_requests_running 3\n"
        )
        == "vllm"
    )
    assert (
        detect_runtime_framework(
            "", "http://m:8000", request_text=lambda _url, _timeout: "llamacpp:kv_cache_tokens 12\n"
        )
        == "llama.cpp"
    )


def test_a_metrics_probe_that_fails_stays_generic():
    def boom(_url: str, _timeout: float) -> str:
        raise OSError("no /metrics")

    assert detect_runtime_framework("", "http://m:8000", request_text=boom) == "generic"


def test_detection_is_skipped_when_there_is_no_endpoint():
    def never_called(_url: str, _timeout: float) -> str:
        raise AssertionError("没有端点时不该去探测 /metrics")

    assert detect_runtime_framework("", "", request_text=never_called) == "generic"


# ---- Jetson 遥测本体 ------------------------------------------------------


def test_tegrastats_line_parsing_covers_ram_gpu_power_and_temperature():
    """1.0 ``_parse_tegrastats_line`` 逐字段照搬。

    **区分力**：四条规则各断言一次——一个只解析 RAM 的实现会在另外三条上返回空。
    """
    parsed = _parse_tegrastats_line("RAM 1234/8192MB GR3D_FREQ 45% VDD_GPU_SOC 7000mW gpu@55.5C cpu@40.25C")
    assert parsed["system_memory_used_mib"] == 1234
    assert parsed["system_memory_total_mib"] == 8192
    assert parsed["system_memory_available_mib"] == 8192 - 1234
    assert parsed["gpu_utilization_pct"] == 45
    assert parsed["power_draw_w"] is None
    assert parsed["power_rails_w"]["VDD_GPU_SOC"] == 7.0
    assert parsed["temperature_c"] == 55.5
    assert parsed["cpu_temperature_c"] == 40.25


def test_a_local_jetson_provider_needs_an_explicit_port():
    """端点没有显式端口 → 直接拒绝：采样一个「猜出来」的进程比不采样更糟。"""
    with pytest.raises(ValueError):
        LocalJetsonLlamaCppTelemetryProvider(endpoint_url="http://127.0.0.1")


def test_jetson_gpu_stats_always_returns_one_uma_gpu_row(monkeypatch: pytest.MonkeyPatch):
    """``gpu_stats`` 必须给出**恰一行** ``gpus``，采样器才有东西可采。

    S9b-2 的真机采样为空就是因为选了 ``UnavailableTelemetryProvider``（没有 ``gpus``）。
    这里把进程探测与设备读数全部替换成常量，断言的是**响应信封的形状**：
    ``status=available`` 且 ``data.gpus[0].memory_unit == process_pss_mib``——
    一个漏掉 ``gpus`` 的实现会让采样器静默地一个点都不采。
    """
    import photobench.infrastructure.runtime.jetson_telemetry as jetson_module

    # 进程归属那一步会真的扫 /proc（要遍历每个进程的 fd）；这条用例只关心响应信封的
    # 形状，所以把它换成恒等函数——否则这条断言的速度取决于这台机器上有多少进程。
    monkeypatch.setattr(
        jetson_module,
        "_augment_local_process_memory",
        lambda data, **_kw: {**(data or {}), "root_pid": 4242, "process_memory_used_mib": 1234.5},
    )
    provider = LocalJetsonLlamaCppTelemetryProvider(endpoint_url="http://127.0.0.1:8100")
    monkeypatch.setattr(provider, "_pid", lambda: 4242)
    monkeypatch.setattr(provider, "_llama_args", lambda _pid: {"cmdline": "llama-server --port 8100"})
    monkeypatch.setattr(provider, "_metrics", lambda: {})
    monkeypatch.setattr(
        provider,
        "_device",
        lambda: {
            "system_memory_used_mib": 3000.0,
            "system_memory_total_mib": 8192.0,
            "system_memory_available_mib": 5192.0,
            "system_memory_scope": "host_all_processes",
        },
    )
    result = provider.gpu_stats()
    assert result["status"] == "available"
    assert result["source"] == "jetson_local_pss"
    assert len(result["data"]["gpus"]) == 1
    assert result["data"]["gpus"][0]["memory_unit"] == "process_pss_mib"
    memory = provider.process_memory()
    assert memory["data"]["root_pid"] == 4242
    assert memory["data"]["process_memory_used_mib"] == 1234.5


def test_jetson_metrics_reads_the_llamacpp_kv_cache_counters(monkeypatch: pytest.MonkeyPatch):
    """llama.cpp 的 ``/metrics`` 里只有 ``llamacpp:kv_cache_*`` 那两条不带标签的才要。"""
    provider = LocalJetsonLlamaCppTelemetryProvider(endpoint_url="http://127.0.0.1:8100")

    class _Response:
        text = (
            "llamacpp:kv_cache_usage_ratio 0.25\n"
            "llamacpp:kv_cache_tokens 321\n"
            'llamacpp:kv_cache_tokens{label="x"} 999\n'
        )

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "photobench.infrastructure.runtime.jetson_telemetry.httpx.get",
        lambda *_a, **_k: _Response(),
    )
    metrics = provider._metrics()
    assert metrics["vllm_metrics"] == {"kv_cache_usage_pct": 25.0}
    assert metrics["kv_cache_used_tokens"] == 321


def test_a_probe_failure_never_reports_a_zero_memory(monkeypatch: pytest.MonkeyPatch):
    """探不到进程时**撤掉**内存读数并写明原因——0 是一个合法的测量值，不是「没读到」。"""
    provider = LocalJetsonLlamaCppTelemetryProvider(endpoint_url="http://127.0.0.1:8100")

    from photobench.infrastructure.runtime import jetson_telemetry as jetson

    monkeypatch.setattr(jetson, "_host_process_rows", lambda *_args: [])
    monkeypatch.setattr(provider, "_device", lambda: {})
    result = provider.gpu_stats()
    assert result["status"] == "available"
    # Device reads no longer perform a second PID discovery. Verify the public
    # process envelope independently: unknown allocation must remain unavailable.
    memory = provider.process_memory()
    data = memory["data"]
    assert data["process_memory_used_mib"] is None
    assert data["model_process_system_memory_used_mib"] is None
    assert data["memory_observations"]["topology"]["status"] != "available"


def test_the_sample_row_shape_is_what_the_aggregator_branches_on():
    """聚合分支的判据是样本自己的 ``memory_unit`` 与 ``source``（见 ``analysis``）。

    这条用一个最小样本把两个字段与 ``aggregate_gpu_samples`` 的分支对上——
    改坏任何一边都会让 UMA 的主机拿到显存口径的 ``memory_profile``。
    """
    from photobench.domain.runs.analysis import aggregate_gpu_samples

    aggregated = aggregate_gpu_samples(
        [
            {
                "memory_unit": "process_pss_uma_mib",
                "source": "jetson_local_pss",
                "model_process_memory_used_mib": 100.0,
                "system_memory_used_mib": 900.0,
            },
        ]
    )
    assert aggregated["source"] == "jetson_local_pss"
    assert aggregated["memory_profile"]["method"] == "orin_process_pss_uma_v1"


def test_cloud_disables_every_local_telemetry_branch(monkeypatch: pytest.MonkeyPatch):
    """``cloud=True`` 时即使本机跑着 llama.cpp 也不该去采本机的数（云端模型没有本机 GPU 指标）。

    **区分力**：忽略 ``cloud`` 的实现会在这条上返回 ``LlamaCppTelemetryProvider``。
    """
    selected = select_runtime_providers("", "http://127.0.0.1:8100/v1", "llama.cpp", cloud=True)
    assert isinstance(selected.telemetry, UnavailableTelemetryProvider)
    assert selected.source == "unavailable"

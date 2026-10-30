"""Jetson PSS and GPU allocation are independent; unknown ledgers stay missing."""

from __future__ import annotations

import pytest

from photobench.infrastructure.runtime import jetson_telemetry as jetson


@pytest.mark.parametrize(
    "ledger,pids,expected",
    [
        ({42: 2617.9}, {42}, 2617.9),
        ({42: 5, 43: 7}, {42, 43}, 12),
        ({}, {42}, 0),
        (None, {42}, None),
        ({42: 5}, set(), None),
    ],
)
def test_nvmap_is_independent_allocation_not_rss_residual(ledger, pids, expected) -> None:
    gpu, source = jetson._process_gpu_memory_mib(pids, ledger=ledger)
    assert gpu == expected
    assert source == ("nvmap_iovmm" if expected is not None else "unavailable")


def test_new_metadata_always_declares_separate_memory_and_gpu() -> None:
    data = jetson.apply_jetson_memory_metadata({"memory_unit": "process_pss_uma_mib"})
    assert data["memory_unit"] == "process_pss_mib"
    assert data["memory_scope"] == "process_pss_separate_gpu_allocation"
    assert data["memory_profile"]["method"] == "jetson_pss_and_nvmap_v3"


@pytest.mark.parametrize("missing_pid", [None, 43])
def test_exported_process_details_match_same_pss_reads_and_survive_json(monkeypatch, missing_pid):
    import json

    rows = [
        {"pid": pid, "start_ticks": str(pid), "listening_ports": [port], "rss_mib": 99}
        for pid, port in [(42, 8100), (43, 8291), (44, 8101)]
    ]
    monkeypatch.setattr(jetson, "_host_process_rows", lambda ports: rows)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: str(pid))
    reads = []

    def smaps(pid):
        reads.append(pid)
        return (None, None) if pid == missing_pid else (pid / 2, pid)

    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", smaps)
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 1024K\n")
    scope = {
        "main_model": {"ports": [8100]},
        "sentrix": {"ports": [8291]},
        "product": [{"ports": [8291]}],
        "embedding": {"mode": "test", "profiles": {"test": {"ports": [8101]}}},
    }
    sample = jetson._augment_local_process_memory(
        {}, model_pid=None, endpoint_port="8100", telemetry_services=scope
    )
    saved = json.loads(json.dumps(sample))
    details = saved["benchmark_processes"]
    assert sorted(reads) == [42, 43, 44]
    assert len(details) == 3
    for row in details:
        assert row["pss_mib"] == (None if row["pid"] == missing_pid else row["pid"] / 2)
        assert row["smaps_rss_mib"] == (None if row["pid"] == missing_pid else row["pid"])
        assert row["start_ticks"] == str(row["pid"])
    if missing_pid is None:
        assert saved["product_stack_memory_mib"] == sum(row["pss_mib"] for row in details)
    else:
        assert saved["product_stack_memory_mib"] is None
        assert saved["model_process_system_memory_used_mib"] == 21


def test_sampler_walks_each_process_once_even_when_device_stats_are_collected(monkeypatch):
    import builtins
    import io

    from photobench.application.run_execution.gpu_sampler import GpuSampler

    provider = jetson.LocalJetsonLlamaCppTelemetryProvider("http://127.0.0.1:8100", telemetry_services={})
    monkeypatch.setattr(provider, "_pid", lambda: 42)
    monkeypatch.setattr(provider, "_metrics", lambda: {})
    monkeypatch.setattr(provider, "_llama_args", lambda pid: {})
    monkeypatch.setattr(provider, "_device", lambda: {})
    monkeypatch.setattr(jetson, "_meminfo_mib", lambda: {"system_memory_used_mib": 100})
    jetson._nvmap_cache.clear()
    jetson._nvmap_cache.update(at=float("-inf"), table=None)
    # 这台机器上若有真实的 runtime_connection.json，scope 会覆盖关键词匹配路径，
    # 于是 pid 42 根本不会被扫到。这条用例验的是「每个进程只走一次 smaps」，
    # 不是「与机器上的部署配置交互」——把它隔离掉，CI 与开发机给出同一个答案。
    monkeypatch.setattr(jetson, "load_telemetry_services", lambda: None)
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 3072K\n")
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: "123")
    monkeypatch.setattr(
        jetson,
        "_host_process_rows",
        lambda ports: [
            {"pid": 42, "start_ticks": "123", "identity": "llama-server --port 8100", "rss_mib": 1}
        ],
    )
    walks = []

    def smaps(path, *args, **kwargs):
        walks.append(str(path))
        return io.StringIO("Rss: 1024 kB\nPss: 512 kB\n")

    monkeypatch.setattr(builtins, "open", smaps)
    sampler = GpuSampler(provider)
    sampler.sample_once()
    assert walks == ["/proc/42/smaps_rollup"]
    assert sampler.samples[0]["model_process_host_memory_mib"] == 0.5
    assert sampler.samples[0]["model_process_system_memory_used_mib"] == 0.5
    assert sampler.samples[0]["model_process_gpu_memory_mib"] == 3


def test_failed_optional_metrics_probe_never_reuses_previous_kv(monkeypatch):
    provider = jetson.LocalJetsonLlamaCppTelemetryProvider("http://127.0.0.1:8100")
    values = iter([{"kv_cache_used_tokens": 12}, {}])
    monkeypatch.setattr(provider, "_metrics", lambda: next(values))
    monkeypatch.setattr(provider, "_pid", lambda: 42)
    monkeypatch.setattr(provider, "_llama_args", lambda pid: {})
    monkeypatch.setattr(provider, "_device", lambda: {})
    provider.gpu_stats()
    assert provider.kv_cache()["data"]["used_tokens"] == 12
    provider.gpu_stats()
    assert provider.kv_cache()["status"] == "unavailable"


def test_busy_optional_metrics_endpoint_has_a_short_budget(monkeypatch):
    import httpx

    provider = jetson.LocalJetsonLlamaCppTelemetryProvider("http://127.0.0.1:8100")

    def busy(url, *, timeout, trust_env, follow_redirects):
        assert timeout <= 0.2
        assert trust_env is False and follow_redirects is False
        raise httpx.ReadTimeout("busy model metrics")

    monkeypatch.setattr(jetson.httpx, "get", busy)
    assert provider._metrics() == {}


def test_real_busy_metrics_http_does_not_wait_for_model_work():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    entered = threading.Event()
    release = threading.Event()

    class BusyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(3)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), BusyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = jetson.LocalJetsonLlamaCppTelemetryProvider(f"http://127.0.0.1:{server.server_port}")
        assert provider._metrics() == {}
        assert entered.is_set() and not release.is_set()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_nvmap_cache_preserves_observation_time_and_refreshes_once_expired(monkeypatch):
    clock, reads = [100.0], []
    monkeypatch.setattr(jetson.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})

    def read():
        reads.append(clock[0])
        clock[0] += 0.1
        return "llama 0 42 1024K\n"

    monkeypatch.setattr(jetson, "_read_nvmap_clients", read)
    assert jetson._nvmap_clients_mib() == {42: 1}
    first = dict(jetson._nvmap_cache["observation"])
    clock[0] = 101
    assert jetson._nvmap_clients_mib() == {42: 1}
    cached = jetson._nvmap_cache["observation"]
    assert cached["cache_hit"] is True
    assert cached["observed_at_monotonic"] == first["observed_at_monotonic"] == 100
    clock[0] = 103
    jetson._nvmap_clients_mib()
    assert reads == [100, 103]
    assert jetson._nvmap_cache["observation"]["observed_at_monotonic"] == 103


def test_sudo_nvmap_probe_uses_short_timeout_and_returns_unavailable(monkeypatch):
    import subprocess

    monkeypatch.setattr(jetson.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(jetson.shutil, "which", lambda name: "/usr/bin/sudo")

    def run(command, **options):
        assert command[:3] == ["/usr/bin/sudo", "-n", "cat"]
        assert options["timeout"] <= 0.2
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(jetson.subprocess, "run", run)
    assert jetson._read_nvmap_clients() is None


def test_listener_cache_revalidates_socket_and_process_identity(monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(jetson.time, "monotonic", lambda: clock[0])
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    for pid in (1, 2, 3):
        (proc / str(pid)).mkdir()
    (proc / "net/tcp").write_text(
        "header\n0: 0100007F:1FA4 0:0 0A 0 0 0 0 0 100\n1: 0100007F:1FAA 0:0 0A 0 0 0 0 0 200\n"
    )
    (proc / "net/tcp6").write_text("header\n")
    monkeypatch.setattr(
        jetson, "Path", lambda name: proc / str(name).removeprefix("/proc/") if str(name) != "/proc" else proc
    )
    identities = {1: "old", 2: "other", 3: "new"}
    sockets = {1: {"100"}, 2: {"200"}, 3: set()}
    calls = []
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: identities[pid])
    monkeypatch.setattr(jetson, "_listener_cache", {})

    def inodes(pid):
        calls.append(pid)
        return sockets[pid]

    monkeypatch.setattr(jetson, "_socket_inodes", inodes)
    assert jetson._listening_ports_by_pid({"8100"}) == {1: {"8100"}}
    calls.clear()
    clock[0] += 5
    assert jetson._listening_ports_by_pid({"8100"}) == {1: {"8100"}}
    assert calls == [1], "unchanged verified sockets must not trigger periodic full fd scans"
    # gpu_stats checks only the model port; process_memory checks the whole
    # frozen topology. Alternating those queries must not evict each other.
    assert jetson._listening_ports_by_pid({"8100", "8106"}) == {1: {"8100"}, 2: {"8106"}}
    calls.clear()
    assert jetson._listening_ports_by_pid({"8100"}) == {1: {"8100"}}
    assert calls == [1], "a different port selection must not force another full /proc scan"
    calls.clear()
    assert jetson._listening_ports_by_pid({"8100", "8106"}) == {1: {"8100"}, 2: {"8106"}}
    assert calls == [1, 2]
    # A reused PID with the same socket inode must not inherit old ownership.
    identities[1] = "reused"
    sockets[1], sockets[3] = set(), {"100"}
    assert jetson._listening_ports_by_pid({"8100"}) == {3: {"8100"}}


def test_listener_cache_checks_exact_fds_and_rediscovers_reused_descriptors(monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "net/tcp").write_text("header\n0: 0100007F:1FA4 0:0 0A 0 0 0 0 0 100\n")
    (proc / "net/tcp6").write_text("header\n")
    for pid in (1, 2):
        (proc / str(pid) / "fd").mkdir(parents=True)
        for fd in range(30):
            (proc / str(pid) / "fd" / str(fd)).symlink_to(f"socket:[{pid * 1000 + fd}]")
    listener = proc / "1/fd/31"
    listener.symlink_to("socket:[100]")
    monkeypatch.setattr(
        jetson, "Path", lambda name: proc / str(name).removeprefix("/proc/") if str(name) != "/proc" else proc
    )
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: str(pid))
    monkeypatch.setattr(jetson, "_listener_cache", {})
    reads = []
    original = jetson.os.readlink

    def readlink(path):
        reads.append(str(path))
        return original(path)

    monkeypatch.setattr(jetson.os, "readlink", readlink)
    assert jetson._listening_ports_by_pid({"8100"}) == {1: {"8100"}}
    reads.clear()
    assert jetson._listening_ports_by_pid({"8100"}) == {1: {"8100"}}
    assert reads == [str(listener)], "unrelated open fds must not be traversed on a verified cache hit"
    listener.unlink()
    listener.symlink_to("socket:[999]")
    (proc / "2/fd/31").symlink_to("socket:[100]")
    assert jetson._listening_ports_by_pid({"8100"}) == {2: {"8100"}}


def test_pid_identity_change_invalidates_collected_memory(monkeypatch):
    from photobench.domain.runtime.metrics import memory_freshness

    clock = [100.0]
    monkeypatch.setattr(jetson.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        jetson,
        "_host_process_rows",
        lambda ports: [
            {
                "pid": 42,
                "ppid": 0,
                "start_ticks": "old",
                "rss_mib": 10,
                "listening_ports": [8100],
            }
        ],
    )
    identities = iter(["old", "new", "new"])
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: next(identities))
    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", lambda pid: (8, 10))
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 20480K\n")
    data = jetson._augment_local_process_memory(
        {},
        model_pid=None,
        endpoint_port="8100",
        telemetry_services={"main_model": {"ports": [8100]}},
    )
    result, _ = memory_freshness({**data, "_t": 100.1})
    assert result["model_process_system_memory_used_mib"] is None
    assert result["product_stack_memory_mib"] is None


def test_slow_topology_has_one_job_and_cannot_starve_memory_or_refresh_old_facts(monkeypatch):
    import threading
    import time

    from photobench.domain.runtime.metrics import memory_freshness
    from photobench.infrastructure.runtime.bounded_probe import BoundedProbe

    gate, entered = threading.Event(), threading.Event()
    calls = []
    row = {"pid": 42, "start_ticks": "42", "rss_mib": 20, "listening_ports": [8100]}

    def slow(ports):
        calls.append(1)
        entered.set()
        gate.wait(10)
        return [row]

    monkeypatch.setattr(jetson, "_host_process_rows", slow)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: "42")
    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", lambda pid: (8, 10))
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 10240K\n")
    original = time.monotonic() - 4
    snapshot = {"started": original, "finished": original + 0.1, "value": [row]}
    topology, memory = BoundedProbe(limit=1), BoundedProbe(limit=4)
    try:
        for _ in range(2):
            start = time.monotonic()
            data = jetson._augment_local_process_memory(
                {},
                model_pid=None,
                endpoint_port="8100",
                telemetry_services={"main_model": {"ports": [8100]}},
                probe=memory,
                topology_probe=topology,
                topology_snapshot=snapshot,
            )
            assert time.monotonic() - start < 2.9
            assert entered.is_set() and calls == [1]
            assert data["memory_observations"]["topology"]["observed_at_monotonic"] == original
            assert data["memory_observations"]["process:42:pss"]["status"] == "available"
            normalized, _ = memory_freshness({**data, "_t": time.monotonic()})
            assert normalized["product_stack_memory_mib"] is None
            assert topology.pending_work() == ["topology"]
    finally:
        gate.set()


def test_visual_load_smaps_finishes_within_bounded_window(monkeypatch):
    import time

    from photobench.domain.runtime.metrics import memory_freshness
    from photobench.infrastructure.runtime.bounded_probe import BoundedProbe

    row = {"pid": 42, "start_ticks": "42", "rss_mib": 20, "listening_ports": [8100]}
    monkeypatch.setattr(jetson, "_host_process_rows", lambda ports: [row])
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: "42")

    def smaps(pid):
        time.sleep(0.75)
        return 8, 10

    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", smaps)
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 10240K\n")
    start = time.monotonic()
    data = jetson._augment_local_process_memory(
        {},
        model_pid=None,
        endpoint_port="8100",
        telemetry_services={"main_model": {"ports": [8100]}},
        probe=BoundedProbe(limit=4),
    )
    assert 0.7 <= time.monotonic() - start < 2
    normalized, _ = memory_freshness({**data, "_t": time.monotonic()})
    assert normalized["memory_complete"] is True
    assert normalized["model_process_host_memory_mib"] == 8


@pytest.mark.parametrize("explicit_pid", [False, True])
def test_delayed_topology_is_refreshed_before_memory_walk_ages_it_out(monkeypatch, explicit_pid):
    import time

    from photobench.domain.runtime.metrics import memory_freshness
    from photobench.infrastructure.runtime.bounded_probe import BoundedProbe

    row = {"pid": 42, "start_ticks": "42", "rss_mib": 20, "listening_ports": [8100]}

    def topology(ports):
        time.sleep(0.25)
        return [row]

    def smaps(pid):
        time.sleep(0.75)
        return 8, 10

    monkeypatch.setattr(jetson, "_host_process_rows", topology)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: "42")
    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", smaps)
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 10240K\n")
    snapshot = {"started": time.monotonic() - 2.5, "value": [row]}
    main = {"ports": [8100], **({"pids": [42]} if explicit_pid else {})}
    started = time.monotonic()
    data = jetson._augment_local_process_memory(
        {},
        model_pid=None,
        endpoint_port="8100",
        telemetry_services={"main_model": main},
        probe=BoundedProbe(limit=4),
        topology_probe=BoundedProbe(limit=1),
        topology_snapshot=snapshot,
    )
    assert data["memory_observations"]["topology"]["observed_at_monotonic"] >= started
    normalized, _ = memory_freshness({**data, "_t": time.monotonic()})
    assert normalized["memory_complete"] is True


def test_probe_budget_stops_additional_smaps_and_does_not_claim_complete_memory(monkeypatch):
    clock, walks = [100.0], []
    monkeypatch.setattr(jetson.time, "monotonic", lambda: clock[0])
    rows = [
        {
            "pid": pid,
            "ppid": 0,
            "start_ticks": str(pid),
            "rss_mib": 10,
            "listening_ports": [8100 if pid == 1 else 8103],
        }
        for pid in (1, 2)
    ]
    monkeypatch.setattr(jetson, "_host_process_rows", lambda ports: rows)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: str(pid))

    def smaps(pid):
        walks.append(pid)
        clock[0] += 1.2
        return 8, 10

    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", smaps)
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 1 20480K\nclip 0 2 10240K\n")
    result = jetson._augment_local_process_memory(
        {},
        model_pid=None,
        endpoint_port="8100",
        telemetry_services={
            "main_model": {"ports": [8100]},
            "embedding": {
                "mode": "clip",
                "profiles": {"clip": {"ports": [8103]}},
            },
        },
    )
    assert walks == [1]
    assert result["product_stack_memory_mib"] is None
    assert result["memory_observations"]["process:2:pss"]["reason"] == "process_probe_budget_exhausted"


def test_port_discovery_inspects_only_actual_listener_candidates(monkeypatch, tmp_path):
    provider = jetson.LocalJetsonLlamaCppTelemetryProvider(
        "http://127.0.0.1:8104", pid_file=str(tmp_path / "absent" / "pid")
    )
    monkeypatch.setattr(jetson, "_listening_ports_by_pid", lambda ports: {42: {"8104"}, 43: {"8104"}})
    seen = []

    def verify(pid):
        seen.append(pid)
        if pid == 42:
            raise ValueError("listener is not the selected inference server")
        return pid

    monkeypatch.setattr(provider, "_pid_from_proc", verify)
    # No /proc directory inventory exists in this test; only listener IDs are usable.
    from pathlib import Path

    monkeypatch.setattr(
        jetson, "Path", lambda value: tmp_path / "missing-proc" if str(value) == "/proc" else Path(value)
    )
    assert provider._pid_from_port() == 43
    assert seen == [42, 43]
    monkeypatch.setattr(jetson, "_listening_ports_by_pid", lambda ports: {})
    with pytest.raises(ValueError, match="no llama-server"):
        provider._pid_from_port()


def test_scoped_process_inventory_reads_details_only_for_listeners_and_new_children(monkeypatch, tmp_path):
    from pathlib import Path

    proc = tmp_path / "proc"
    proc.mkdir()

    def write_stat(pid, parent, ticks="123"):
        directory = proc / str(pid)
        directory.mkdir(exist_ok=True)
        fields = ["S", str(parent)] + ["0"] * 17 + [ticks]
        (directory / "stat").write_text(f"{pid} (name with spaces) " + " ".join(fields))

    for pid, parent in ((1, 0), (2, 1), (3, 2), (8, 0)):
        write_stat(pid, parent)
    monkeypatch.setattr(
        jetson,
        "Path",
        lambda value: (
            proc / str(value).removeprefix("/proc/")
            if str(value).startswith("/proc/")
            else proc
            if str(value) == "/proc"
            else Path(value)
        ),
    )
    monkeypatch.setattr(
        jetson,
        "_listening_ports_by_pid",
        lambda ports: {1: {"8104"}, 8: {"8106"}} if not ports else {1: {"8104"}},
    )

    def parents():
        return {
            int(p.name): int((p / "stat").read_text().rsplit(")", 1)[1].split()[1]) for p in proc.iterdir()
        }

    monkeypatch.setattr(jetson, "_process_parent_snapshot", parents)
    identity_reads = []
    identity = jetson._process_identity

    def read_identity(pid):
        identity_reads.append(pid)
        return identity(pid)

    monkeypatch.setattr(jetson, "_process_identity", read_identity)
    reads = []
    monkeypatch.setattr(jetson, "_process_rss_mib", lambda pid: reads.append(pid) or 10.0)
    monkeypatch.setattr(jetson, "_process_cmdline", lambda pid: "/usr/bin/llama-server --port 8104")
    rows = jetson._host_process_rows({"8104"})
    assert {r["pid"] for r in rows} == {1, 2, 3}
    assert set(reads) == {1, 2, 3}, "unrelated same-name models must not incur RSS/command probes"
    assert 8 not in identity_reads, "port scope must not scan unrelated process stat files"
    write_stat(4, 1)
    assert {r["pid"] for r in jetson._host_process_rows({"8104"})} == {1, 2, 3, 4}
    assert {r["pid"] for r in jetson._host_process_rows()} == {1, 2, 3, 4, 8}

    # Unavailable native snapshot must trigger complete proc ancestry discovery.
    def unavailable():
        raise OSError("ps unavailable")

    monkeypatch.setattr(jetson, "_process_parent_snapshot", unavailable)
    assert {r["pid"] for r in jetson._host_process_rows({"8104"})} == {1, 2, 3, 4}

    def reuse(pid):
        if pid == 2:
            write_stat(2, 1, "reused")
        return 10.0

    monkeypatch.setattr(jetson, "_process_rss_mib", reuse)
    assert 2 not in {r["pid"] for r in jetson._host_process_rows({"8104"})}


@pytest.mark.parametrize("selector", ["legacy", "pid", "keyword", "remote_keyword", "port"])
def test_port_inventory_optimization_preserves_non_port_service_selectors(monkeypatch, selector):
    main = {"ports": [8104]}
    product = {"ports": [8291]}
    if selector == "pid":
        product = {"pids": [7], "ports": [8291]}
    elif selector == "keyword":
        product = {"keywords": ["sentrix-private-worker"]}
    elif selector == "remote_keyword":
        product = {"keywords": ["sentrix-private-worker"], "local": False}
    scope = {} if selector == "legacy" else {"main_model": main, "product": [product]}
    rows = [
        {
            "pid": 1,
            "ppid": 0,
            "start_ticks": "1",
            "listening_ports": [8104],
            "identity": "llama-server --port 8104",
            "rss_mib": 10,
        },
        {
            "pid": 7,
            "ppid": 0,
            "start_ticks": "7",
            "listening_ports": [],
            "identity": "sentrix-private-worker",
            "rss_mib": 10,
        },
    ]
    selections, sampled = [], []

    def inventory(ports):
        selections.append(ports)
        return rows if ports is None else rows[:1]

    monkeypatch.setattr(jetson, "_host_process_rows", inventory)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: str(pid))
    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", lambda pid: sampled.append(pid) or (8, 10))
    monkeypatch.setattr(jetson, "SYSTEM_PROCESS_KEYWORDS", ("sentrix-private-worker",))
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 1 20480K\n")
    jetson._augment_local_process_memory({}, model_pid=1, endpoint_port="8104", telemetry_services=scope)
    if selector in {"legacy", "pid", "keyword"}:
        assert selections == [None]
        assert sampled == [1, 7], "non-listening local services must not disappear from accounting"
    else:
        assert selections[0] is not None
        assert sampled == [1], "explicit port scope must not match unrelated same-name services"


@pytest.mark.parametrize("changed_membership", [False, True])
@pytest.mark.parametrize("topology_seconds,smaps_seconds", [(1.1, 0.3), (1.6, 0.3), (1.6, 1.4)])
def test_topology_finishing_during_or_after_smaps_is_joined_with_identity_check(
    monkeypatch, changed_membership, topology_seconds, smaps_seconds
):
    import time

    from photobench.domain.runtime.metrics import memory_freshness
    from photobench.infrastructure.runtime.bounded_probe import BoundedProbe

    row = {"pid": 42, "ppid": 1, "start_ticks": "42", "rss_mib": 20, "listening_ports": [8100]}
    calls = []

    def topology(ports):
        calls.append(1)
        time.sleep(topology_seconds)
        fresh = [{**row, "rss_mib": 30}]
        if changed_membership:
            fresh.append({"pid": 43, "ppid": 42, "start_ticks": "43", "rss_mib": 5})
        return fresh

    def smaps(pid):
        time.sleep(smaps_seconds)
        return 8, 10

    monkeypatch.setattr(jetson, "_host_process_rows", topology)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: str(pid))
    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", smaps)
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 10240K\n")
    original = time.monotonic() - 1.8
    snapshot = {"started": original, "finished": original + 0.1, "value": [row]}
    probe = BoundedProbe(limit=1)
    started = time.monotonic()
    data = jetson._augment_local_process_memory(
        {},
        model_pid=None,
        endpoint_port="8100",
        telemetry_services={"main_model": {"ports": [8100]}},
        probe=BoundedProbe(limit=4),
        topology_probe=probe,
        topology_snapshot=snapshot,
    )
    finished = time.monotonic()
    assert topology_seconds - 0.05 < finished - started < topology_seconds + 0.35
    assert calls == [1] and probe.pending_work() == [], "harvesting must not start extra work"
    assert snapshot["started"] >= started
    normalized, _ = memory_freshness({**data, "_t": finished})
    if changed_membership:
        assert normalized["memory_complete"] is False
        assert data["memory_observations"]["topology"]["reason"] == "topology_changed_during_memory_read"
        assert len(snapshot["value"]) == 2
    else:
        assert normalized["memory_complete"] is True
        assert data["memory_observations"]["topology"]["observed_at_monotonic"] == snapshot["started"]
        assert (
            started <= data["memory_observations"]["process:42:pss"]["observed_at_monotonic"] < started + 0.5
        )
        assert data["model_system_processes"][0]["rss_mib"] == 30


def test_previous_topology_finishing_during_memory_launches_current_successor(monkeypatch):
    import threading
    import time

    from photobench.domain.runtime.metrics import memory_freshness
    from photobench.infrastructure.runtime.bounded_probe import BoundedProbe

    row = {"pid": 42, "ppid": 1, "start_ticks": "42", "rss_mib": 20, "listening_ports": [8100]}
    entered, release = threading.Event(), threading.Event()
    calls = []

    def topology(ports):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            release.wait(10)
        return [{**row, "rss_mib": 30}]

    def smaps(pid):
        # The old discovery cannot finish until memory starts: serial waiting
        # would consume the initial deadline before reaching this signal.
        release.set()
        time.sleep(0.2)
        return 8, 10

    monkeypatch.setattr(jetson, "_host_process_rows", topology)
    monkeypatch.setattr(jetson, "_process_start_ticks", lambda pid: str(pid))
    monkeypatch.setattr(jetson, "_process_smaps_memory_mib", smaps)
    monkeypatch.setattr(jetson, "_nvmap_cache", {"at": float("-inf"), "table": None})
    monkeypatch.setattr(jetson, "_read_nvmap_clients", lambda: "llama 0 42 10240K\n")
    probe = BoundedProbe(limit=1)
    probe.collect({"topology": lambda: topology({"8100"})}, budget=0)
    assert entered.wait(1)
    original = time.monotonic() - 1.8
    snapshot = {"started": original, "finished": original + 0.1, "value": [row]}
    started = time.monotonic()
    try:
        data = jetson._augment_local_process_memory(
            {},
            model_pid=None,
            endpoint_port="8100",
            telemetry_services={"main_model": {"ports": [8100]}},
            probe=BoundedProbe(limit=4),
            topology_probe=probe,
            topology_snapshot=snapshot,
        )
        assert time.monotonic() - started < 0.8
        assert calls == [1, 1] and probe.pending_work() == []
        assert snapshot["started"] >= started
        normalized, _ = memory_freshness({**data, "_t": time.monotonic()})
        assert normalized["memory_complete"] is True
        assert data["memory_observations"]["process:42:pss"]["observed_at_monotonic"] < snapshot["started"]
    finally:
        release.set()


def test_gpu_thermal_sensor_reads_only_gpu_and_preserves_idle_missing(monkeypatch, tmp_path):
    import errno
    import os

    cpu = tmp_path / "thermal_zone0"
    gpu = tmp_path / "thermal_zone7"
    cpu.mkdir()
    gpu.mkdir()
    (cpu / "type").write_text("cpu-thermal\n")
    (cpu / "temp").write_text("65000\n")
    (gpu / "type").write_text("gpu-thermal\n")
    (gpu / "temp").write_text("49562\n")
    value = jetson._gpu_temperature(tmp_path)
    assert value["temperature_c"] == 49.562
    assert value["gpu_temperature_sensor"] == str(gpu / "temp")
    assert value["gpu_temperature_method"] == "jetson_gpu_thermal_sysfs"
    (gpu / "temp").write_text("-256000\n")
    assert (
        jetson._gpu_temperature(tmp_path)["resource_missing_reasons"]["temperature_c"]
        == "gpu_temperature_invalid_reading"
    )

    def idle(fd, size):
        raise BlockingIOError(errno.EAGAIN, os.strerror(errno.EAGAIN))

    monkeypatch.setattr(jetson.os, "read", idle)
    missing = jetson._gpu_temperature(tmp_path)
    assert missing["temperature_c"] is None
    assert (
        missing["resource_missing_reasons"]["temperature_c"]
        == "gpu_temperature_sensor_temporarily_unavailable"
    )


def test_gpu_temperature_uses_its_own_observation_age_and_source():
    from photobench.domain.runtime.metrics import device_freshness, resource_records

    sample = {
        "protocol_version": "photobench-resource-telemetry-v1",
        "source": "jetson_local_pss",
        "_t": 100.0,
        "temperature_c": 49.562,
        "cpu_temperature_c": 65,
        "device_observed_at_monotonic": 90.0,
        "gpu_temperature_observed_at_monotonic": 99.9,
        "gpu_temperature_method": "jetson_gpu_thermal_sysfs",
    }
    fresh, rejected = device_freshness(sample)
    assert fresh["temperature_c"] == 49.562
    assert fresh["cpu_temperature_c"] is None
    assert "temperature_c" not in rejected
    record = resource_records(fresh)["gpu_temperature"]
    assert record["method"] == "jetson_gpu_thermal_sysfs"
    assert record["observed_at_monotonic"] == 99.9
    assert record["status"] == "available"
    expired, rejected = device_freshness({**sample, "_t": 105.0, "device_observed_at_monotonic": 105.0})
    assert expired["temperature_c"] is None
    assert rejected == ["temperature_c"]
    assert resource_records(expired)["gpu_temperature"]["reason"] == "gpu_temperature_observation_expired"

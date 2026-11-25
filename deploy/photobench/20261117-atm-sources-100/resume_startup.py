"""继续本次已完成文件更新的启动阶段；先核对落盘源码与既有回执。"""

from pathlib import Path
import json, sqlite3, subprocess, time, urllib.request, sys
from preflight import SERVICES, inspect, sha

BASE = Path(__file__).resolve().parent
manifest = json.loads((BASE / "manifest.json").read_text())
before = json.loads((BASE / "before.json").read_text())
intent = json.loads((BASE / "intent.json").read_text())
assert intent["revision"] == manifest["source_revision"]
assert not (BASE / "acceptance.json").exists()
finalize_only = sys.argv[1:] == ["--finalize-only"]
assert finalize_only or not (BASE / "resume-startup-intent.json").exists()
for port, (_, root, variant) in SERVICES.items():
    for relative, expected in manifest["variants"][variant]["files"].items():
        assert sha(Path(root) / relative) == expected["after"], (port, relative)
    assert (
        json.loads((Path(root) / "build-info.json").read_text())["revision"] == manifest["deployed_version"]
    )
if not finalize_only:
    (BASE / "resume-startup-intent.json").write_text(
        json.dumps(
            {
                "started_at": time.time(),
                "reason": "WeMM dependency was already stopped before this deployment; restored existing service",
            }
        )
    )


def idle(snapshot):
    for port, item in snapshot["services"].items():
        if int(port) >= 8700:
            assert item["active_work"]["work"] == [], f"{port}: evaluation is busy"
        else:
            for data in item["databases"].values():
                turns = data.get("active_turns", [])
                assert all(r[1] == "pending" and r[2].startswith("2026-11-10") for r in turns), turns
                assert len(turns) == (10 if port == "8291" else 0), turns
                for table in ["import_jobs", "video_jobs"]:
                    assert all(
                        status.lower()
                        not in [
                            "running",
                            "queued",
                            "retry_scheduled",
                            "waiting_dependency",
                            "processing",
                            "retry_wait",
                        ]
                        or count == 0
                        for status, count in data.get(table + "_statuses", [])
                    ), table


def wait(port, path):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{path}", timeout=4) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"{port}: health did not become ready")


if not finalize_only:
    print("Starting updated products", flush=True)
    for port in [8191, 8291, 8294]:
        subprocess.run(["systemctl", "--user", "start", SERVICES[port][0]], check=True, timeout=15)
        wait(port, "health/live")
        print(f"{port}: ready", flush=True)
    print("Starting updated evaluation services", flush=True)
    for port in [8772, 8872, 8874]:
        subprocess.run(["systemctl", "--user", "start", SERVICES[port][0]], check=True, timeout=15)
        wait(port, "chat/sessions")
        print(f"{port}: ready", flush=True)

after = inspect(manifest)
idle(after)
for port, item in after["services"].items():
    variant = port if int(port) < 8700 else "photobench"
    assert item["source"] == {p: v["after"] for p, v in manifest["variants"][variant]["files"].items()}, (
        f"{port}: deployed source mismatch"
    )
    prior = before["services"][port]
    if int(port) < 8700:
        assert item["databases"] == prior["databases"], f"{port}: database inventory changed"
    else:
        assert item["result_inventory"] == prior["result_inventory"], f"{port}: historical result changed"
        assert item["configured_product_revision"] == manifest["deployed_version"]
        assert item["env"]["PHOTOBENCH_BUILD_REVISION"] == manifest["deployed_version"]
    kind = "product" if int(port) < 8700 else "photobench"
    for relative, value in manifest["dist"][kind].items():
        assert item["dist_files"][relative] == value
for path, value in before["protected"].items():
    if Path(path).name == "runtime_connection.json":
        previous = json.loads((BASE / "backup" / path.lstrip("/")).read_text())
        current = json.loads(Path(path).read_text())
        assert current == {**previous, "product_build_revision": manifest["deployed_version"]}, path
    else:
        assert after["protected"][path] == value, path
# The service startup applies the committed SQL migration; all old core rows stay intact.
for port in [8191, 8291, 8294]:
    for database in after["services"][str(port)]["databases"]:
        with sqlite3.connect(database) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(asset_places)")}
            assert {"precise_address", "address_source", "address_source_sha256"} <= columns
# Register the two new, independently stored packages in every dataset catalog.
for port in [8772, 8872, 8874]:
    data_root = Path(after["services"][str(port)]["env"]["PHOTOBENCH_DATA_ROOT"])
    for name in ["2026.11.17-atm-sources-addresses", "2026.11.17-atm-sources-smoke"]:
        source = Path("/var/tmp/sentrix-atm-sources-20261117") / name
        assert (source / "manifest.json").is_file(), str(source)
        target = data_root / name
        if target.is_symlink():
            assert target.resolve() == source.resolve(), str(target)
        else:
            assert not target.exists(), str(target)
            target.symlink_to(source, target_is_directory=True)
(BASE / "after.json").write_text(json.dumps(after, indent=2) + "\n")
(BASE / "acceptance.json").write_text(
    json.dumps(
        {
            "status": "completed",
            "revision": manifest["source_revision"],
            "deployed_version": manifest["deployed_version"],
            "pids": {p: v["pid"] for p, v in after["services"].items()},
            "protected_files": len(before["protected"]),
            "historical_results": {
                p: len(v["result_inventory"]) for p, v in after["services"].items() if int(p) >= 8700
            },
        },
        indent=2,
    )
    + "\n"
)
print((BASE / "acceptance.json").read_text(), flush=True)

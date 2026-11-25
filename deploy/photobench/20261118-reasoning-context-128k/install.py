"""Publish this committed context-budget release once, while the paired services are idle."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/home/realmagic/xy/deploy/20261117-atm-sources-release")
from preflight import SERVICES, inspect, sha

BASE = Path(__file__).resolve().parent
manifest = json.loads((BASE / "manifest.json").read_text())
expected = json.loads((BASE / "expected-before.json").read_text())


def save(name, value):
    (BASE / name).write_text(json.dumps(value, indent=2) + "\n")


def idle(snapshot):
    for port, item in snapshot["services"].items():
        if int(port) >= 8700:
            assert item["active_work"]["work"] == [], (port, "busy")
        else:
            for stats in item["databases"].values():
                turns = stats.get("active_turns", [])
                assert len(turns) == (10 if port == "8291" else 0), (port, turns)
                assert all(r[1] == "pending" and r[2].startswith("2026-11-10") for r in turns)
                for table in ["import_jobs", "video_jobs"]:
                    assert not any(
                        count and status in {"running", "queued", "processing", "retry_scheduled", "waiting_dependency"}
                        for status, count in stats.get(table + "_statuses", [])
                    ), (port, table)


def backup(path):
    dest = BASE / "backup" / str(path).lstrip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, dest)


def wait(port):
    endpoint = "health/live" if port < 8700 else "active-work"
    for _attempt in range(45):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/{endpoint}", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"{port}: startup failed")


assert not (BASE / "intent.json").exists(), "Already started; inspect the deployment receipt."
before = inspect(manifest)
idle(before)
assert before["protected"] == expected["protected"], "Configuration or chat changed"
for port, item in before["services"].items():
    prior = expected["services"][port]
    for key in ["pid", "build_info", "env", "databases", "result_inventory", "dist_files"]:
        assert item.get(key) == prior.get(key), (port, key)
    variant = SERVICES[int(port)][2]
    assert item["source"] == {p: row["before"] for p, row in manifest["variants"][variant]["files"].items()}
for variant, details in manifest["variants"].items():
    for relative, row in details["files"].items():
        assert sha(BASE / "payload" / variant / relative) == row["after"]
save("before.json", before)
save("intent.json", {"revision": manifest["source_revision"], "started_at": time.time()})
products, evaluations = [8191, 8291, 8294], [8772, 8872, 8874]
try:
    for port in evaluations:
        subprocess.run(["systemctl", "--user", "stop", SERVICES[port][0]], check=True, timeout=60)
    # Recheck persisted turns after stopping the measurement request producers.
    import sqlite3

    for port in products:
        for database, stats in before["services"][str(port)]["databases"].items():
            with sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True) as connection:
                turns = [list(r) for r in connection.execute(
                    "select id,status,created_at from agent_turns where status in ('pending','running') order by id"
                )]
            assert turns == stats["active_turns"], (port, "became busy")
        subprocess.run(["systemctl", "--user", "stop", SERVICES[port][0]], check=True, timeout=60)
    for port in products:
        unit, root, variant = SERVICES[port]
        root = Path(root)
        for relative, row in manifest["variants"][variant]["files"].items():
            target = root / relative
            assert sha(target) == row["before"]
            backup(target)
            shutil.copy2(BASE / "payload" / variant / relative, target)
        build = root / "build-info.json"
        backup(build)
        info = json.loads(build.read_text())
        info["revision"] = manifest["deployed_version"]
        build.write_text(json.dumps(info, indent=2) + "\n")
        if port in [8191, 8294]:
            env_path = BASE / f"context-{port}.env"
            env_path.write_text("SENTRIX_VLM_REASONING_CONTEXT_TOKENS=131072\n")
            drop = Path.home() / ".config/systemd/user" / (unit + ".d") / "99zzz-reasoning-context.conf"
            assert not drop.exists(), str(drop)
            drop.parent.mkdir(parents=True, exist_ok=True)
            drop.write_text("[Service]\nEnvironmentFile=" + str(env_path) + "\n")
    for port in evaluations:
        cfg = Path(before["services"][str(port)]["env"]["PHOTOBENCH_CONFIG_ROOT"]) / "runtime_connection.json"
        backup(cfg)
        connection = json.loads(cfg.read_text())
        connection["product_build_revision"] = manifest["deployed_version"]
        cfg.write_text(json.dumps(connection, ensure_ascii=False, indent=2) + "\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    for port in products + evaluations:
        subprocess.run(["systemctl", "--user", "start", SERVICES[port][0]], check=True, timeout=15)
        wait(port)
        print(f"{port}: ready", flush=True)
    after = inspect(manifest)
    idle(after)
    for port, item in after["services"].items():
        variant = SERVICES[int(port)][2]
        prior = before["services"][port]
        assert item["source"] == {p: row["after"] for p, row in manifest["variants"][variant]["files"].items()}
        if int(port) < 8700:
            assert item["databases"] == prior["databases"], (port, "database changed")
        else:
            for key in ["result_inventory", "dist_files", "env"]:
                assert item[key] == prior[key], (port, key)
            assert item["configured_product_revision"] == manifest["deployed_version"]
    for path, value in before["protected"].items():
        if Path(path).name == "runtime_connection.json":
            previous = json.loads((BASE / "backup" / path.lstrip("/")).read_text())
            current = json.loads(Path(path).read_text())
            assert current == {**previous, "product_build_revision": manifest["deployed_version"]}
        else:
            assert after["protected"][path] == value, path
    save("after.json", after)
    save("acceptance.json", {
        "status": "passed", "revision": manifest["source_revision"], "deployed_version": manifest["deployed_version"],
        "pids": {port: item["pid"] for port, item in after["services"].items()},
        "source_files_per_product": 5, "cloud_reasoning_tokens": 131072,
        "historical_results_and_chat_preserved": True,
    })
    print((BASE / "acceptance.json").read_text(), flush=True)
except Exception:
    # Keep the service units running for inspection if an acceptance assertion fails.
    for port in products + evaluations:
        subprocess.run(["systemctl", "--user", "start", SERVICES[port][0]], check=False, timeout=15)
    raise

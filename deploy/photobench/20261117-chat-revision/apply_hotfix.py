"""One-time server100 chat hotfix: verify published files, replace two files, restart benchmarks."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
MANIFEST = json.loads((PACKAGE / "release.json").read_text())
UNITS = {
    8772: "sentrix2-photobench.service",
    8872: "sentrix-atm100-photobench.service",
    8874: "sentrix-atm100-doubao-photobench.service",
    8191: "sentrix2-api.service",
    8291: "sentrix-atm100-api.service",
    8294: "sentrix-atm100-doubao-api.service",
}


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
        return json.load(response)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process(unit):
    pid = int(subprocess.check_output(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]))
    assert pid > 0, unit
    proc = Path(f"/proc/{pid}")
    env = dict(x.split("=", 1) for x in (proc / "environ").read_text().split("\0") if "=" in x)
    return pid, (proc / "cwd").resolve(), env


state = {"status": "preflight", "services": {}, "protected": {}, "products": {}}
roots = set()
for port, unit in UNITS.items():
    pid, root, env = process(unit)
    if port < 8700:
        database = Path(env["SENTRIX_DATA_DIR"]) / "sentrix.db"
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            active = connection.execute(
                "SELECT id,status,created_at FROM agent_turns WHERE status IN ('pending','running')"
            ).fetchall()
        # The published 8291 adapter isolates exactly these historical pending turns.
        assert not active or (
            port == 8291
            and len(active) == 10
            and all(row[1] == "pending" and str(row[2]) < "2026-11-11" for row in active)
        ), (port, active)
        state["products"][str(port)] = {"pid": pid, "active": active, "root": str(root)}
        continue
    assert not get(port, "/api/v1/active-work")["work"], port
    for name, expected in MANIFEST["files"].items():
        assert sha(root / name) == expected["before"], (port, name, "source changed since published baseline")
    roots.add(root)
    config = Path(env["PHOTOBENCH_CONFIG_ROOT"])
    results = Path(env["PHOTOBENCH_RESULTS_ROOT"])
    chat = results.with_name(results.name + "-chat")
    for path in [*config.rglob("*"), *chat.glob("*.json")]:
        if path.is_file():
            state["protected"][str(path)] = sha(path)
    inventory = {
        str(path): [path.stat().st_size, path.stat().st_mtime_ns]
        for path in results.rglob("*")
        if path.is_file()
    }
    state["services"][str(port)] = {
        "unit": unit,
        "pid_before": pid,
        "root": str(root),
        "result_inventory": inventory,
    }

(PACKAGE / "before.json").write_text(json.dumps(state, indent=2))
for root in roots:
    for name, expected in MANIFEST["files"].items():
        target, source = root / name, PACKAGE / "payload" / name
        assert sha(source) == expected["after"], name
        backup = PACKAGE / "backup" / root.name / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        shutil.copyfile(source, target)
        assert sha(target) == expected["after"], name

version_file = PACKAGE / "version.env"
version_file.write_text(f"PHOTOBENCH_BUILD_REVISION={MANIFEST['deployed_version']}\n")
for service in state["services"].values():
    dropin = Path.home() / ".config/systemd/user" / (service["unit"] + ".d") / "99z-chat-revision.conf"
    assert not dropin.exists(), dropin
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text(f"[Service]\nEnvironmentFile={version_file}\n")
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
for port, service in state["services"].items():
    assert not get(int(port), "/api/v1/active-work")["work"], port
    subprocess.run(["systemctl", "--user", "restart", service["unit"]], check=True)
    for _attempt in range(30):
        try:
            config = get(int(port), "/api/v1/config")
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError(f"Service did not return: {port}")
    assert config["build_revision"] == MANIFEST["deployed_version"], port
    service["pid_after"] = process(service["unit"])[0]
    assert not get(int(port), "/api/v1/active-work")["work"], port

for path, digest in state["protected"].items():
    assert sha(Path(path)) == digest, path
for service in state["services"].values():
    for path, old_stat in service["result_inventory"].items():
        if Path(path).name == ".photobench-instance.lock":
            continue
        current = Path(path).stat()
        assert [current.st_size, current.st_mtime_ns] == old_stat, path
for port, product in state["products"].items():
    assert process(UNITS[int(port)])[0] == product["pid"], port
state["status"] = "completed"
state["source_revision"] = MANIFEST["source_revision"]
state["deployed_version"] = MANIFEST["deployed_version"]
(PACKAGE / "acceptance.json").write_text(json.dumps(state, indent=2))
print(
    json.dumps(
        {
            "status": state["status"],
            "version": state["deployed_version"],
            "services": {
                p: {k: v for k, v in s.items() if k != "result_inventory"}
                for p, s in state["services"].items()
            },
            "protected_files": len(state["protected"]),
            "products": state["products"],
        }
    )
)

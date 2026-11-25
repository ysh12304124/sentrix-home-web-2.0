"""Finish the interrupted one-time version registration after the published 99 drop-in reset."""

import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

package = Path(__file__).resolve().parent
manifest = json.loads((package / "release.json").read_text())
state = json.loads((package / "before.json").read_text())


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
        return json.load(response)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid(unit):
    return int(subprocess.check_output(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]))


for port, service in state["services"].items():
    assert not get(port, "/api/v1/active-work")["work"]
    for name, expected in manifest["files"].items():
        assert sha(Path(service["root"]) / name) == expected["after"]
for product in state["products"].values():
    assert Path(f"/proc/{product['pid']}").exists()
if "--verify-only" not in sys.argv:
    for service in state["services"].values():
        directory = Path.home() / ".config/systemd/user" / (service["unit"] + ".d")
        initial = directory / "30-chat-revision.conf"
        final = directory / "99z-chat-revision.conf"
        assert initial.exists() and not final.exists()
        initial.rename(final)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
for port, service in state["services"].items():
    assert not get(port, "/api/v1/active-work")["work"]
    if "--verify-only" not in sys.argv:
        subprocess.run(["systemctl", "--user", "restart", service["unit"]], check=True)
    for _ in range(30):
        try:
            config = get(port, "/api/v1/config")
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError(f"Service did not return: {port}")
    assert config["build_revision"] == manifest["deployed_version"], port
    assert not get(port, "/api/v1/active-work")["work"]
    service["pid_after"] = pid(service["unit"])
for path, digest in state["protected"].items():
    assert sha(Path(path)) == digest, path
for service in state["services"].values():
    for path, old_stat in service["result_inventory"].items():
        if Path(path).name == ".photobench-instance.lock":
            continue
        current = Path(path).stat()
        assert [current.st_size, current.st_mtime_ns] == old_stat, path
for product in state["products"].values():
    assert Path(f"/proc/{product['pid']}").exists()
state.update(
    status="completed",
    source_revision=manifest["source_revision"],
    deployed_version=manifest["deployed_version"],
)
(package / "acceptance.json").write_text(json.dumps(state, indent=2))
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

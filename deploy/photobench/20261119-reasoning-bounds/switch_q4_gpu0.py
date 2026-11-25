"""Move the existing Q4 service to GPU0 once, before bounded-budget activation."""

import hashlib
import importlib.util
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "start_q4km.sh"
UNIT = "sentrix-atm100-model.service"
OLD_GPU = "GPU-4ef9200c-d8f3-638a-3eeb-26fe4fa3d7b0"
NEW_GPU = "GPU-bd82fa9d-0906-ce2c-9932-a2c021551203"
EXPECTED = "62b04dd701a175dd169951208c38d94f96da4fc20a4b8bd3e0fd807fbe9f1df9"


def command(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def main():
    spec = importlib.util.spec_from_file_location("bounded_release", HERE / "release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    before = release.inspect()
    release.require_idle(before)
    original = SCRIPT.read_bytes()
    assert hashlib.sha256(original).hexdigest() == EXPECTED
    assert original.count(OLD_GPU.encode()) == 1
    gpu_before = command("nvidia-smi", "--query-gpu=uuid,memory.free", "--format=csv,noheader,nounits")
    free = dict(line.split(", ", 1) for line in gpu_before.splitlines())
    assert int(free[NEW_GPU]) >= 7000
    protected = {
        u: release.pid(u)
        for u in [
            "sentrix2-api.service",
            "sentrix2-photobench.service",
            "sentrix-atm100-api.service",
            "sentrix-atm100-doubao-api.service",
            "sentrix-atm100-photobench.service",
            "sentrix-atm100-doubao-photobench.service",
        ]
    }
    release.save("gpu0-switch-intent.json", {"before": before, "gpu": gpu_before, "protected": protected})
    with (HERE / "start_q4km.gpu1-before.sh").open("xb") as handle:
        handle.write(original)
    temporary = SCRIPT.with_suffix(".gpu0.tmp")
    temporary.write_bytes(original.replace(OLD_GPU.encode(), NEW_GPU.encode()))
    temporary.chmod(SCRIPT.stat().st_mode)
    subprocess.run(["bash", "-n", str(temporary)], check=True, timeout=10)
    os.replace(temporary, SCRIPT)
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True, timeout=70)
    deadline = time.monotonic() + 120
    while True:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8100/v1/models", timeout=2) as response:
                models = json.load(response)
            assert [row["id"] for row in models["data"]] == ["qwen3.5-4b-q4_k_m"]
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    new_pid = release.pid(UNIT)
    apps = command("nvidia-smi", "--query-compute-apps=pid,gpu_uuid", "--format=csv,noheader")
    rows = [line.split(", ", 1) for line in apps.splitlines()]
    assert [new_pid, NEW_GPU] in rows
    assert all(p != before["protected"][UNIT] for p, _ in rows)
    assert {u: release.pid(u) for u in protected} == protected
    release.save(
        "gpu0-switch-response.json",
        {
            "pid": new_pid,
            "gpu_uuid": NEW_GPU,
            "models": models,
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            "protected": protected,
            "compute_apps": rows,
        },
    )
    print(json.dumps({"pid": new_pid, "gpu_uuid": NEW_GPU, "protected_unchanged": True}))


if __name__ == "__main__":
    main()

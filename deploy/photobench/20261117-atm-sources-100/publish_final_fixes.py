"""空闲时发布来源导出修复，并保留100既有独立产品入口的构建模式。"""

from pathlib import Path
import json, shutil, subprocess, time, urllib.request
from preflight import inspect, SERVICES, sha

BASE = Path(__file__).resolve().parent
manifest = json.loads((BASE / "manifest.json").read_text())
fix = json.loads((BASE / "final-fixes-manifest.json").read_text())
assert not (BASE / "final-fixes-intent.json").exists()
before = inspect(manifest)
for port in [8772, 8872, 8874]:
    assert before["services"][str(port)]["active_work"]["work"] == []
for port in [8191, 8291, 8294]:
    for stats in before["services"][str(port)]["databases"].values():
        assert all(
            row[1] == "pending" and row[2].startswith("2026-11-10") for row in stats.get("active_turns", [])
        )
for relative, expected in fix["files"].items():
    assert sha(BASE / "final-fixes" / relative) == expected, relative
roots = [Path(SERVICES[p][1]) for p in [8772, 8872]]
component = "apps/photobench/src/photobench/domain/runs/exports.py"
for root in roots:
    assert sha(root / component) == fix["before"], str(root)
(BASE / "final-fixes-intent.json").write_text(
    json.dumps({"revision": fix["source_revision"], "started_at": time.time()})
)
for port in [8772, 8872, 8874]:
    subprocess.run(["systemctl", "--user", "stop", SERVICES[port][0]], check=True)
for root in roots:
    saved = BASE / "final-fixes-backup" / str(root).lstrip("/") / component
    saved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / component, saved)
    shutil.copy2(BASE / "final-fixes" / component, root / component)
    assert sha(root / component) == fix["files"][component]
for port in [8191, 8291, 8294]:
    dist = Path(before["services"][str(port)]["dist_root"])
    for relative in sorted(fix["files"], key=lambda name: name.endswith("index.html")):
        prefix = "apps/web/dist/"
        if not relative.startswith(prefix):
            continue
        target = dist / relative.removeprefix(prefix)
        saved = BASE / "final-fixes-backup" / str(target).lstrip("/")
        saved.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / "final-fixes" / relative, target)
        assert sha(target) == fix["files"][relative]
for port in [8772, 8872, 8874]:
    subprocess.run(["systemctl", "--user", "start", SERVICES[port][0]], check=True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/active-work", timeout=4) as response:
                assert json.load(response)["work"] == []
                break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError(str(port))
after = inspect(manifest)
for port in [8772, 8872, 8874]:
    assert (
        before["services"][str(port)]["result_inventory"] == after["services"][str(port)]["result_inventory"]
    )
for port in [8191, 8291, 8294]:
    assert before["services"][str(port)]["databases"] == after["services"][str(port)]["databases"]
assert before["protected"] == after["protected"]
(BASE / "final-fixes-acceptance.json").write_text(
    json.dumps(
        {
            "status": "passed",
            "source_revision": fix["source_revision"],
            "preserved_historical_results": True,
            "preserved_configuration": True,
            "legacy_local_auth": True,
        },
        indent=2,
    )
)
print((BASE / "final-fixes-acceptance.json").read_text())

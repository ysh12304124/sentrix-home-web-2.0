"""Stage committed checkpoint reuse code into a new Q4-only candidate."""

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path("/home/realmagic/xy/experiments/atm1013-20261118")
OLD, NEW = BASE / "q4-read-release", BASE / "q4-trace-reuse-release"
manifest = json.loads((HERE / "manifest.json").read_text())
assert not NEW.exists()
for name, row in manifest["files"].items():
    source = OLD / name
    assert (hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None) == row["before"]
    assert hashlib.sha256((HERE / "overlay" / name).read_bytes()).hexdigest() == row["after"]
shutil.copytree(OLD, NEW, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
for name in manifest["files"]:
    target = NEW / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "overlay" / name, target)
result = {"release": str(NEW), "manifest": manifest, "services_restarted": []}
with (HERE / "stage-response.json").open("x") as handle:
    json.dump(result, handle, indent=2)
print(json.dumps(result))

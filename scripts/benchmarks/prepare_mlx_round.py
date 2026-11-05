"""Restore an independent accepted library and launch one PhotoBench round."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import httpx

r = Path(os.environ["SENTRIX_MLX_EXPERIMENT_ROOT"]).resolve()
repo = r / "repo"
python = repo / ".venv/bin/python"
a = argparse.ArgumentParser()
a.add_argument("round")
args = a.parse_args()
stage = r / "rounds" / args.round
stage.mkdir(parents=True, exist_ok=True)
data = stage / "data"
config = r / "private/photobench-config"
connection = json.loads((config / "runtime_connection.json").read_text())
if not data.exists():
    command = [
        str(python),
        str(repo / "scripts/benchmarks/memory_snapshot.py"),
        "restore",
        "--source",
        str(r / "source200/snapshots/accepted-baseline-v3"),
        "--identity",
        str(r / "source200/runtime/memory-baseline-identity.json"),
        "--destination",
        str(data),
        "--receipt",
        str(stage / "restore-receipt.json"),
        "--consumer-migrations",
        str(repo / "apps/api/src/sentrix/infrastructure/persistence/migrations"),
    ]
    with (stage / "restore.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=repo)
else:
    raise RuntimeError("round data already exists; inspect before reuse")
baseenv = {
    **os.environ,
    "LC_ALL": "en_US.UTF-8",
    "PYTHONUTF8": "1",
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
    "SENTRIX_DATA_DIR": str(data),
    "SENTRIX_VLM_BASE_URL": "http://127.0.0.1:18100/v1",
    "SENTRIX_EMBEDDING_SCHEME": "legacy",
    "SENTRIX_TEXT_EMBEDDER_URL": "http://127.0.0.1:18101",
    "SENTRIX_LEGACY_CLIP_URL": "http://127.0.0.1:18103",
}
for key, value in connection["product_settings"].items():
    envkey = {"face_providers": "FACE_PROVIDERS", "retinaface_providers": "RETINAFACE_PROVIDERS"}.get(
        key, "SENTRIX_" + key.upper()
    )
    baseenv[envkey] = str(value).lower() if isinstance(value, bool) else str(value)
apiargs = [
    str(python),
    "-m",
    "uvicorn",
    "--factory",
    "sentrix.main:create_app",
    "--host",
    "127.0.0.1",
    "--port",
    "18191",
]
pbargs = [
    str(python),
    "-m",
    "uvicorn",
    "--factory",
    "photobench.main:create_app",
    "--host",
    "127.0.0.1",
    "--port",
    "18772",
]
baseenv.update(
    PHOTOBENCH_CONFIG_ROOT=str(config),
    PHOTOBENCH_DATA_ROOT=str(r / "source200/datasets"),
    PHOTOBENCH_RESULTS_ROOT=str(r / "results"),
    PHOTOBENCH_SENTRIX_API_URL="http://127.0.0.1:18191",
    PHOTOBENCH_SENTRIX_REPO_ROOT=str(repo),
    PHOTOBENCH_SENTRIX_START_COMMAND=" ".join(apiargs),
    PHOTOBENCH_SENTRIX_PID_FILE=str(stage / "api-process.json"),
    PHOTOBENCH_SENTRIX_LOG_PATH=str(stage / "api.log"),
    PHOTOBENCH_WEB_DIST_ROOT=str(repo / "apps/photobench/web/dist"),
)
(stage / "environment.private.json").write_text(
    json.dumps({k: v for k, v in baseenv.items() if k.startswith(("SENTRIX_", "PHOTOBENCH_"))}, indent=2)
)
api = subprocess.Popen(
    apiargs,
    stdout=(stage / "api.log").open("ab"),
    stderr=subprocess.STDOUT,
    env=baseenv,
    cwd=repo,
    start_new_session=True,
)
(stage / "api-process.json").write_text(
    json.dumps(
        {
            "pid": api.pid,
            "endpoint": baseenv["SENTRIX_VLM_BASE_URL"],
            "runtime_connection": connection,
            "argv": apiargs,
        },
        indent=2,
    )
)
with httpx.Client(timeout=15, trust_env=False) as client:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if api.poll() is not None:
            raise RuntimeError("API exited before ready")
        try:
            health = client.get("http://127.0.0.1:18191/api/v1/health")
            health.raise_for_status()
            if health.json().get("embeddings") == "ok":
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    else:
        raise RuntimeError("API health deadline")
    diagnostics = client.get("http://127.0.0.1:18191/api/v1/diagnostics")
    diagnostics.raise_for_status()
    d = diagnostics.json()
    (stage / "diagnostics.private.json").write_text(json.dumps(d, indent=2))
    effective = d["runtime_configuration"]
    assert all(effective[k] == v for k, v in connection["product_settings"].items()), (
        "runtime configuration differs"
    )
    bench = subprocess.Popen(
        pbargs,
        stdout=(stage / "photobench.log").open("ab"),
        stderr=subprocess.STDOUT,
        env=baseenv,
        cwd=repo,
        start_new_session=True,
    )
    (stage / "photobench-process.json").write_text(json.dumps({"pid": bench.pid, "argv": pbargs}, indent=2))
    (r / "runtime/current-round.json").write_text(
        json.dumps(
            {"round": args.round, "api_pid": api.pid, "bench_pid": bench.pid, "at": time.time()}, indent=2
        )
    )
    for _ in range(60):
        if bench.poll() is not None:
            raise RuntimeError("PhotoBench exited before ready")
        try:
            response = client.get("http://127.0.0.1:18772/api/v1/runs")
            response.raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(1)
    else:
        raise RuntimeError("PhotoBench health deadline")
print(json.dumps({"round": args.round, "api_pid": api.pid, "bench_pid": bench.pid, "status": "ready"}))

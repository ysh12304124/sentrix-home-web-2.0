#!/usr/bin/env bash
# Start the local upload/chat API and the 487QA PhotoBench UI against this worktree.
# Existing model and embedding sidecars are external processes and are never restarted here.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJ"

if [ -f "$PROJ/data/local-mac.env" ]; then
  # Machine-specific paths only; this file lives under ignored data/ and contains no secrets.
  # shellcheck disable=SC1091
  set -a
  . "$PROJ/data/local-mac.env"
  set +a
fi

PYTHON="${SENTRIX_PYTHON_BIN:-$PROJ/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  echo "Python runtime not found: $PYTHON (run make install or set SENTRIX_PYTHON_BIN)" >&2
  exit 1
fi

export SENTRIX_DEPLOYMENT_PROFILE="${SENTRIX_DEPLOYMENT_PROFILE:-mac}"
export SENTRIX_EXPERIENCE_MODE="${SENTRIX_EXPERIENCE_MODE:-true}"
export SENTRIX_DATA_DIR="${SENTRIX_DATA_DIR:-$PROJ/data}"
export SENTRIX_EMBEDDING_SCHEME="${SENTRIX_EMBEDDING_SCHEME:-legacy}"
export SENTRIX_VLM_BASE_URL="${SENTRIX_VLM_BASE_URL:-http://127.0.0.1:18100/v1}"
export SENTRIX_TEXT_EMBEDDER_URL="${SENTRIX_TEXT_EMBEDDER_URL:-http://127.0.0.1:8101}"
export SENTRIX_LEGACY_CLIP_URL="${SENTRIX_LEGACY_CLIP_URL:-http://127.0.0.1:8103}"
export FACE_PROVIDERS="${FACE_PROVIDERS:-CPUExecutionProvider}"
export RETINAFACE_PROVIDERS="${RETINAFACE_PROVIDERS:-CPUExecutionProvider}"
export PHOTOBENCH_PORT="${PHOTOBENCH_PORT:-8772}"
export PHOTOBENCH_DATA_ROOT="${PHOTOBENCH_DATA_ROOT:-}"
export PHOTOBENCH_CONFIG_ROOT="${PHOTOBENCH_CONFIG_ROOT:-$PROJ/apps/photobench/config}"
export PHOTOBENCH_RESULTS_ROOT="${PHOTOBENCH_RESULTS_ROOT:-$SENTRIX_DATA_DIR/photobench-results}"
export PHOTOBENCH_WEB_DIST_ROOT="${PHOTOBENCH_WEB_DIST_ROOT:-$PROJ/apps/photobench/web/dist}"
export PHOTOBENCH_SENTRIX_API_URL="${PHOTOBENCH_SENTRIX_API_URL:-http://127.0.0.1:8191}"
export PHOTOBENCH_SENTRIX_REPO_ROOT="$PROJ"
export PHOTOBENCH_SENTRIX_START_COMMAND="$PYTHON -m uvicorn --factory sentrix.main:create_app --host 127.0.0.1 --port 8191 --app-dir apps/api/src"

# Pass product settings to PhotoBench's model-switch restart path. The same
# environment must survive a benchmark-triggered restart of the upload API.
export PHOTOBENCH_SENTRIX_DATA_DIR="$SENTRIX_DATA_DIR"
export PHOTOBENCH_SENTRIX_EXPERIENCE_MODE="$SENTRIX_EXPERIENCE_MODE"
export PHOTOBENCH_SENTRIX_API_PORT="8191"
export PHOTOBENCH_SENTRIX_VLM_BASE_URL="$SENTRIX_VLM_BASE_URL"
export PHOTOBENCH_SENTRIX_EMBEDDING_SCHEME="$SENTRIX_EMBEDDING_SCHEME"
export PHOTOBENCH_SENTRIX_TEXT_EMBEDDER_URL="$SENTRIX_TEXT_EMBEDDER_URL"
export PHOTOBENCH_SENTRIX_LEGACY_CLIP_URL="$SENTRIX_LEGACY_CLIP_URL"
export PHOTOBENCH_SENTRIX_FACE_PROVIDERS="$FACE_PROVIDERS"
export PHOTOBENCH_SENTRIX_RETINAFACE_PROVIDERS="$RETINAFACE_PROVIDERS"

if [ ! -d "$PHOTOBENCH_DATA_ROOT/album3-max-video10" ]; then
  echo "Set PHOTOBENCH_DATA_ROOT to the directory containing album3-max-video10" >&2
  exit 1
fi
"$PYTHON" - "$PHOTOBENCH_DATA_ROOT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]) / "album3-max-video10"
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
qa_path = root / manifest["qa_sets"]["mixed-image-video-487q"]
qa_bytes = qa_path.read_bytes()
qa_rows = [line for line in qa_bytes.splitlines() if line.strip()]
qa_sha = hashlib.sha256(qa_bytes).hexdigest()
expected_sha = "73fa4512dde072fb0df9cf45ff3ebd192192db606f9764054bde002dcbf0be47"
if len(qa_rows) != 487 or qa_sha != expected_sha:
    raise SystemExit(f"487QA integrity mismatch: count={len(qa_rows)} sha256={qa_sha}")
if len(manifest.get("photos", [])) != 334 or len(manifest.get("videos", [])) != 10:
    raise SystemExit("album3-max-video10 manifest is not the expected 334-photo/10-video dataset")
print(f"Verified album3-max-video10: 334 photos, 10 videos, 487 QA, sha256={qa_sha}")
PY
if [ ! -f "$PHOTOBENCH_WEB_DIST_ROOT/index.html" ]; then
  echo "PhotoBench UI is not built; run: cd apps/photobench/web && npm ci && npm run build" >&2
  exit 1
fi

wait_http() {
  local url="$1"
  local tries="${2:-60}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# Dependencies must be functionally reachable before accepting uploads or starting QA.
for endpoint in \
  "$SENTRIX_VLM_BASE_URL/models" \
  "$SENTRIX_TEXT_EMBEDDER_URL/health" \
  "$SENTRIX_LEGACY_CLIP_URL/health"; do
  if ! wait_http "$endpoint" 3; then
    echo "Required model endpoint is unavailable: $endpoint" >&2
    exit 1
  fi
done

if ! wait_http "$PHOTOBENCH_SENTRIX_API_URL/api/v1/health" 3; then
  if lsof -nP -iTCP:8191 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port 8191 is occupied by an unhealthy service; refusing to stop an unknown process" >&2
    exit 1
  fi
  mkdir -p "$PROJ/logs"
  echo "Starting Sentrix upload/chat API on 8191"
  nohup env \
    SENTRIX_DEPLOYMENT_PROFILE="$SENTRIX_DEPLOYMENT_PROFILE" \
    SENTRIX_EXPERIENCE_MODE="$SENTRIX_EXPERIENCE_MODE" \
    SENTRIX_DATA_DIR="$SENTRIX_DATA_DIR" \
    SENTRIX_EMBEDDING_SCHEME="$SENTRIX_EMBEDDING_SCHEME" \
    SENTRIX_VLM_BASE_URL="$SENTRIX_VLM_BASE_URL" \
    SENTRIX_TEXT_EMBEDDER_URL="$SENTRIX_TEXT_EMBEDDER_URL" \
    SENTRIX_LEGACY_CLIP_URL="$SENTRIX_LEGACY_CLIP_URL" \
    FACE_PROVIDERS="$FACE_PROVIDERS" \
    RETINAFACE_PROVIDERS="$RETINAFACE_PROVIDERS" \
    PYTHONPATH="$PROJ/apps/api/src" \
    "$PYTHON" -m uvicorn --factory sentrix.main:create_app \
      --host 127.0.0.1 --port 8191 --app-dir apps/api/src \
      > "$PROJ/logs/api.log" 2>&1 < /dev/null &
fi
wait_http "$PHOTOBENCH_SENTRIX_API_URL/api/v1/health" 120 || {
  tail -60 "$PROJ/logs/api.log" >&2 || true
  echo "Sentrix API did not become ready" >&2
  exit 1
}
if ! curl -fsS "$PHOTOBENCH_SENTRIX_API_URL/api/v1/internal/status" >/dev/null 2>&1; then
  echo "Sentrix API is up but experience mode/import executor is not enabled" >&2
  exit 1
fi
# Register the already running local API with PhotoBench. Matching endpoint,
# embedding and product settings prevents a 487 run using the current model from
# needlessly restarting the upload/chat API. A later intentional model switch
# still uses the reproducible command/environment configured above.
"$PYTHON" - "$PROJ/apps/photobench/logs/sentrix-process.json" "$PHOTOBENCH_SENTRIX_API_URL" "$PHOTOBENCH_CONFIG_ROOT/runtime_connection.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

record_path = Path(sys.argv[1])
api_url = sys.argv[2].rstrip("/")
runtime_path = Path(sys.argv[3])
pids = subprocess.run(
    ["lsof", "-ti", "tcp:8191", "-sTCP:LISTEN"],
    check=True,
    capture_output=True,
    text=True,
).stdout.split()
if len(pids) != 1 or not pids[0].isdigit():
    raise SystemExit(f"expected one local Sentrix listener on 8191, found {pids}")
config = json.loads(runtime_path.read_text(encoding="utf-8"))
if str(config.get("sentrix_url", "")).rstrip("/") != api_url:
    raise SystemExit(f"PhotoBench runtime config points to another Sentrix API: {config.get('sentrix_url')}")
endpoint = str(config.get("model_base_url") or "").rstrip("/")
if not endpoint or not isinstance(config.get("embedding"), dict):
    raise SystemExit("PhotoBench runtime config is missing its model endpoint or embedding scheme")
record_path.parent.mkdir(parents=True, exist_ok=True)
record_path.write_text(
    json.dumps({"pid": int(pids[0]), "endpoint": endpoint, "runtime_connection": config}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"Registered existing Sentrix listener pid={pids[0]} endpoint={endpoint}")
PY

if ! wait_http "http://127.0.0.1:$PHOTOBENCH_PORT/api/v1/manifests" 2; then
  if lsof -nP -iTCP:"$PHOTOBENCH_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $PHOTOBENCH_PORT is occupied by an unhealthy service; refusing to stop it" >&2
    exit 1
  fi
  mkdir -p "$PROJ/logs" "$PHOTOBENCH_RESULTS_ROOT"
  echo "Starting PhotoBench on $PHOTOBENCH_PORT with $PHOTOBENCH_DATA_ROOT"
  nohup env PYTHONPATH="$PROJ/apps/photobench/src" "$PYTHON" \
    -m uvicorn --factory photobench.main:create_app \
    --host 127.0.0.1 --port "$PHOTOBENCH_PORT" --app-dir apps/photobench/src \
    > "$PROJ/logs/photobench.log" 2>&1 < /dev/null &
fi
wait_http "http://127.0.0.1:$PHOTOBENCH_PORT/api/v1/manifests" 90 || {
  tail -60 "$PROJ/logs/photobench.log" >&2 || true
  echo "PhotoBench did not become ready" >&2
  exit 1
}

echo "Sentrix:   $PHOTOBENCH_SENTRIX_API_URL"
echo "PhotoBench: http://127.0.0.1:$PHOTOBENCH_PORT"
echo "Dataset:    $PHOTOBENCH_DATA_ROOT/album3-max-video10 (487 QA)"

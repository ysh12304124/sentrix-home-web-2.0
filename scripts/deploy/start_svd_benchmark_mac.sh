#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
export PHOTOBENCH_SENTRIX_REPO_ROOT="$ROOT"
export SENTRIX_EMBEDDING_SCHEME=legacy
export SENTRIX_DATA_DIR="$ROOT/data"
export SENTRIX_VLM_BASE_URL="http://127.0.0.1:8100/v1"
export SENTRIX_TEXT_EMBEDDER_URL="http://127.0.0.1:8101"
export SENTRIX_LEGACY_CLIP_URL="http://127.0.0.1:8103"
export SENTRIX_AGENT_WALL_TIME_S=600
export SENTRIX_VIDEO_KEYFRAME_ALGORITHM=svd_lowrank

cd "$ROOT"
exec .venv/bin/python -m uvicorn --factory photobench.main:create_app \
  --host 127.0.0.1 --port 8772 --app-dir apps/photobench/src

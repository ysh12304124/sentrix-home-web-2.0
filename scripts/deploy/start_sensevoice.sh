#!/usr/bin/env bash
# Host-owned SenseVoice-Small / sherpa-onnx service. Local weights are provisioned before launch.
# Called by the Mac model stack or the shared systemd unit.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJ"
export PYTHONPATH="$PROJ/apps/api/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -f deploy/thread-limits.env ]; then
  # shellcheck disable=SC1091
  . deploy/thread-limits.env
fi

exec .venv/bin/python -m scripts.maintenance.sensevoice_sidecar "$@"

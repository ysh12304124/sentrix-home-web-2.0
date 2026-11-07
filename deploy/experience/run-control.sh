#!/usr/bin/env bash
# 控制面启动器：只从环境读取监听与证书，不接受命令行用户输入。
set -euo pipefail

PYTHON="${SENTRIX_CONTROL_PYTHON:-/opt/sentrix/current/.venv/bin/python}"
HOST="${SENTRIX_CONTROL_LISTEN_HOST:-127.0.0.1}"
PORT="${SENTRIX_CONTROL_LISTEN_PORT:-8210}"

ARGS=(
  -m uvicorn
  --factory sentrix.control.main:create_app
  --host "$HOST"
  --port "$PORT"
  --workers 1
  --app-dir /opt/sentrix/current/apps/api/src
)

if [[ -n "${SENTRIX_CONTROL_TLS_CERT_FILE:-}" && -n "${SENTRIX_CONTROL_TLS_KEY_FILE:-}" ]]; then
  ARGS+=(--ssl-certfile "$SENTRIX_CONTROL_TLS_CERT_FILE")
  ARGS+=(--ssl-keyfile "$SENTRIX_CONTROL_TLS_KEY_FILE")
fi

exec "$PYTHON" "${ARGS[@]}"

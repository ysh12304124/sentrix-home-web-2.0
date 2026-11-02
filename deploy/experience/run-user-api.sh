#!/usr/bin/env bash
# 每用户 API 启动器：端口与令牌全部来自该用户的 0600 环境文件。
set -euo pipefail

ACCOUNT="${1:?account id is required}"
PYTHON="${SENTRIX_API_PYTHON:-/opt/sentrix/current/.venv/bin/python}"
HOST="127.0.0.1"
PORT="${SENTRIX_API_PORT:?SENTRIX_API_PORT is required}"

exec "$PYTHON" -m uvicorn \
  --factory sentrix.main:create_app \
  --host "$HOST" \
  --port "$PORT" \
  --workers 1 \
  --app-dir /opt/sentrix/current/apps/api/src

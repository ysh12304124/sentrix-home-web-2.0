#!/usr/bin/env bash
# Sentrix Home Web 2.0 一键停止
set -euo pipefail

WAIT_TIMEOUT=10

echo "停止 Sentrix Home Web 2.0 全部服务..."
echo ""

# 杀死端口对应的进程（带 SIGKILL 回退）
kill_port() {
  local port="$1" name="$2"
  local pids

  pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)

  if [[ -z "$pids" ]]; then
    echo "  - $name (:$port) 未运行"
    return 0
  fi

  for pid in $pids; do
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  done

  local elapsed=0
  while (( elapsed < WAIT_TIMEOUT )); do
    local still_running=false
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then
        still_running=true
        break
      fi
    done

    if ! $still_running; then
      echo "  ✓ 停止 $name (:$port)"
      return 0
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo "  ⚠️  $name (:$port) 未在 ${WAIT_TIMEOUT}s 内停止，发送 SIGKILL..."
  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
    pkill -9 -P "$pid" 2>/dev/null || true
  done

  sleep 1
  echo "  ✓ 强制停止 $name (:$port)"
}

# 按名称杀死进程
kill_by_name() {
  local pattern="$1" name="$2"
  local pids

  pids=$(pgrep -f "$pattern" 2>/dev/null || true)

  if [[ -z "$pids" ]]; then
    echo "  - $name 未运行"
    return 0
  fi

  for pid in $pids; do
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  done

  local elapsed=0
  while (( elapsed < WAIT_TIMEOUT )); do
    local still_running=false
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then
        still_running=true
        break
      fi
    done

    if ! $still_running; then
      echo "  ✓ 停止 $name"
      return 0
    fi

    sleep 1
    elapsed=$((elapsed + 1))
  done

  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
    pkill -9 -P "$pid" 2>/dev/null || true
  done

  sleep 1
  echo "  ✓ 强制停止 $name"
}

# 2.0 服务
kill_port 8191 "Sentrix 2.0 API"
kill_port 8772 "PhotoBench 2.0"
kill_port 8103 "Chinese-CLIP 侧车"
kill_port 8101 "BGE 嵌入"
kill_port 8100 "VLM llama-server"

# 残留进程
kill_by_name "llama-server.*8102" "旧 BGE llama-server (:8102)" || true
kill_by_name "text_embedder_sidecar" "text_embedder_sidecar" || true
kill_by_name "legacy_clip_sidecar" "legacy_clip_sidecar" || true
kill_by_name "benchmark_orchestrator" "PhotoBench orchestrator" || true
kill_by_name "uvicorn.*sentrix.main" "2.0 uvicorn" || true
kill_by_name "uvicorn.*photobench.main" "PhotoBench uvicorn" || true

echo ""
echo "✅ 全部服务已停止"

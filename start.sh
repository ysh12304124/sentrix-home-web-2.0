#!/usr/bin/env bash
# Sentrix Home Web 2.0 一键启动 — Orin NX (CUDA)
#
# 用法:
#   ./start.sh              默认启动全部服务（server profile）
#   ./start.sh -h           帮助
#
# 启动内容:
#   1. 清理旧进程
#   2. Chinese-CLIP 视觉嵌入侧车（端口 8103，Python + CUDA）— 小模型，先启动
#   3. BGE-M3 文本嵌入（llama.cpp，端口 8101）
#   4. llama-server VLM（qwen35-q4km + mmproj-f16，CUDA 加速，端口 8100）— 大模型，后启动
#   5. Sentrix 2.0 API（端口 8191，SENTRIX_DEPLOYMENT_PROFILE=server）
#   6. PhotoBench 2.0（端口 8772）
#
# Orin NX 配置：
#   - embedding_scheme=legacy (bge-m3 + chinese-clip)
#   - face=CUDA (CUDA available)
#   - agent_wall_time=180s, chat_timeout=120s
#   - QA 并发=2
#   - VLM: --cache-ram 512 --ctx-checkpoints 1 (限制显存占用)
#   - BGE: --cache-ram 128 --ctx-checkpoints 0
#   - SENTRIX_IMPORT_WORKERS=4 (CLIP/人脸检测并行)
#   - SENTRIX_ALBUM_WORKERS=1 (VLM 单并发)
#   - SENTRIX_VLM_TIMEOUT_S=300 (5分钟，防止排队超时)
#
# 模型权重路径（~/models/）：
#   VLM:      ~/models/Qwen3.5/qwen3.5-4b-iq3xxs.gguf + ~/models/vlm/qwen35-mmproj-f16.gguf
#   CLIP:     ~/models/clip/clip_cn_vit-l-14.pt
#   BGE-M3:   ~/models/bge-m3/bge-m3-q8_0.gguf
#   Face:     ~/.insightface/models/buffalo_l/
#   VLM 其他权重: ~/models/vlm/ (备用模型)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

# ---------- 路径常量 ----------
VENV_PY="$root/.venv/bin/python"
LLAMA_BIN="${LLAMA_BIN:-/home/nx/llama.cpp/build/bin/llama-server}"

# 模型权重（~/models 下的标准布局）
VLM_MODEL="${HOME}/models/Qwen3.5/qwen3.5-4b-iq3xxs.gguf"
VLM_MMPROJ="${HOME}/models/vlm/qwen35-mmproj-f16.gguf"
BGE_MODEL="${HOME}/models/bge-m3/bge-m3-q8_0.gguf"
CLIP_CHECKPOINT="${HOME}/models/clip/clip_cn_vit-l-14.pt"

# 端口
VLM_PORT=8100
BGE_PORT=8101
CLIP_PORT=8103
API_PORT=8191
PB_PORT=8772

# NVIDIA 捆绑 CUDA 库（cusparselt / cudnn / cublas 等）——
# onnxruntime-gpu 与 chinese-clip 侧车在 dlopen 时需要这些 .so，
# 不设 LD_LIBRARY_PATH 会静默退化成 CPU 或启动失败。
# **不能全局 export**：llama-server 是用系统 CUDA 编译的，加载 venv 里的 pip cublas
# 会导致 cublasCreate_v2 失败（版本不匹配）。只给需要的进程单独设置。
_NVIDIA_LD_PATHS=""
for _d in "$root"/.venv/lib/python3.11/site-packages/nvidia/*/lib; do
  [ -d "$_d" ] && _NVIDIA_LD_PATHS="$_d:${_NVIDIA_LD_PATHS:-}"
done
_NVIDIA_LD_PATHS="${_NVIDIA_LD_PATHS%:}"  # 去末尾冒号

# ---------- 参数解析 ----------
case "${1:-}" in
  ""|-server|--server) ;;
  -h|--help)
    sed -n '/^# Sentrix Home Web 2.0/,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "未知参数: $1" >&2; exit 1 ;;
esac

# ---------- 辅助函数 ----------
wait_for_http() {
  local url="$1" name="$2" max_wait="${3:-60}"
  local elapsed=0
  while (( elapsed < max_wait )); do
    if curl -s --max-time 2 "$url" >/dev/null 2>&1; then
      echo "  ✓ $name HTTP 就绪"
      return 0
    fi
    sleep 2; elapsed=$((elapsed + 2))
  done
  echo "  ⚠️  $name ${max_wait}s 内 HTTP 未就绪（继续启动）"
  return 1
}

# 等待 llama-server 模型加载完成（/health 返回 {"status": "ok"}）
wait_for_llama_model() {
  local port="$1" name="$2" max_wait="${3:-120}"
  local elapsed=0
  echo "  等待 $name 模型加载..."
  while (( elapsed < max_wait )); do
    local response
    response=$(curl -s --max-time 2 "http://127.0.0.1:${port}/health" 2>/dev/null || echo "")
    if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      echo "  ✓ $name 模型加载完成 (${elapsed}s)"
      return 0
    fi
    sleep 2; elapsed=$((elapsed + 2))
  done
  echo "  ⚠️  $name 模型 ${max_wait}s 内未加载完成（继续启动）"
  return 1
}

# 等待 CLIP sidecar 模型加载完成
wait_for_clip_model() {
  local port="$1" max_wait="${2:-60}"
  local elapsed=0
  echo "  等待 Chinese-CLIP 模型加载..."
  while (( elapsed < max_wait )); do
    local response
    response=$(curl -s --max-time 2 "http://127.0.0.1:${port}/health" 2>/dev/null || echo "")
    if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      echo "  ✓ Chinese-CLIP 模型加载完成 (${elapsed}s)"
      return 0
    fi
    if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"error"'; then
      echo "  ✗ Chinese-CLIP 模型加载失败"
      local err_msg
      err_msg=$(echo "$response" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('error','unknown')[:200])" 2>/dev/null || echo "unknown")
      echo "    错误: $err_msg"
      return 1
    fi
    sleep 2; elapsed=$((elapsed + 2))
  done
  echo "  ⚠️  Chinese-CLIP ${max_wait}s 内未就绪（继续启动）"
  return 1
}

# 杀死端口对应的进程（包括子进程，带 SIGKILL 回退）
WAIT_TIMEOUT=10
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
      if kill -0 "$pid" 2>/dev/null; then still_running=true; break; fi
    done
    if ! $still_running; then
      echo "  ✓ 停止 $name (:$port)"
      return 0
    fi
    sleep 1; elapsed=$((elapsed + 1))
  done
  echo "  ⚠️  $name (:$port) 未在 ${WAIT_TIMEOUT}s 内停止，发送 SIGKILL..."
  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
    pkill -9 -P "$pid" 2>/dev/null || true
  done
  sleep 1
  echo "  ✓ 强制停止 $name (:$port)"
}

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
      if kill -0 "$pid" 2>/dev/null; then still_running=true; break; fi
    done
    if ! $still_running; then
      echo "  ✓ 停止 $name"
      return 0
    fi
    sleep 1; elapsed=$((elapsed + 1))
  done
  echo "  ⚠️  $name 未在 ${WAIT_TIMEOUT}s 内停止，发送 SIGKILL..."
  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
    pkill -9 -P "$pid" 2>/dev/null || true
  done
  sleep 1
  echo "  ✓ 强制停止 $name"
}

# ═══════════════════════════════════════════════════════════
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Sentrix Home Web 2.0 — Orin NX 启动           ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ---------- 1. 清理旧进程 ----------
echo "[1/6] 清理旧进程..."

kill_port $API_PORT "Sentrix 2.0 API"
kill_port $PB_PORT  "PhotoBench 2.0"
kill_port $CLIP_PORT "Chinese-CLIP 侧车"
kill_port $BGE_PORT  "BGE 嵌入"
kill_port $VLM_PORT  "VLM llama-server"

# 残留进程
kill_by_name "text_embedder_sidecar" "旧 text_embedder_sidecar" || true
kill_by_name "legacy_clip_sidecar" "旧 legacy_clip_sidecar" || true
kill_by_name "benchmark_orchestrator" "旧 PhotoBench orchestrator" || true
kill_by_name "uvicorn.*sentrix.main" "旧 2.0 uvicorn" || true
kill_by_name "uvicorn.*photobench.main" "旧 PhotoBench uvicorn" || true

sleep 2

# ---------- 2. 启动 Chinese-CLIP 视觉嵌入侧车（端口 8103，Python + CUDA）----------
echo ""
echo "[2/6] 启动 Chinese-CLIP 视觉嵌入侧车 (端口 $CLIP_PORT, Python + CUDA) ..."

if [[ ! -f "$CLIP_CHECKPOINT" ]]; then
  echo "  ❌ CLIP 权重不存在: $CLIP_CHECKPOINT" >&2
  exit 1
fi

# Chinese-CLIP 使用 ONNX 后端（内存占用更小，约 500MB vs PyTorch 1.7GB）
# .venv 环境有 onnxruntime-gpu，使用 ONNX 后端避免 PyTorch 的 libcusparseLt 依赖问题
CHINESE_CLIP_CHECKPOINT="$CLIP_CHECKPOINT" \
SENTRIX_CLIP_BACKEND=onnx \
SENTRIX_CLIP_IMG_ONNX="/home/nx/models/clip/clip_cn_vit-l-14.img.fp16.onnx" \
SENTRIX_CLIP_TXT_ONNX="/home/nx/models/clip/clip_cn_vit-l-14.txt.fp16.onnx" \
LD_LIBRARY_PATH="/usr/local/cuda/lib64:/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
nohup "$VENV_PY" "$root/scripts/maintenance/legacy_clip_sidecar.py" \
  --port "$CLIP_PORT" \
  > /tmp/clip-sidecar-2.0.log 2>&1 &
echo "  PID: $! (Python, ONNX+CUDA, .venv 环境)"

wait_for_http "http://127.0.0.1:${CLIP_PORT}/health" "Chinese-CLIP HTTP" 30 || true
wait_for_clip_model "$CLIP_PORT" 60 || true

# ---------- 3. 启动 BGE-M3 文本嵌入（llama.cpp，端口 8101）----------
echo ""
echo "[3/6] 启动 BGE-M3 文本嵌入 (llama.cpp, 端口 $BGE_PORT) ..."

if [[ ! -f "$BGE_MODEL" ]]; then
  echo "  ❌ BGE 模型文件不存在: $BGE_MODEL" >&2
  exit 1
fi

# BGE 使用 llama.cpp，不需要设置 LD_LIBRARY_PATH（使用系统 CUDA）
# 关键参数：
#   --ctx-size 8192      : 上下文大小
#   --parallel 1         : 单并发
#   --batch-size 512     : 批处理大小
#   --ubatch-size 512    : 微批处理大小
#   --cache-ram 128      : 限制 KV cache RAM 占用 128MB
#   --ctx-checkpoints 0  : 嵌入模式不需要上下文检查点
nohup "$LLAMA_BIN" \
  --embedding \
  -m "$BGE_MODEL" \
  --host 127.0.0.1 --port "$BGE_PORT" \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --parallel 1 \
  --batch-size 512 \
  --ubatch-size 512 \
  --cache-ram 128 \
  --ctx-checkpoints 0 \
  > /tmp/bge-embedding-2.0.log 2>&1 &
echo "  PID: $! (llama.cpp embedding mode)"

wait_for_http "http://127.0.0.1:${BGE_PORT}/health" "BGE HTTP" 30 || true
wait_for_llama_model "$BGE_PORT" "BGE-M3" 60 || true

# ---------- 4. 启动 VLM（llama-server）----------
echo ""
echo "[4/6] 启动 VLM llama-server (qwen35-q4km, CUDA 加速, 端口 $VLM_PORT) ..."

if [[ ! -f "$VLM_MODEL" ]]; then
  echo "  ❌ 模型文件不存在: $VLM_MODEL" >&2
  exit 1
fi

# VLM 使用 llama.cpp，不需要设置 LD_LIBRARY_PATH（使用系统 CUDA）
# 关键参数：
#   --cache-ram 512      : 限制 KV cache RAM 占用 512MB（默认值更大）
#   --ctx-checkpoints 1  : 启用上下文检查点，减少显存峰值
#   --slot-prompt-similarity 0 : 禁用 slot prompt 相似度检查（单并发不需要）
LLAMA_ARGS=(-m "$VLM_MODEL"
            --host 127.0.0.1 --port "$VLM_PORT"
            --ctx-size 8192 --parallel 2
            --n-gpu-layers 999 --threads 4
            --reasoning off
            --flash-attn on
            --cache-type-k q8_0 --cache-type-v q8_0
            --metrics
            --slot-prompt-similarity 0
            --cache-ram 512
            --ctx-checkpoints 1)
if [[ -f "$VLM_MMPROJ" ]]; then
  LLAMA_ARGS+=(--mmproj "$VLM_MMPROJ")
fi

nohup "$LLAMA_BIN" "${LLAMA_ARGS[@]}" > /tmp/llama-server-2.0.log 2>&1 &
echo "  PID: $!"

wait_for_http "http://127.0.0.1:${VLM_PORT}/health" "VLM HTTP" 60 || true
wait_for_llama_model "$VLM_PORT" "VLM" 120 || true

# ---------- 5. 启动 Sentrix 2.0 API（端口 8191）----------
echo ""
echo "[5/6] 启动 Sentrix 2.0 API (端口 $API_PORT, profile=server) ..."

mkdir -p "$root/data/media" "$root/data/previews" "$root/data/geo"

export SENTRIX_DEPLOYMENT_PROFILE=server
export SENTRIX_EMBEDDING_SCHEME=legacy
# 限制导入并发为 1，减少内存峰值（VLM 和 CLIP 都是 --parallel 1）
# SENTRIX_IMPORT_WORKERS=4  : 控制资产导入并发（CLIP/人脸检测可并行）
# SENTRIX_ALBUM_WORKERS=1   : 控制相册流水线处理并发（VLM 调用也受此影响）
# VLM 是单并发 (--parallel 1)，多个 worker 会排队，增加超时到 5 分钟防止超时
export SENTRIX_IMPORT_WORKERS=4
export SENTRIX_ALBUM_WORKERS=4
# 启用体验模式：PhotoBench 需要 TurnExecutor 来执行 QA turns
# 这会禁用部分直接访问的 API（导入、搜索等），但 PhotoBench 通过内部调用绕过
#export SENTRIX_EXPERIENCE_MODE=1
export SENTRIX_VLM_BASE_URL="http://127.0.0.1:${VLM_PORT}/v1"
export SENTRIX_TEXT_EMBEDDER_URL="http://127.0.0.1:${BGE_PORT}"
export SENTRIX_LEGACY_CLIP_URL="http://127.0.0.1:${CLIP_PORT}"
export SENTRIX_VLM_CONTEXT_TOKENS=6144
# VLM 超时设为 5 分钟（300s），因为 VLM 单并发时多个 worker 会排队
export SENTRIX_VLM_TIMEOUT_S=300
# 人脸检测使用 CPU（显存不足时使用 CPU 模式）
export FACE_PROVIDERS="${FACE_PROVIDERS:-CPUExecutionProvider}"
export RETINAFACE_PROVIDERS="${RETINAFACE_PROVIDERS:-CPUExecutionProvider}"
# 设置 NVIDIA CUDA 库路径（onnxruntime-gpu 需要）
export LD_LIBRARY_PATH="$_NVIDIA_LD_PATHS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

nohup "$VENV_PY" -m uvicorn --factory sentrix.main:create_app \
  --host 0.0.0.0 --port "$API_PORT" \
  --app-dir apps/api/src \
  > /tmp/sentrix-api-2.0.log 2>&1 &
echo "  PID: $!"

wait_for_http "http://127.0.0.1:${API_PORT}/api/v1/health" "Sentrix 2.0 API" 30 || true

# ---------- 6. 启动 PhotoBench 2.0（端口 8772）----------
echo ""
echo "[6/6] 启动 PhotoBench 2.0 (端口 $PB_PORT, profile=server) ..."

export PHOTOBENCH_SENTRIX_SERVICE_UNIT=""
export PHOTOBENCH_QA_CONCURRENCY="${PHOTOBENCH_QA_CONCURRENCY:-2}"
# 导入超时：server 默认 172800s (48小时)
export PHOTOBENCH_IMPORT_TIMEOUT="${PHOTOBENCH_IMPORT_TIMEOUT:-172800}"
echo "  导入超时: ${PHOTOBENCH_IMPORT_TIMEOUT}s ($(( PHOTOBENCH_IMPORT_TIMEOUT / 3600 ))小时)"

nohup "$VENV_PY" -m uvicorn --factory photobench.main:create_app \
  --host 0.0.0.0 --port "$PB_PORT" \
  --app-dir apps/photobench/src \
  > /tmp/photobench-2.0.log 2>&1 &
echo "  PID: $!"

wait_for_http "http://127.0.0.1:${PB_PORT}/api/v1/runs" "PhotoBench 2.0" 15 || true

# ---------- 汇总 ----------
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║              启动结果 — Orin NX                 ║"
echo "╠══════════════════════════════════════════════════╣"

check_health_status() {
  local port="$1" path="${2:-/health}"
  local response
  response=$(curl -s --max-time 2 "http://127.0.0.1:${port}${path}" 2>/dev/null || echo "")
  if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    echo "✓"
  elif echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"error"'; then
    echo "✗"
  elif echo "$response" | grep -q '"status"'; then
    echo "~"
  else
    echo "✗"
  fi
}

vlm_ok=$(check_health_status "$VLM_PORT" "/health")
bge_ok=$(check_health_status "$BGE_PORT" "/health")
clip_ok=$(check_health_status "$CLIP_PORT" "/health")
api_health=$(curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/api/v1/health" 2>/dev/null || echo "{}")
api_status=$(echo "$api_health" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
api_embeddings=$(echo "$api_health" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('embeddings','?'))" 2>/dev/null || echo "?")
api_embeddings_error=$(echo "$api_health" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('embeddings_error','')[:80])" 2>/dev/null || echo "")
pb_ok=$(curl -s --max-time 2 -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PB_PORT}/api/v1/runs" 2>/dev/null)

echo "║  VLM llama-server  :$VLM_PORT   $vlm_ok                          ║"
echo "║  BGE-M3 文本嵌入   :$BGE_PORT   $bge_ok                          ║"
echo "║  Chinese-CLIP 视觉 :$CLIP_PORT   $clip_ok                          ║"
echo "║  Sentrix 2.0 API   :$API_PORT   $api_status (embeddings=$api_embeddings) ║"
echo "║  PhotoBench 2.0    :$PB_PORT   HTTP $pb_ok                          ║"
echo "╠══════════════════════════════════════════════════╣"

# 局域网 IP
REAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
LAN_IP="${REAL_IP:-127.0.0.1}"

has_critical_failure=false
if [[ "$vlm_ok" != "✓" ]]; then
  echo "║  ❌ VLM 未就绪 — VLM 推理将不可用                    ║"
  has_critical_failure=true
fi
if [[ "$clip_ok" != "✓" ]]; then
  echo "║  ❌ CLIP 未就绪 — 视觉嵌入不可用                      ║"
  has_critical_failure=true
fi
if [[ "$bge_ok" != "✓" ]]; then
  echo "║  ❌ BGE 未就绪 — 文本嵌入不可用，语义搜索将失效        ║"
  has_critical_failure=true
fi
if [[ "$api_embeddings" != "ok" ]]; then
  echo "║  ❌ API 嵌入功能不可用 — 视觉/文本向量将无法写入       ║"
  if [[ -n "$api_embeddings_error" ]]; then
    echo "║     原因: $api_embeddings_error"
  fi
  has_critical_failure=true
fi

if [[ "$api_status" == "ok" ]] && ! $has_critical_failure; then
  echo "║                                                  ║"
  echo "║  ✅ 启动成功！所有服务就绪                        ║"
  echo "║                                                  ║"
  echo "║  主界面:   http://${LAN_IP}:${API_PORT}             ║"
  echo "║  PhotoBench: http://${LAN_IP}:${PB_PORT}            ║"
  echo "║                                                  ║"
  echo "║  Profile:  server (legacy+CUDA-face+180s wall)   ║"
  echo "║  VLM 模型: qwen35-q4km (CUDA)                   ║"
  echo "║                                                  ║"
elif $has_critical_failure; then
  echo "║                                                  ║"
  echo "║  ❌ 关键服务未就绪，请修复后重启：                 ║"
  echo "║    VLM        : /tmp/llama-server-2.0.log        ║"
  echo "║    BGE 嵌入   : /tmp/bge-embedding-2.0.log       ║"
  echo "║    CLIP 侧车  : /tmp/clip-sidecar-2.0.log        ║"
  echo "║    API        : /tmp/sentrix-api-2.0.log          ║"
  echo "║                                                  ║"
  echo "╚══════════════════════════════════════════════════╝"
  echo ""
  echo "停止全部: ./stop.sh"
  echo ""
  exit 1
else
  echo "║                                                  ║"
  echo "║  ⚠️  部分服务未就绪，请检查日志：                  ║"
  echo "║    VLM        : /tmp/llama-server-2.0.log        ║"
  echo "║    BGE 嵌入   : /tmp/bge-embedding-2.0.log       ║"
  echo "║    CLIP 侧车  : /tmp/clip-sidecar-2.0.log        ║"
  echo "║    API        : /tmp/sentrix-api-2.0.log          ║"
  echo "║    PhotoBench : /tmp/photobench-2.0.log           ║"
  echo "║                                                  ║"
fi
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "停止全部: ./stop.sh"
echo ""

#!/usr/bin/env bash
set -euo pipefail
export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONIOENCODING=utf-8
export CUDA_VISIBLE_DEVICES=GPU-4ef9200c-d8f3-638a-3eeb-26fe4fa3d7b0
export LD_LIBRARY_PATH=/home/realmagic/services/llama-qwen35/lib:/home/realmagic/services/llama-qwen35/bin
exec /home/realmagic/services/llama-qwen35/bin/llama-server \
  --model /home/realmagic/models/llama/qwen3.5/qwen35-q4km.gguf \
  --mmproj /home/realmagic/models/llama/qwen3.5/qwen35-mmproj-f16.gguf \
  --host 127.0.0.1 --port 8100 --alias qwen3.5-4b-q4_k_m \
  --n-gpu-layers 999 --ctx-size 65536 --parallel 4 \
  --batch-size 512 --ubatch-size 256 --threads 8 --threads-batch 8 \
  --flash-attn on --cache-type-k f16 --cache-type-v f16 \
  --fit off --cache-ram 0 --jinja --reasoning off --metrics --slots

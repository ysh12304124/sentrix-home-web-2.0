# Sentrix Home Web 2.0

家庭相册记忆系统：照片/视频导入 → 记忆构建 → 自然语言检索与问答 → PhotoBench 评测。

---

## ⚠️ 修改代码前必读（强制）

**任何人在修改本项目代码之前，必须先完整阅读：**

### [`docs/architecture/module-interfaces.md`](docs/architecture/module-interfaces.md)

这份文档不是“参考资料”，是**强制规则**。它规定了：

- 四层架构与 11 条 import 契约；
- 所有可替换端口（模型、嵌入、OCR、地理、存储、评测）的接口与失败语义；
- **所有代码改动**（不只是换模块）必须遵守的十条硬规则；
- 改动流程、验收标准（Definition of Done）、PR 检查清单与反模式；
- 153（WeMM）与 130（legacy）两套部署的差异和迁移检查项。

**没有读完就开始改，最常见的后果**：反向依赖、静默降级、外部契约被改坏、
换了嵌入却没重建索引、PhotoBench 迁移后请求仍发往旧机器。

如果你只想快速判断自己的改动是否合规，先看文档的 **§0.5 十条硬规则** 与 **§5 验收标准**。

---

## 快速开始

### 153（默认，WeMM）

```bash
make dev            # 主 API 8191
make pb-dev         # PhotoBench 8772
```

或单独启动：

```bash
./scripts/deploy/start_api.sh
./scripts/deploy/start_photobench.sh
```

### Mac mini / 16 GB 机器（legacy）

```bash
SENTRIX_DEPLOYMENT_PROFILE=mac ./scripts/deploy/start_legacy_mac.sh
./scripts/deploy/start_photobench_mac.sh  # PhotoBench 8772；保留 Mac legacy 重启环境
```

详见 [`docs/runbooks/mac-mini-legacy.md`](docs/runbooks/mac-mini-legacy.md)。

### 本地前端开发

确认 API 8191 已启动后，在另一个终端运行：

```bash
make web-dev        # http://localhost:4274/
```

该目标默认代理到 `http://127.0.0.1:8191`，并启用已有的本地用户模式，无需运行 8210
控制面；账号登录、管理台与反馈功能需要控制面。完整 Mac 启动脚本已启动 API 时，
无需再运行 `make dev`。

开发控制面功能时，先启动控制服务，再显式选择代理目标：

```bash
SENTRIX_PREVIEW_TARGET=http://127.0.0.1:8210 make web-dev
```

指定该目标后默认使用控制面认证；`VITE_LEGACY_LOCAL_AUTH` 仍可显式覆盖。
这些默认值仅作用于 `make web-dev`，不改变直接运行 npm 或生产构建的行为。

### 部署 profile（同一份代码，server / mac）

平台差异**不写成代码里的 `if macOS`**。用 `SENTRIX_DEPLOYMENT_PROFILE=server|mac`
提供一组默认值；**显式 env / run payload 永远优先**。

| 默认项 | `server`（153） | `mac`（158） |
|---|---|---|
| `embedding_scheme` | `wemm` | `legacy` |
| `agent_wall_time_s` | 180 | 600 |
| `chat_timeout_s` | 120 | 180 |
| QA 并发 | `None`（按端点槽位探测） | 2 |
| `import_timeout` | 7200 | 86400 |
| `FACE_PROVIDERS` / `RETINAFACE_PROVIDERS` | CUDA,CPU | CPU |

显式覆盖示例：

```bash
# 一台 Mac 仍想用 WeMM
SENTRIX_DEPLOYMENT_PROFILE=mac SENTRIX_EMBEDDING_SCHEME=wemm ...
# 临时把 Mac 的 QA 并发提到 4
PHOTOBENCH_QA_CONCURRENCY=4 ...
# 单次 run 覆盖导入超时
{"import_timeout": 7200}
```

**profile 不负责的部署项（仍需显式设置）：**

- 数据/结果/配置/前端产物根：`SENTRIX_DATA_DIR`、`PHOTOBENCH_{DATA,RESULTS,CONFIG,WEB_DIST}_ROOT`
- 模型与侧车地址：`SENTRIX_VLM_BASE_URL`、`SENTRIX_WEMM_URL`、
  `SENTRIX_TEXT_EMBEDDER_URL`、`SENTRIX_LEGACY_CLIP_URL`、`SENTRIX_RERANKER_URL`
- llama-server 的 `--ctx-size/--parallel`（决定每个槽的上下文窗口）
- judge 端点/模型/密钥（`judge_providers.json` 或 run payload）
- 数据集根（158 的 `PHOTOBENCH_DATA_ROOT` 指向 1.0 的 `services/photobench/data`）

153 不设 profile（默认 `server`）；158 只需在启动时设
`SENTRIX_DEPLOYMENT_PROFILE=mac`，再补齐上面这些部署项。

### llama.cpp 启动参数（缓存/端侧速度已验证）

> 2026-11-12/13 的 487 题实测配置；模型路径按部署位置替换。
> 这组参数只影响缓存复用与 prefill，不改变模型权重或答案本身。

**153（server，RTX 3090 24GB）**

```bash
/home/asus/llama.cpp/build/bin/llama-server \
  -m /home/asus/models/llama/qwen3.5/qwen35-q4km.gguf \
  --mmproj /home/asus/models/llama/qwen3.5/qwen35-mmproj-f16.gguf \
  --alias qwen35-q4km --host 0.0.0.0 --port 8100 \
  --n-gpu-layers 999 --ctx-size 65536 --parallel 4 \
  --metrics --reasoning off \
  --cache-ram 12288 --ctx-checkpoints 8 --checkpoint-min-step 256 \
  --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 \
  --ubatch-size 2048
```

**158 Mac mini（16GB，单用户）**

```bash
/opt/homebrew/bin/llama-server \
  -m /Users/saas/GitHub/Sentrix-Home-Web/models/llama/qwen35-q4km.gguf \
  --mmproj /Users/saas/GitHub/Sentrix-Home-Web/models/llama/qwen35-mmproj-f16.gguf \
  --alias qwen35-q4km --host 0.0.0.0 --port 8100 \
  --n-gpu-layers 999 --ctx-size 65536 --parallel 4 \
  --metrics --reasoning off \
  --cache-ram 2048 --ctx-checkpoints 8 --checkpoint-min-step 256 \
  --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0
# --ubatch-size 2048 在 Metal 上复测后再加
```

参数含义：

- `--cache-ram`（MiB，默认 8192；0 关闭，-1 不限）：主机内存里的 prompt 缓存总预算，
  保存槽位状态（KV、循环状态、checkpoint）供后续请求恢复前缀。153 用 12288；
  Mac 16GB 用 2048，避免 OOM。
- `--ctx-checkpoints`（每槽个数，默认 32）：Qwen3.5 是混合线性注意力，循环状态不能
  任意截断，回到较早位置必须有 checkpoint；实测 8 已足够。
- `--checkpoint-min-step`（token，默认 8192）：checkpoint 的最小间隔；调到 256 才能
  让 1–3k token 的短对话在末尾附近找到可恢复点。
- `--ctx-size/--parallel`：`--ctx-size` 是总窗口，`--parallel 4` 时每槽 16384 token。
- `--ubatch-size 2048`：只影响 prefill 的物理批大小；153 实测冷 prefill +4.7%，
  decode 与缓存命中不变。
- `--flash-attn on`、`--cache-type-k/v q8_0`：KV 用 8-bit 量化，显存约为 f16 的一半，
  实测对 decode/质量影响在噪声内。
- API 侧建议 `SENTRIX_VLM_SLOTS=1`（不 pin，交给服务端 LCP）；只有需要确定性槽位
  隔离时才设为与 `--parallel` 一致。

---

## 目录

```
apps/api/          主产品（sentrix.*）：api → application → domain，infrastructure 实现端口
apps/web/          主前端（8191 挂载）
apps/photobench/   独立评测包（photobench.*），只经 /api/v1 HTTP 访问主产品
docs/architecture/ 设计、模块图、**强制规则与端口文档**
docs/refactor/     重构计划、裁决、能力登记、偏离记录
docs/runbooks/     部署与运维手册
scripts/           开发、部署、维护脚本
```

PhotoBench资源采集工具的固定版本、平台来源及安装核对见[资源采集工具](docs/runbooks/telemetry-tools.md)；Python精确依赖声明在`apps/photobench/pyproject.toml`，原生工具基线在`apps/photobench/telemetry-tools.lock.json`。

记忆构建沿用 2.0 原有资产/观察/事件/实体/事实/向量流水线，不引入旧版记忆生成器。可插拔的 `BuildMemoryGraph` 从这些标准端口构建事件、上下文、同视频关键帧时序、对象/关系枢纽、互选视觉近邻和 `rules_vlm` 因果边；`RetrieveGraph` 按问题类型有界拓展，原回答提示词和生成器不变。图效果的参考边从节点来源字段重建，不能把构图边自身当真值；详见 `docs/refactor/hpq-causal-deviations.md`。当前观察没有 bbox，因果规则仍使用 hpq 的 label-only 回退。

---

## 改动前检查（最低要求）

```bash
make lint format-check typecheck arch photobench-check check-docs coverage
# 或一次跑完
make ci
```

新增/移动 Python 文件必须同步 `docs/architecture/module-map.md`；新增能力必须同步
`docs/refactor/capability-map.jsonl`；偏离 1.0 行为必须写 `*-deviations.md`。

---

## 评测

PhotoBench 运行结果在 `apps/photobench/results/<run_id>/`，页面在 8772。
QA 数据集在 `apps/photobench/datasets/`。

本机双场景（主相册手动上传/AI 记忆对话 + `album3-max-video10` 487QA）启动与验收见
[`local-mac-upload-and-487qa.md`](docs/runbooks/local-mac-upload-and-487qa.md)。该工作副本使用
`scripts/deploy/start_local_two_workflows_mac.sh`，评测素材目录和结果目录由本机忽略文件
`data/local-mac.env` 指定，不会写入 Git。

---

## 许可与数据

数据集、模型权重、GeoNames dump 不进版本控制；部署方按 `docs/runbooks/` 放置。

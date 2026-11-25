# Sentrix Home Web 2.0

家庭相册记忆系统：照片/视频导入 → 记忆构建 → 自然语言检索与问答 → PhotoBench 评测。

同一实例中，相同原始媒体在不同相册间共享一份文件内容，相册记录和记忆独立。
反复测评导入会复用原件；已有重复副本的整理方式见[共享媒体存储](docs/runbooks/shared-media-storage.md)。

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

### Windows / Conda 本地开发

使用 [`environment.windows.yml`](environment.windows.yml) 创建独立的 Python 3.11、
Node.js 22 和 NVIDIA 开发环境。安装顺序、PowerShell 激活、平台限制与验证命令见
[`Windows Conda 环境`](docs/runbooks/windows-conda.md)。

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
make web-dev        # https://localhost:4274/
```

“问一句”的语音输入首次需安装更新后的 Python 依赖，并通过
`.venv/bin/python scripts/deploy/prepare_sensevoice.py` 提前准备本地权重。
SenseVoice-Small / sherpa-onnx 由独立共享服务启动加载，同主机用户 API 经 HTTP 复用。
通过 `scripts/deploy/start_local_models_mac.sh` 随其他模型统一启动并检查健康。
单独部署该模型使用 `scripts/deploy/start_sensevoice.sh`；开发前端运行 `make web-dev`。
浏览器录音发送到本机后端，音频不落盘、不使用云端 ASR。
资源准备和 HTTPS 要求见[语音输入运行手册](docs/runbooks/assistant-speech.md)。

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

### 模型与测评配置

拉取代码后，需在部署机器上补齐实际配置。仓库保留 `.example` 模板；本机配置和密钥单独保存。
模型接入方式由 `SENTRIX_LLM_PROFILE=local|cloud` 选择，与上面的 `server|mac` 部署形态分别设置。

| 配置 | 作用 | 生效方式 |
|---|---|---|
| 产品环境变量 | 模型接入方式、端点、推理预算与上下文 | 主 API 启动时读取 |
| `SENTRIX_LLM_CONFIG` 指定的 JSON | 按 `chat` / `vision` 角色设置模型与请求参数 | 仅 `SENTRIX_LLM_PROFILE=cloud` 时读取 |
| `apps/photobench/config/runtime_connection.json` | PhotoBench 连接哪个产品、模型服务及嵌入服务 | 测评服务启动时读取；也可在页面保存连接设置 |
| `apps/photobench/config/judge_providers.json` | 测评评分模型，独立于被测模型 | 从 `judge_providers.example.json` 复制后配置 |
| `apps/photobench/config/vllm_targets.json` | 可选模型管理服务的目标列表 | 从 `vllm_targets.example.json` 复制后配置；已有模板可提供默认值 |

下文将产品配置放在 `data/product.env.local`，角色 JSON 放在 `data/llm-profile.json`；
`data/` 已被 Git 忽略。PhotoBench 的上述三份实际 JSON 也已忽略。
环境文件需由启动命令显式加载；主 API 不会自动扫描这些文件。

#### 本地 Qwen3.5 Q4

可从已提交的 [Qwen 环境模板](deploy/experience/qwen35-q4km.env.example) 建立本机配置。
以下命令用于首次配置，在仓库根目录执行；已有配置请编辑原文件，保留现有数据与嵌入地址。

```bash
mkdir -p data
cp deploy/experience/qwen35-q4km.env.example data/product.env.local
```

在 `data/product.env.local` 中补上实际端点和每槽上下文，例如：

```dotenv
SENTRIX_VLM_BASE_URL=http://127.0.0.1:8100/v1
SENTRIX_VLM_CONTEXT_TOKENS=16384
SENTRIX_VLM_REASONING_CONTEXT_TOKENS=16384
```

模板已设置 `SENTRIX_LLM_PROFILE=local` 和
`SENTRIX_VLM_GENERATION_PROFILE=qwen35_nonthinking`，并保留四项推理调用上界。
该采样预设向聊天、结构化规划和视觉请求传入 `temperature=0.7`、`seed=42`、
`top_p=0.8`、`top_k=20`、`min_p=0`、`presence_penalty=1.5`，以及
`chat_template_kwargs.enable_thinking=false`。其他模型按各自部署选择参数。

`local` 使用单个端点，模型名默认通过 `/v1/models` 获取；可用 `SENTRIX_VLM_MODEL` 显式指定。
此模式不读取角色 JSON。此前部署目录里的 `q4km.json` 也不会仅因文件存在就生效。
153 的配置核查记录见 [psh 配置对齐](docs/refactor/2026-11-19-psh-formal-config.md)。

#### 按角色配置模型（cloud）

`cloud` 可分别连接聊天和视觉服务，端点需兼容 OpenAI 请求格式。
首次配置先复制 [角色配置模板](deploy/cloud/model-profiles.example.json)：

```bash
cp deploy/cloud/model-profiles.example.json data/llm-profile.json
```

在 `data/product.env.local` 中选择该文件，路径使用本机绝对路径：

```dotenv
SENTRIX_LLM_PROFILE=cloud
SENTRIX_LLM_CONFIG=/absolute/path/to/repo/data/llm-profile.json
```

按实际模型修改 JSON；模板中的模型名和窗口大小是示例值。

| JSON 字段 | 填写内容 |
|---|---|
| `roles.chat` / `roles.vision` 的 `base_url`、`model` | 实际服务地址和模型 ID；建议两角色显式填写，避免继承启动环境中的默认值 |
| 各角色的 `context_tokens`、`max_output_tokens` | 输入与输出共用的上下文窗口、单次输出上限；均为正整数 |
| `api_key_env` | 存放密钥的环境变量名，如 `SENTRIX_CLOUD_API_KEY`；密钥由启动环境提供 |
| `roles.vision.supports_vision` | 设为 `true`，并确认端点支持图片输入 |
| 各角色的 `payload`、`retry`、`timeout_s` | 服务支持的请求字段、重试次数和单次请求超时 |
| `agent` | 普通回合的调用预算，以及历史消息、工具预览和压缩设置 |
| `vision` / `memory` | 图片尺寸和记忆构建参数，可沿用模板后按部署调整 |

模板中 `agent.max_steps`、`max_tool_calls`、`max_search_calls`、`max_page_calls` 和
`max_inspections` 的 `null` 表示普通 cloud 回合不限制对应次数；需要限制的字段应填写正整数。
正式测评可开启下节推理模式，使用共享请求计数，默认执行 `100/600/100/500` 的上界。

#### 启动并核对连接

PhotoBench 首次配置可建立 `apps/photobench/config/runtime_connection.json`，
下面是同机 WeMM 的连接示例，端口按实际部署替换：

```json
{
  "sentrix_url": "http://127.0.0.1:8191",
  "model_base_url": "http://127.0.0.1:8100/v1",
  "vllm_manager_url": "",
  "embedding": {
    "mode": "unified_multimodal",
    "wemm_url": "http://127.0.0.1:8102"
  }
}
```

`PHOTOBENCH_CONFIG_ROOT` 可指定独立配置目录；`BENCH_SENTRIX_URL` 和
`BENCH_VLLM_BASE_URL` 等环境变量优先于 PhotoBench 连接文件。
使用独立目录时，同时放入 Judge 和模型目标配置；可复制仓库中的
`judge_providers.example.json`、`vllm_targets.example.json` 后修改，保留这两个模板文件名也可加载。
`start_api.sh` 也会从这份连接文件导入主模型和嵌入地址，覆盖同名环境变量，需保持两处一致。
cloud 模式的角色端点以 `SENTRIX_LLM_CONFIG` 中的角色配置为准。

连接配置与所选模型配置对齐后，加载产品环境并启动主 API：

```bash
set -a
source data/product.env.local
set +a
./scripts/deploy/start_api.sh
```

systemd 部署应在对应单元的 `EnvironmentFile` 中加载同一份配置，密钥另由受控环境提供。
产品环境变量或角色 JSON 修改后，需在该实例的导入和对话空闲时重启主 API。

启动后通过 `GET /api/v1/diagnostics` 核对产品的 `runtime_configuration`，
通过 PhotoBench 的 `GET /api/v1/config` 核对连接。
正式任务再检查 `runtime_snapshot` 中的实际模型与 `product_settings`；运行任务保留冻结参数，
修改配置文件不会追溯改变历史任务。已有服务由对应服务单元管理，避免重复运行启动脚本。

### 推理模式

推理模式放宽 Agent 的普通回合预算，让它能继续检索和调用工具，适合需要多轮取证的测评。
默认单题最多发起 100 次生成模型请求和 600 次工具请求。
搜索属于工具请求，总额内分为搜索 100 次、其他工具 500 次，两类额度互不借用。

| 产品环境变量 | 默认值 | 计数范围 |
|---|---|---|
| `SENTRIX_REASONING_MAX_MODEL_CALLS` | 100 | 规划、聊天及工具内部生成模型请求，重试也计数 |
| `SENTRIX_REASONING_MAX_TOOL_CALLS` | 600 | 所有获准处理的工具请求，包含搜索 |
| `SENTRIX_REASONING_MAX_SEARCH_CALLS` | 100 | `search_memories` 请求 |
| `SENTRIX_REASONING_MAX_OTHER_TOOL_CALLS` | 500 | 除搜索外的工具请求，包含收尾 |

以上参数在主 API 的启动环境中设置。系统从总预算中预留 2 次模型与工具收尾额度，
收尾仍计入总上限；额度用尽后拒绝超额请求并记录原因。推理回合的墙钟预算至少为 3600 秒，
PhotoBench 默认等待单题 3660 秒，留出结果读取时间。

PhotoBench 启动设置中的“开启推理模式”默认开启；交互对话和单题重测也支持该开关。
产品对话 API 默认关闭，可在创建回合的请求中传 `"reasoning": true` 开启。
关闭后，本地默认回到 8 步、6 次工具往返的普通预算；cloud 普通预算取角色 JSON 的 `agent` 设置。

Agent 推理模式与模型的原生 thinking 开关分别控制。
例如 Qwen 的 `qwen35_nonthinking` 仍可开启 Agent 推理模式，模型请求继续使用 `enable_thinking=false`。

local 普通窗口由 `SENTRIX_VLM_CONTEXT_TOKENS` 设置，cloud 普通窗口取 `roles.chat.context_tokens`。
`SENTRIX_VLM_REASONING_CONTEXT_TOKENS` 可单独设置推理窗口。
本地窗口受端点实际容量和精确计数配置约束；cloud 推理窗口默认 131072 token，需与实际端点能力匹配。
上下文由输入和输出共用，不能把 `max_tokens` 直接填成窗口大小。
详细规则见 [推理模式上下文](docs/refactor/2026-11-18-reasoning-context.md)。

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

资料库的媒体详情页可删除源图片或视频。删除视频时会一并删除派生关键帧及其独占观察、事件、事实、图边和索引；仍有其他媒体证据的共享记忆保留。SQLite 删除与外部清理任务同事务提交，随后清理受控媒体文件和 Qdrant 镜像。数据目录外的导入源文件会保留；外部清理失败会在页面提示，可点“重试外部清理”或调用 `POST /api/v1/assets/deletion-cleanup/retry`。资料库“删除记录”可查看最近 50 条及文件、向量镜像清理状态；`GET /api/v1/assets/deletions?scope_id=<相册ID>&limit=50` 返回同一份记录（最多 200 条）。此操作不可撤销。

---

记忆修正入口位于主前端：事件详情的“修正事件”、媒体详情的“修正媒体记忆”、实体详情的“修正实体”，人物与关系沿用现有编辑入口。保存后同步确认身份快照、图谱和受影响的词法/文本向量索引；“记忆修正记录与同步状态”可查看前后内容、同步错误并重试。新观察/事件/实体修正使用 revision 防止覆盖其他用户的新版本，权威修改和审计任务同事务落库；人物改名先快速返回 pending，再在有界服务线程中完成相同的关联摘要、画像、图和索引同步，页面轮询完成状态并刷新时间线。服务重启或同步失败时任务保留为 pending，可从修正记录重试。其他身份/关系入口仍按既有同步响应执行。修正不会重新抽帧或重新调用观察模型。

## 改动前检查（最低要求）

```bash
make lint format-check typecheck arch photobench-check check-docs coverage
# 或一次跑完
make ci
```

新增/移动 Python 文件必须同步 `docs/architecture/module-map.md`；新增能力必须同步
`docs/refactor/capability-map.jsonl`；偏离 1.0 行为必须写 `*-deviations.md`。

---

## 离线地点解析

部署方可把区域 OSM PBF 编译为 `$SENTRIX_DATA_DIR/geo/osm-places.sqlite`。
产品用 OSM 查行政区、居民点、道路和景点，数据缺席时保留 PyGeoCN 粗行政区。
构建、标注对照和已有相册回填见 [OSM 离线地点解析](docs/runbooks/osm-places.md)。

## 评测

邮件/文档走独立分块文本索引与 `search_text_memories`，原文保留源ID与offset。
邮件候选检索与最终交付（引用）复用原有召回计数与逐题等权汇总，按源邮件去重；
文本工具每次最多返回6封排序后的证据，另展示R@1/5/10。
已有分块向量可复用；FTS5与邮件关联投影自动补齐。没有分块索引的旧数据需先构建；
历史缺取证事实的指标保持未记录。
边界与验证见[独立文本检索说明](docs/refactor/text-retrieval-deviations.md)。

PhotoBench 运行结果在 `apps/photobench/results/<run_id>/`，页面在 8772。
QA 数据集在 `apps/photobench/datasets/`。

本机双场景（主相册手动上传/AI 记忆对话 + `album3-max-video10` 487QA）启动与验收见
[`local-mac-upload-and-487qa.md`](docs/runbooks/local-mac-upload-and-487qa.md)。该工作副本使用
`scripts/deploy/start_local_two_workflows_mac.sh`，评测素材目录和结果目录由本机忽略文件
`data/local-mac.env` 指定，不会写入 Git。

---

## 许可与数据

数据集、模型权重、GeoNames dump 不进版本控制；部署方按 `docs/runbooks/` 放置。

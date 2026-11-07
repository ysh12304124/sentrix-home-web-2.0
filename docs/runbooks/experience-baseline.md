# 体验服务基线盘点（2026-11-02）

本文件是体验服务实施与回滚的对照基线。数据在 2026-11-02 通过只读命令采集；PID 会随重启变化，
表中的**端点、托管者与配置来源**才是长期有效的部分。系统级功能、故障注入与容量压测由用户执行。

## 1. 源码基线

| 位置 | SHA | 状态 |
| --- | --- | --- |
| 设计依赖基线 | `d3485537b02e30e0ee3e7cc6dc4b450c2f38f7db` | 计划固定值 |
| GitHub `origin/main` | `d3485537b02e30e0ee3e7cc6dc4b450c2f38f7db` | `git ls-remote origin refs/heads/main` 一致 |
| 153 `/home/asus/Github/sentrix-home-web2.0` | `d3485537b02e30e0ee3e7cc6dc4b450c2f38f7db` | 工作树干净，`main` 跟踪 `origin/main` |
| 开发分支 | `codex/experience-service` | 从上述基线新建；`main` 不移动 |

**运行进程不等于源码。** 8191 的 `/api/v1/health` 报 `build_revision=development`，
不能据此判断它已加载当前提交。

## 2. 端口、进程与托管者

快照时间：2026-11-02。PSS 来自 `/proc/<pid>/smaps_rollup`，GPU 来自 `nvidia-smi`。

| 端口 | 进程（PID 快照） | 工作目录 / 来源 | 托管者 | 用途 | 资源（快照） |
| --- | --- | --- | --- | --- | --- |
| 8100 | `llama-server` 3362957 | `/home/asus/models/llama/qwen3.5` | **无**（用户单元 `sentrix-photobench-qwen35-lora-q4km.service` 为 inactive；`Restart=on-failure` 未生效） | 共享主模型 | PSS 2.26 GiB；GPU 9478 MiB |
| 8091 | 1.0 API 3134 | `/home/asus/Github/Sentrix-Home-Web` | 系统单元 `sentrix-api.service`（active） | 1.0 产品 API | PSS 1.08 GiB；GPU 332 MiB |
| 4174 | node 3135 | 1.0 静态前端 | 系统单元 `sentrix-web.service`（active） | 1.0 前端 | — |
| 8101 | `text_embedder_sidecar.py` 3143 | `/home/asus/Github/Sentrix-Home-Web`（`.venv-text`） | **无**（子进程，PPID 3134） | BGE-M3 文本嵌入 | PSS 3.92 GiB |
| 8102 | `wemm_embedder_sidecar.py` 3357699 | `/home/asus/Github/Sentrix-Home-Web`（`.venv-wemm`） | **无**（用户单元 `sentrix-wemm-sidecar.service` inactive） | WeMM 统一多模态嵌入 | PSS 1.73 GiB；GPU 5540 MiB |
| 8103 | — | — | — | Chinese-CLIP 视觉嵌入 | **未运行**（本次临时拉起实测后已停止） |
| 8191 | 2.0 API 3002049 | `/home/asus/Github/sentrix-home-web2.0` | **无**（用户单元 `sentrix-api.service` inactive，`NRestarts=92`；实际由 8772 拉起，PPID 3902440） | 2.0 体验 API 候选 | PSS 2.34 GiB |
| 8772 | PhotoBench 3902440 | 同 2.0 仓库 | **无**（用户单元 `sentrix-photobench.service` inactive） | 2.0 评测 | PSS 883 MiB |
| 8192 / 8773 | freeret API / PhotoBench | `/home/asus/Github/sentrix-freeret` | 裸进程 | 实验 | — |
| 8193 / 8194 / 8774 | ds API / API / PhotoBench | `/home/asus/Github/sentrix-home-web2.0-ds` | 裸进程 | 实验 | 8193 PSS 1.48 GiB；8194 PSS 1.29 GiB |
| 8291 / 8872 | xy release API / PhotoBench | `/home/asus/xy/releases/...` | 裸进程 | 历史回归 | 8291 PSS 2.12 GiB |
| 8500 | vLLM Manager API | miniconda `sentrix-vllm` | 用户单元 `sentrix-vllm-api.service`（active） | 模型管理 | — |
| 8771 | 1.0 benchmark orchestrator | `/home/asus/Github/Sentrix-Home-Web/services/photobench` | 裸进程 | 旧评测 | — |
| 11434 | Ollama | — | 系统单元 `ollama.service`（active） | 其他模型 | — |

系统单元 `sentrix-vllm.service` 为 `enabled` 但 `ActiveState=exited`；8100 上实际是 llama.cpp，
不是该单元描述里的 vLLM。

## 3. 主模型与嵌入能力（实测）

### 主模型 8100

- `GET /v1/models` 返回 `qwen35-q4km`；llama.cpp，`n_ctx=131072`，`n_ctx_train=262144`，`n_embd=2560`，
  `n_params≈4.33B`，Q4_K - Medium。
- 启动参数含 `--parallel 2`、`--ctx-size 262144`、`--mmproj qwen35-mmproj-f16.gguf`、
  `--cache-type-k q8_0 --cache-type-v q8_0`、`--reasoning off`。
- `--parallel 2` 是模型槽位的**当前运行事实**；调度器按实测值读取，不写死。

### BGE-M3（8101）

- `GET /health` → `{"status":"ok","model":"BAAI/bge-m3","dimension":1024}`。
- `POST /embed {"texts":["hello"]}` 返回 1024 维向量（已实测，首元素 `-0.59858...`）。
- 由 1.0 的 `text_embedder_sidecar.py` 提供，2.0 只通过 HTTP 使用。

### Chinese-CLIP（8103）

- 默认端口无人监听；权重 `~/.cache/clip/clip_cn_vit-l-14.pt`（1.6 GiB）存在。
- 2026-11-02 用 `scripts/maintenance/legacy_clip_sidecar.py --port 8103` 临时拉起（PyTorch/CUDA）实测：
  - `POST /embed_image {"path": ...}` → 768 维（首元素 `0.01012...`）；
  - `POST /embed_text {"text":"家庭聚餐"}` → 768 维；
  - 侧车 GPU 占用 1134 MiB；实测后已停止。
- **部署前置：** 该侧车当前没有任何常驻托管者，需在体验服务上线前由 systemd 接管
  （或复用其他可靠托管方式），否则 legacy 视觉检索与启动探针不可用。

### 现有 2.0 进程的配置漂移（必须保留证据）

8191 的 `/api/v1/diagnostics`（2026-11-02）：

```json
{"embedding_scheme":"wemm","vlm_base_url":"http://127.0.0.1:8103/v1",
 "wemm_url":"http://127.0.0.1:8102","text_embedder_url":"http://127.0.0.1:8101",
 "legacy_clip_url":"http://127.0.0.1:8103","build_revision":"development"}
```

- 运行的是 `wemm`，而体验发行要求 `legacy`。
- `vlm_base_url` 指向 8103，8103 在 CLIP 侧车停止时既不是 VLM 也不是 OpenAI 兼容端点。
- `/health` 仍返回 `status=ok`，证明单健康灯不能代表问答链路可用。

## 4. 8100 直连消费者清单

| 消费者 | 实际配置来源 | 是否直连 8100 | 结论 |
| --- | --- | --- | --- |
| PhotoBench 8772 | `apps/photobench/config/runtime_connection.json`：`model_base_url=http://127.0.0.1:8100/v1` | 是 | 同机评测；接触发 `_sync_sentrix_endpoint()` |
| 2.0 ds API 8194 | 进程环境缺失记录；实测与 8100 有 ESTAB 连接 | 是 | 实验实例，上线前须核对 |
| xy release API 8291 | `SENTRIX_VLM_BASE_URL=http://127.0.0.1:8100/v1` | 是 | 历史回归实例 |
| xy PhotoBench 8872 | 同上 | 是 | 历史回归实例 |
| 2.0 体验候选 8191 | `SENTRIX_VLM_BASE_URL=http://127.0.0.1:8103/v1` | 否（当前指向死端口） | 体验发行改为经控制入口 |
| freeret 8192 / 8773 | `SENTRIX_VLM_BASE_URL=http://127.0.0.1:8108/v1` | 否 | 8108 未监听，实例当前不可用 |
| 1.0 API 8091 | `backend/model_clients.py` 默认 `http://127.0.0.1:8100/v1` | **未核实** | 运行参数不在进程环境中；需在切换前单独确认 |
| 1.0 benchmark 8771 | 通过 8091 间接使用 | 间接 | 跟随 8091 |

**停点三依据：** 8291、8872、8194 仍是未接入准入的直连旁路。在它们接入控制入口前，
管理面板不得宣称“问答优先级生效”，也不得执行 5 用户容量验收。

PhotoBench 的端点核对路径：`apps/photobench/src/photobench/application/run_execution/service.py`
的 `_sync_sentrix_endpoint()`（约 `:930`）。它调用 `sentrix_switch.ensure_endpoint(model_base_url)`，
再用 `/diagnostics` 的 `runtime_configuration.vlm_base_url` 与期望值逐字比较。
另有一处配置漂移：`runtime_connection.json` 的 `endpoint_model` 为 `qwen35-4b-lora-q4km`，
8100 实际模型 ID 为 `qwen35-q4km`；模型身份核对会暴露此差异。

## 5. 机器资源快照

| 项目 | 数值 |
| --- | --- |
| 物理内存 | 61909 MiB 总量；约 40527 MiB available |
| Swap | 2047 MiB，**已用满**（无剩余） |
| 根盘 | 1.8 TiB，536 GiB 可用（70%） |
| GPU | RTX 3090 24576 MiB；已用约 15.4 GiB；利用率 72–96%；温度 48–63 °C；功率约 320 W |
| GPU 进程 | llama-server 9478 MiB；WeMM 5540 MiB；1.0 API 332 MiB |

Swap 已满意味着任何新增常驻进程都必须先计入内存预算；每用户 API 的 PSS 需按实测
（现有 2.0 API 约 2.3 GiB）估算，不能只看 RSS。

## 6. 分支、发行目录与数据目录命名

| 对象 | 约定 |
| --- | --- |
| 开发分支 | `codex/experience-service` |
| 发行件 | `/opt/sentrix/releases/<40-hex-git-sha>/`，只读，归发布账号所有 |
| 当前版本 | `/opt/sentrix/current` → `releases/<sha>` |
| 控制库与截图 | `/srv/sentrix/control/` |
| 用户数据根 | `/srv/sentrix/users/<stable-account-id>/`，权限 `0700` |
| 内部 API 监听 | 每用户 `127.0.0.1:<独立端口>`，唯一内部令牌 |
| 旧数据 | `/home/asus/Github/sentrix-home-web2.0/data`、1.0 `data/`、PhotoBench 结果均不移动 |

## 7. 回退要点

- 体验服务使用独立端口、独立控制库与全新用户数据根；切换前不修改 8191/8772/8091 的端口与数据目录。
- 8100、8101、8102、8103 当前的实际端点与启动参数已记录在本文件；切换窗口若接管托管，
  必须保留上表参数作为回退配置。
- 旧 8191 由 PhotoBench 拉起是既存风险；体验服务不得复用这一归属，也不得让 PhotoBench
  重启体验用户 API。
- 任何尚未接入控制入口的 8100 消费者（见第 4 节）都是 D7 上线阻断项，保留可回退配置。

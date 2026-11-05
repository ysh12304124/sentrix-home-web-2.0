# Sentrix MLX 并发验收

本实验基于上游 `main@d3485537b02e30e0ee3e7cc6dc4b450c2f38f7db`，迁入 PR #7 的嵌入推理解锁、Qdrant 冷启动保护、冻结库恢复和性能统计。数据库事务及状态锁保留。正式成绩以随报告发布的原始记录哈希和逐轮终态为准；资格测试、取消和失败的初跑不计入通过档。

## 固定环境

- Apple M4，16 GB 统一内存，macOS 26.6；Sentrix、SQLite、Qdrant、BGE-M3、Chinese-CLIP 和 Qwen 全部在同一台 Mac 运行。
- 模型从 ModelScope `lmstudio-community/Qwen3.5-4B-MLX-4bit` 下载。权重 SHA256：`52dc943eaf6093a2313271a7a0abc36d127b2a16609a0a7c54ee1b4e4ed06cb8`。逐文件版本和 SHA256 随实验清单保留。
- 原始模型目录只读保留。`scripts/benchmarks/prepare_mlx_model.py` 生成写时复制目录，仅将嵌套的图像处理器类名调整为 Transformers 支持的 `Qwen2VLImageProcessor`；权重、词表和数值预处理参数不变。
- 正式引擎为 [vllm-mlx](https://github.com/waybarrios/vllm-mlx) 0.6.0，mlx-vlm 0.7.6，Transformers 5.19.0，Python 3.12.9。先尝试的 vLLM Metal 0.32.0 未通过这台机器上 32K KV 缓存容量检查；相关失败日志保留，未计作正式结果。
- BGE-M3、CLIP 在 CPU 上运行。源机与 Mac 的模型权重、词表及预处理身份一致；真实嵌入数值兼容检查另行记录。解除推理锁改变实现哈希，因此不能声称实现身份或向量逐位相同。
- 云端 Judge 为 doubao-seed-2.1-lite。QA 与 Judge 独立异步调度，`judge_backpressure=false`。评分队列不降低 QA 请求数。

## 模型启动与契约

在隔离的引擎环境中安装上述固定版本，以派生模型目录启动：

```sh
export MLX_BUFFER_CACHE_LIMIT=512mb
export SENTRIX_MLX_CONTEXT_TOKENS=32768
export SENTRIX_MLX_EVENTS="$EXPERIMENT_ROOT/evidence/model-events.jsonl"
python scripts/benchmarks/serve_mlx.py serve "$DERIVED_MODEL" \
  --mllm --continuous-batching --max-num-seqs 16 \
  --prefill-batch-size 4 --completion-batch-size 16 \
  --mllm-prefill-step-size 512 --chunked-prefill-tokens 512 \
  --disable-prefix-cache --max-tokens 32768 --max-request-tokens 32768 \
  --served-model-name qwen35-4b-mlx-q4 --host 127.0.0.1 --port 18100 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 --enable-metrics \
  --default-chat-template-kwargs '{"enable_thinking":false}' --timeout 900
```

`serve_mlx.py` 有严格版本检查，并补齐以下基准所需契约：

1. `/tokenize` 使用引擎实际聊天模板、工具、结构化提示和图像展开计算输入长度。Sentrix 按剩余窗口分配输出预算，不按字符估算，不静默裁剪输入。窗口不足必须显式失败，统一增大窗口并重新验收后才能用于可比轮次。
2. 每请求独立的 MLX 随机状态固定为 42，避免其他并发请求消耗其随机序列。保留上游 top-p、min-p、top-k 的处理顺序。
3. vllm-mlx 0.6.0 的分块生成路径把未包含当前 token 的历史传给约束处理器，会触发 JSON grammar 错误并中止同批请求。适配器补上当前 token，与普通生成路径一致；不改变请求历史，不关闭结构化约束。
4. 上游允许新文本不断加入当前批次，图像却必须等整批结束；混合请求下会造成图像饥饿。发现等待媒体后停止新文本插队，完成已有分块预填充和生成，再按原到达顺序接纳媒体批次；保留批内并发，不对推理加互斥锁。队列指标同时包含调度器等待区和已接纳但尚未预填充的请求。
5. 长输入分块预填充期间，上游插入短请求后遗漏等待队列清理，同一请求可能被重复加入生成批次，产生多个结束输出及不一致的 token 计数。适配器在预填充处理成功返回后消费对应队列项，包含已经显式生成错误响应的输入；保留异常处理和批内并发。旧轮次的异常计数不修写，修复后重新建立统一系列。

固定采样：temperature 0.7、top_p 0.8、top_k 20、min_p 0、presence_penalty 1.5、seed 42、关闭思考。Agent 最多 30 个模型步骤及 30 次工具调用。模型服务超时、输出截断和未知结果分别保留，不通过自动重发消除原始失败。Agent 步数与规划器/工具内部的 HTTP 请求数分别统计。

文本和视觉工具的 HTTP 超时都显式设置为 1800 秒，Agent 墙钟上限 3600 秒；在启动诊断及恢复配置中分别核对。Qwen 配置下，当运行时已将可用工具收敛为唯一的 `final_answer` 时，使用其原有参数 schema 做严格 JSON Schema 约束解码，再将模型生成的原始参数交给该已确定的动作。vllm-mlx 0.6.0 的指定函数 `tool_choice` 只追加提示语；真实失败请求在只有收尾工具时仍反复调用旧工具并耗尽 30 步，隔离复现亦如此。新路径不附带 tools，避免服务端拒绝 tools 与 response_format 组合；保留全部对话、精确窗口预算、原始 token 用量及结束原因。JSON、字段或类型不合法时显式失败，不修写内容，不退回提示词约束或重发。答案和图片选择仍由模型生成并经过原有证据、声明和交付核验，不替换失败状态或放宽 30 步限制。其他模型配置和多工具选择沿用原生工具协议。

## 数据、恢复与轮次

完整 487 题文件 SHA256：`73fa4512dde072fb0df9cf45ff3ebd192192db606f9764054bde002dcbf0be47`，含 561 轮对话。固定 64 题含 69 轮对话，SHA256：`4f6d95b2673f3aa01a9dff7a6675293eaff45d5935c8418d8f31dbb810293e5b`。`mlx_capacity.py freeze-subset` 按整条 JSONL 的固定哈希排序选择，再恢复原文件顺序；保留多轮对话。

每一轮均使用 `memory_snapshot.py restore` 从同一验收快照恢复独立目录，并核对文件清单、迁移、媒体、向量和身份记录。macOS 使用 APFS 写时复制；绝不让两轮共用可写 SQLite 或 Qdrant 目录。该快照是历史验收的组合记忆库，不代表原始素材已全部成功入库。

每轮重启引擎以清除模型缓存，采用相同的 4 个文本/合成图片请求预热，然后提交唯一的 PhotoBench Run。提交意图先落盘，响应及 run_id 随后记录。恢复观察只查询已有 run_id；未知提交不得重放。C4、C8、C12、C16 各运行同一组 64 题两轮，最高通过档另开独立库运行完整 487 题。

通过条件包括完整 QA 和 Judge 终态、无执行/模型错误、真实生成批次及完整模型调用证据。应用主调用与视觉工具内部调用的数量、输入/输出 token 组合必须与引擎唯一请求的终态记录一致；缓存工具回放按原 call_id 去重，冲突或缺失不通过。答案得分独立报告；错误、取消和补跑不覆盖原始记录。最高通过档要求两轮都通过；吞吐最佳档按两轮总题数除以两轮 QA 总时长计算。

## 统计与页面

`mlx_capacity.py` 记录 CPU、GPU、统一内存、交换存量及换入换出计数、模型队列与活动数。`serve_mlx.py` 另从真实 token 生成批次记录实际批次大小、请求终态和首 token 时间。客户端提交 16 个请求不等于模型实际同时执行 16 路。

QA 和 Judge 时间、吞吐、完成与失败分开统计。QA 资源汇总只覆盖 QA 阶段；原始采样保留评分阶段。首 token 时间从调度器接收请求起算，包含模型队列等待，不包含 HTTP 请求之前的输入预处理。缺失值显示“未采集”，不替换为零。

请求终态同时保留调度器计数、实际返回对象的计数及返回事件，发现差异仍判失败，不用其中一个数覆盖另一个数。未知提交保留请求阶段、已知会话 ID、异常类型链及系统错误号，排除异常文本中的地址和凭据。同步 HTTP 客户端按工作线程隔离连接池，避免多线程清理闲置连接时共享句柄；不加请求全局锁，不增加未知 POST 重试。独立连接压测只验证 HTTP 和会话落库，不计作模型并发成绩。

Prometheus 只读查询可能在视觉预填充期间超时。查询期限为 30 秒，单独记录实际响应时间；CPU/GPU/内存的采集时间与模型指标响应时间不混称同一时刻。指标读取缺口展示成功/总采样数，保留原始错误，不补零，也不将它误判为生成失败。没有任何可用模型指标、模型调用终态无法对账或缺少实际生成批次，仍然不通过。队列峰值是观测到的峰值，存在缺口时不声称捕获了绝对峰值。

PhotoBench 的“并发实验”页读取 `capacity-report.json`，展示逐档比较、487 题总览及资源曲线。公开截图只包含这些汇总，不包含私人照片、问题、答案正文、文件访问地址或凭据。复核页面与原始汇总一致后再截图。

## 服务恢复

接管前保存旧 MiniCPM LaunchAgent、启动脚本和进程状态。以 `launchctl bootout` 正常停止旧服务，保留权重。实验结束后按记录的 PID 和命令行核验归属，停止新推理、嵌入和评测进程，关闭监控；再用保存的 LaunchAgent 配置执行 `launchctl bootstrap`，验证启动状态和健康端点。不得仅杀旧进程造成服务被自动拉起，也不停止其他应用。

## 编排脚本

`launch_mlx_engine.py`、`prepare_mlx_round.py`、`run_mlx_matrix.py` 是本实验的可审阅编排入口，端口与目录布局固定。运行前准备以下隔离目录；原始数据和含认证信息的配置不提交仓库：

- `repo/`：本分支代码及 Python 3.11 环境 `.venv/`，构建后的 PhotoBench 位于 `apps/photobench/web/dist/`。
- `envs/vllm-mlx/`：固定版本的引擎环境；`models/`：原始及派生模型、BGE、CLIP。
- `source200/snapshots/accepted-baseline-v3/`、`source200/runtime/memory-baseline-identity.json`、`source200/datasets/album3-max-video10/`：已验收冻结材料。名称是实验目录约定，不会连接源机。
- `private/photobench-config/`：有效的 PhotoBench 配置，其中 `runtime_connection.json` 冻结产品设置；Judge 凭据只在这个私有目录内。
- `evidence/source-code-manifest.json`：`revision` 与 `files` 路径→SHA256 映射；`engine-qualification.json`、`window-seed-qualification.json`、`preflight-result.json`：真实资格测试及预跑结果。编排器先检查这些证据。
- `runtime/`、`rounds/`、`results/`、`evidence/`：进程、独立轮次、原始结果和采样记录。嵌入侧车先启动在 18101/18103，并通过健康和数值身份验收。

```sh
export SENTRIX_MLX_EXPERIMENT_ROOT=/absolute/path/to/isolated-experiment
export SENTRIX_MLX_SERIES=unique-series-name
cd "$SENTRIX_MLX_EXPERIMENT_ROOT/repo"
.venv/bin/python -m scripts.benchmarks.run_mlx_matrix
```

编排器用文件锁排除重复启动。已知运行的观察可恢复；处于提交中但没有唯一 run_id 的状态必须先对照 PhotoBench 记录，禁止再次 POST。失败轮次保留后，修复与复测使用新的轮次/系列身份，不能把旧结果改成通过。资格测试、生成请求或运行提交发生未知结果时均不自动重发。

失败会暂停编排。核对原始日志、完成独立诊断，并确认没有遗留运行中的请求后，可在状态的 `inspected_failure_sha256` 中记录已检查的失败汇总哈希，继续后续独立轮次。此记录不改变失败结果，也不重发原题；后续新失败或汇总变化仍会暂停。最高通过档只使用两轮都通过的档位。额外诊断轮次标记 `purpose=diagnostic`，单独保留，不混入两轮正式比较。

规划器的结构化响应还将 `evidence_type` 限定为 `EvidenceType` 注册表枚举。原 schema 只要求字符串，4B 模型可输出属于 `value_type` 的 `media_set`，导致 JSON 合法却被业务解析器拒绝。新增约束与既有提示词、校验器保持一致；未增加别名，也未接受未知证据类型。旧失败及对应多轮记录保留，用原失败请求的真实生成验证修复后再建立新系列。

## 优化对照与 487 题速度门槛

2026-11-04 用户追加授权：完成优化后先测 C16，稳定且速度符合预期再执行
487 题。原 v7 配置保留 C4/C8/C12 各两轮、C16 第一轮及第二轮取消记录；
优化配置单独记录 C16 两轮。不会将不同推理版本混成同一条并发曲线。
速度门槛在测量前固定：两轮 QA/Judge 和模型调用核对全部通过，两轮合计
吞吐比原 C16 提升至少 15%，每轮比原 C16 合计吞吐提升至少 10%。
这是实际运行门槛，不宣称统计显著性。未达到则保留记录、继续定位，不启动 487。

优化使用独立环境 `envs/vllm-mlx-optimized`，保留原环境。
`apply_mlx_kv_patch.py` 校验原文件 SHA256，移植
[mlx-vlm #2510](https://github.com/Neywa-Labs/mlx-vlm/pull/2510)
的原始补丁，并核对完整新文件 SHA256。包版本仍是 0.7.6，报告必须注明补丁，
不能冒称已升级完整版本。`SENTRIX_MLX_OPTIMIZATIONS=strided_kv,batch_grammar`
启用读取带步长的 KV 和请求独立的语法掩码批量处理。
语法解析器、EOS、空白保护、处理器顺序和随机状态均保留请求隔离。
启动时版本或补丁哈希不匹配即失败，不静默退回另一配置。

整批采样过滤在本地 M4 算子对照中比原逐行过滤更慢，未纳入正式优化。
正式采样继续使用原逐请求 seed 42、temperature/top_p/top_k 顺序与参数。
`qualify_mlx_operators.py` 验证真实 llguidance/Metal 掩码一致性、重排及请求移出，
算子测试不替代真实服务协议与整题吞吐验收。

真实服务验收发现另一个兼容问题：vllm-mlx 0.6.0 捕获旧函数名
`_target_verify_left_padded_attention`，而 mlx-vlm 0.7.6 已使用
`_qwen3_5_left_padded_attention`。未衔接时，兼容层清除左填充标记后走空回退，
既没有执行带步长的 KV 内核，也漏掉原生左填充处理。适配器在兼容层安装前
建立函数别名；过晚安装或发现不符的旧函数立即报错。
真实单层注意力对照覆盖 C4/C8/C12/C16、不同长度和无填充情况，衔接后与
mlx-vlm 原生实现逐位一致，且有填充的 BF16 用例确实执行目标内核。
首次服务资格测试虽然达到实际批次16、隔离及计数正确，但内核调用为0，
仍保留为失败。修复后须以新资格记录重验全部真实协议，再启动正式轮次。

原 v7 基线同样包含上述左填充兼容缺陷，不能当作数值正确性基准。
保留其原始答案、评分和吞吐，仅按用户指定作为原部署的速度参考。
优化结果包含兼容性修复与性能改动，吞吐差异不能完全归因于 KV 或语法掩码；
实际调用数、token量和质量分布必须分别报告，不要求新旧答案逐位相同。

macOS psutil 7.2.2 的 `sin/sout` 是一般 `pageins/pageouts`，历史
`swap_in_bytes/swap_out_bytes` 字段保留原记录但不得解释为真实交换流量。
新采样另用 `vm_stat` 的 `Swapins/Swapouts` 记录
`actual_swap_in_bytes/actual_swap_out_bytes`。交换空间存量及其增量仍单独报告；
缺失数据保持未知，不能从交换存量推算交换流量。


### 用户要求立即切换时的基线处理

用户于 2026-11-04 明确取消原配置 C16 第二轮，改为直接测试优化版。
先停止两个调度控制器，再取消该 PhotoBench run；仅在活动工作清空并保留
取消凭据后切换。取消轮次单列，部分题目不计入吞吐基线，不重发未知请求。
默认速度门槛仍要求两轮基线；只有记录了本次用户取消凭据及其 SHA256，
并显式设置 `baseline_reference_mode=single_completed_trial_user_cancel` 时，
允许采用已通过的 C16 第一轮。优化候选仍必须完整通过两轮，提升门槛仍为
合计15%、每轮10%。单轮对照不宣称统计显著性。若取消前第二轮已经完成，
则保留真实结果并继续使用两轮基线。

## 预填充诊断与精确前缀缓存

v9 两轮 C16 均完成 64 QA / 64 Judge、实际批次16及调用核对，但加权吞吐
2.284741 题/分钟，仅比原部署参考提高 1.21%，因此速度门槛失败，未启动487。
原记录保留；后续候选使用新的系列。

独立16题诊断包含固定64题中的全部5道多轮题，保留完整对话。关闭缓存时，
78次模型调用处理159642个输入token，QA耗时520.1514秒。逐调度步去除重复
计时后，预填充阶段403.2748秒（77.53%）、解码阶段102.8505秒（19.77%），
实际复用0个token。阶段墙钟包括CPU提交、已有同步等待及首token采样，
不称为纯GPU内核耗时；异步执行仍可能把部分工作计入后续同步边界。
原始输入token只保存在私有诊断目录，公开汇总见
[独立诊断](../benchmarks/mlx-mac135/prefill-baseline-diagnostic.json)。

上游 [issue #730](https://github.com/waybarrios/vllm-mlx/issues/730)
讨论 Qwen3.5 混合缓存无法复用。
[PR #843](https://github.com/waybarrios/vllm-mlx/pull/843)
在稳定对话边界保存不可回退的循环状态；
[PR #842](https://github.com/waybarrios/vllm-mlx/pull/842)
释放快照复制辅助闭包保留的数组。两项已合并修复以精确补丁移植，保留
Apache-2.0 许可。Sentrix 的工具集合会变化，仅完整对话边界并不能覆盖主要
重复工作：本次实际输入的离线可复用比例为13.18%；最多1024个相同前缀token
的潜在复用比例为32.07%。这是离线估计，不是实际命中率或吞吐提升。

候选 `shared_prefix` 在原对话边界与1024之间取较小值，在该精确位置保存
原有 BF16/循环状态。上游缓存仍逐token精确匹配、复制独立状态并管理取消；
不进行近似匹配、缓存量化、输入裁剪或模板改写。只允许纯文本前缀，媒体请求
保留原处理路径。缓存上限固定1024 MiB，禁用磁盘载入/保存，独立轮次重启后
仅执行相同4个预热请求。该策略减少重复输入处理，不将预填充改称批量执行。

```sh
# 对停止的独立环境执行；所有源文件哈希必须匹配。
python -m scripts.benchmarks.apply_mlx_prefix_patch
export SENTRIX_MLX_ENGINE_ENV=envs/vllm-mlx-prefill
export SENTRIX_MLX_OPTIMIZATIONS=strided_kv,batch_grammar,shared_prefix
python scripts/benchmarks/launch_mlx_engine.py
```

正式启动仍须先通过真实协议、窗口、取消、计数、缓存隔离和批次16资格测试。
每次真正接受的缓存复用记录完整输入长度及复用长度；短输入在两条接纳路径中
重复观察时去重且检查一致性。缺少请求证据、计数不一致或缓存从未使用均不能
通过缓存候选的正式验收。合成请求或16题诊断的提速不替代原64题两轮门槛。

首次独立16题缓存对照完成：两次均16 QA / 16 Judge通过、实际批次16、零执行
错误。缓存候选耗时334.7733秒，对照520.1514秒，诊断吞吐提高55.37%；实际
60/73次调用复用61440个输入token，占144771个输入token的42.44%。预填充
阶段降至222.6513秒。输入token和模型调用数也从159642/78变成144771/73，
不能将全部吞吐差异归因缓存。此结果不是正式64题两轮验收；见
[缓存诊断汇总](../benchmarks/mlx-mac135/prefill-cache-diagnostic.json)。
此前12次真实冷/热请求的完整输入token、输出、用量计数和隔离均一致，
[资格凭据](../benchmarks/mlx-mac135/prefix-cache-real-qualification.json)
与整题吞吐单独报告。缓存诊断期间实际交换写出约508 MiB（无缓存对照为0），
后续正式轮次继续监测内存及真实交换流量，不能以完成测试推断没有内存代价。
离线潜在复用按请求预处理时刻估算；请求等候预填充期间产生的新缓存可提高
实际命中，故该估计不构成命中率上限。


### v10 完整487失败与 v11修复资格

v10 两轮64题仍保持原验收结果：加权3.435956题/分钟、相对原单轮参考+52.21%。随后完整487有13道Agent异常和2道无效/缺失评分，未通过；474道成功QA/9892.3164秒=2.874959题/分钟。不能用64题结果替代全量验收，也不能将原摘要中Judge completed=487当作487个有效评分。

引擎日志确认普通多模态路径一次接纳14个请求（合计36044输入token），错误使用completion_batch_size，未遵守prefill_batch_size=4，随后报Metal Insufficient Memory。新适配器仅在该路径接纳时应用已有预填充上限，并在退出或异常时恢复16路解码容量。16个客户端的独立长文本与四组视频关键帧资格通过；这仍是合成资格，不能替代正式QA复测。

另外修复两类实际调用问题：达到既有连续失败阈值的工具从后续工具列表移除，防止模型继续请求必被拒绝的动作；Judge无效JSON最多进行一次格式重试，原响应保留，第二次仍无效则明确失败，绝不从坏JSON补造分数。有参考答案、无可选行为标签的题仍送回答质量评分，行为标签保持未标注。所有新运行与原v10失败记录分离。

五个针对性回归先失败后通过；完整CI3414后端、143主前端、613PhotoBench测试通过，49个真实环境测试不在本地CI执行。新正式C16两轮及487须重新取得真实终态；原10%逐轮/15%加权速度门槛不变。公开证据位于 `docs/benchmarks/mlx-mac135/full487-v10-failure.json` 与 `prefill-admission-qualification-v11.json`。

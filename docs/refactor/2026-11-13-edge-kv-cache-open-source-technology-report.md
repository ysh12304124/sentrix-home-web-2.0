# Sentrix 端侧前缀缓存：开源技术与论文调研报告

日期：2026-11-13  
范围：在线 Agent 问答中的 prefill、KV 缓存与混合模型状态复用。本文只研究公开资料和现有代码，不包含新测试、部署或业务代码修改。

## 1. 结论先行

对当前 Qwen3.5-4B、GGUF、llama.cpp、OpenAI Chat Completions 兼容接口、Mac 端侧的组合，最值得借鉴的不是某个可直接安装的“KV 缓存插件”，而是三类方法：

1. **保证序列化后的真实 token 前缀稳定，并保留可恢复的混合模型状态。** 这与现有冻结工具、会话槽位亲和、`cache-ram` 和上下文检查点直接衔接，属于最低迁移成本的路径。OpenAI 请求 JSON 字段的顺序不决定模型看见的顺序；真正的序列由 chat template 决定。[Qwen3.5 模板](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/chat_template.jinja)、[llama.cpp 服务文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。
2. **在缓存容量紧张或前缀经常分叉时，更聪明地选检查点。** Sparse Prefix Caching 给出精确恢复的稀疏检查点方法；Marconi 给出按复用收益与状态内存权衡的准入、淘汰方法。它们可指导后续 llama.cpp 策略设计，但公开代码不是可以直接接入 Sentrix 的 llama.cpp 插件。[Sparse Prefix Caching](https://arxiv.org/abs/2605.05219)、[Marconi](https://arxiv.org/abs/2411.19379)。
3. **研究较长前缀在任意位置分叉时的状态重建。** SuffixReplay、Tail-Replay、DASC 直接针对混合注意力模型。SuffixReplay 已在 Qwen3.5-4B 上发表结果，但实现和实验条件与 Mac 上的 Q4 GGUF 差别很大；目前应看作运行时研发方向，不能把论文加速倍数当成项目收益预测。[SuffixReplay](https://arxiv.org/abs/2609.33477)、[Tail-Replay](https://arxiv.org/abs/2608.30310)、[DASC](https://arxiv.org/abs/2608.30386)。

若要评估**现成的 Apple Silicon 替代运行时**，oMLX 值得单独关注：它公开提供热 RAM、冷 SSD、块级前缀共享和 VLM 支持。但要获得其原生缓存机制，需要走 MLX 模型路径；仅把现有 GGUF 接到它的外部引擎，不能假定获得同一套缓存能力。[oMLX 文档](https://github.com/jundot/omlx/blob/main/README.md)。

## 2. 与 Sentrix 相关的技术前提

### 2.1 缓存的是模型状态，不是提示词字符串

llama.cpp 接收 Chat Completions 消息后，先用模型的 chat template 展开角色、工具说明、图片标记及特殊 token，再送入模型。前缀缓存匹配的是**展开后的 token 序列及相关多模态输入**。前面多一条工具定义、历史 assistant 的 `<think>` 标记不同、系统内容变化，都会让首个差异点之后无法按原前缀直接复用。后续即使出现一段相同检索文字，也不自动成为前缀命中。[llama.cpp 服务文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)、[Qwen 模板问题报告](https://github.com/QwenLM/Qwen3/issues/1826)。

Qwen3.5-4B 官方配置为 **32 层：24 层线性注意力、8 层全注意力**，全注意力在零起始层号 3、7、11、15、19、23、27、31。线上复用一个前缀，需要全注意力层的 KV 与 GatedDeltaNet 的循环状态都能正确恢复。模型权重 Q4_K_M、全注意力 KV 的 q8_0、GatedDeltaNet 状态的精度和检查点，是几种不同的内存项目；只量化 KV 并不能消除循环状态的占用。[Qwen3.5-4B 配置](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)。

可以把一次缓存命中分成三个层次：

| 层次 | 要满足什么 | 对耗时的实际含义 |
| --- | --- | --- |
| 文本或媒体前缀相同 | 相同模板、token、图片预处理结果与位置 | 只是可复用的候选条件 |
| 状态可恢复 | 命中边界有全注意力 KV，以及循环状态检查点或可重建输入 | 才能跳过相应模型计算 |
| 缓存仍驻留且读取划算 | 命中状态没被淘汰，恢复内存/磁盘数据比重算更快 | 才可能降低真实 TTFT 与端到端耗时 |

这也是为什么“缓存 token 占比”不能单独代表速度。前缀命中较多，但要回退到较早的循环检查点时，仍会发生后缀重算；缓存状态过大时，也会挤占模型与推理工作内存。[vLLM 混合模型前缀缓存说明](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)。

### 2.2 当前项目已有的基础

现有代码已经有 [按 conversation 维持槽位亲和的逻辑](../../apps/api/src/sentrix/domain/assistant/request_context.py)，单槽 Mac 交给 llama.cpp 自选；此前的执行文档记录了冻结一轮工具集、`cache-ram`、`ctx-checkpoints` 与 `checkpoint-min-step` 的调整。仓库还有 [MLX 基准用的有界精确前缀脚本](../../scripts/benchmarks/mlx_prefix_cache.py)，它限定公共前缀长度和缓存容量，属于基准实验设施，不等于当前生产推理已经切换到 MLX。相关背景与已有结果见 [原执行文档](2026-11-09-prompt-cache-optimization-plan.md)。

用户提供的 2026-11-12 汇报版显示，现有优化已让 Mac 的缓存命中显著提高，prefill 仍是重要耗时；但其中模型层数应按上面的官方配置校正。本文不复算或补测汇报数据。现有结果也提示：更激进地压短提示词或改变工具模板，可能损害问答质量和交图，所以后续技术采用不能只以缓存命中率作判断。

## 3. 可借鉴技术全景

“精确”指能够恢复与原推理算法一致的状态；“近似”指有意丢弃、压缩或重建部分状态，可能改变回答。

| 技术/项目 | 机制 | 精确性 | 公开形态 | 当前 Sentrix 适配判断 |
| --- | --- | --- | --- | --- |
| [llama.cpp prompt cache](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | 槽位状态、RAM 前缀、混合状态检查点 | 精确前缀，受恢复边界限制 | 当前运行时 | 直接相关；先从模板、槽位、检查点继续理解 |
| [Sparse Prefix Caching](https://arxiv.org/abs/2605.05219) | 按分叉分布稀疏保存循环状态 | 精确 | [研究代码](https://github.com/arabel1a/sparse-prefix-caching) | 可借鉴检查点布局；需改运行时 |
| [Marconi](https://arxiv.org/abs/2411.19379) | 分叉点准入，按计算节省/占用淘汰 | 精确 | [策略研究代码](https://github.com/ruipeterpan/marconi) | 容量成为瓶颈时有价值；需改缓存管理器 |
| [SGLang Unified Radix Cache](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/mem_cache/unified_cache/components/README.md) | KV、SSM 状态的统一前缀树与分组件管理 | 精确 | 可用 GPU 服务代码 | 架构参考；Mac GGUF 不能直接移植 |
| [vLLM APC](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/) | 块哈希、混合模型网格检查点 | 精确 | 可用 GPU 服务代码 | 理解检查点与分叉；不属于 Mac 当前引擎 |
| [LMCache hybrid](https://github.com/LMCache/LMCache/blob/dev/docs/source/mp/hybrid_models.rst) | 外置/分层 KV 与循环状态页 | 精确匹配路径 | vLLM 连接器 | 适于后续 Linux GPU 服务分支 |
| [oMLX](https://github.com/jundot/omlx/blob/main/README.md) | Apple Silicon 的 RAM/SSD 分层、块共享、VLM | 声称精确前缀 | 可用运行时 | 端侧候选；需要 MLX 模型与接口兼容评估 |
| [Qwen Code 工具稳定排序](https://github.com/QwenLM/qwen-code/blob/main/docs/design/prompt-cache/global-tool-schema-stable-sort.md) | 同一工具集按稳定键排序，避免发现顺序造成前缀变化 | 不改变模型计算 | 公开设计与代码路径 | 直接借鉴“先过滤后排序”；收益限于顺序抖动 |
| [Qwen Code ToolSearch/ToolCall](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md) | 固定桥接工具，长尾 schema 按需查询和调用 | 不改变模型计算 | 开源 Harness 功能 | 仅八个工具时，额外模型步可能抵消节省 |
| [Tail-Replay](https://arxiv.org/abs/2608.30310)、[SuffixReplay](https://arxiv.org/abs/2609.33477) | 留 FA KV，重放近期输入以恢复 GDN | 近似 | 论文/研究实现 | 长前缀频繁分叉时有研究价值 |
| [DASC](https://arxiv.org/abs/2608.30386) | 按状态衰减特性压缩循环检查点 | 近似 | 论文/研究实现 | 检查点内存瓶颈时有研究价值 |
| [CacheBlend](https://arxiv.org/abs/2405.16444)、[Prompt Cache](https://arxiv.org/abs/2311.04934) | 非前缀检索块复用、选择性重算 | 近似或有任务边界 | [LMCache 相关实现](https://github.com/LMCache/LMCache/blob/dev/examples/README.md)、[Prompt Cache 代码](https://github.com/yale-sys/prompt-cache) | 检索内容反复出现时才考虑；不适合直接套到 GDN |

## 4. 技术细节与可能的接入方式

### 4.1 现有 llama.cpp 路径：序列稳定与可恢复边界

**机制。** `cache_prompt` 复用先前请求的相同前缀；`--cache-ram` 保留额外的 RAM 前缀条目；`--ctx-checkpoints` 和 `--checkpoint-min-step` 控制混合/循环状态回退所需检查点。不同槽位以及历史被重新序列化，也会改变实际可复用程度。[服务参数说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。

**Sentrix 接法。** 保持已经决定的 OpenAI 兼容协议、现有模型服务、工具冻结与槽位亲和。把稳定系统内容、稳定工具集合、会话历史和当轮动态问题按模板实际展开顺序来审视；对每个阶段分别理解首次差异点。若 Qwen 官方工具模板被沿用，工具说明会放在 system 正文前，工具列表变动会很早截断共同前缀。只重排请求 JSON 里的 `tools` 字段无效，因为服务端模板决定模型可见顺序。[官方 Qwen 模板](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/chat_template.jinja)。

**边界。** `--cache-reuse` 是基于 KV shifting 的另一种功能，不等于 `cache_prompt`；混合/多模态模型存在不支持的报告，不能当作目前的即用开关。[llama.cpp 混合模型记录](https://github.com/ggml-org/llama.cpp/issues/24055)。`/slots` 保存恢复曾有图片相关改进，但混合状态检查点跨保存恢复仍有公开问题，因此也不能先假设重启后保留相同命中。[混合检查点问题](https://github.com/ggml-org/llama.cpp/issues/28194)。

**价值判断。** 这一方向以现有服务为基础，最适合解决“同一轮多次模型调用却反复重算长系统/工具前缀”的情况。若问题本身是每步新增了长而不同的检索结果，这项策略只能保护其之前的稳定部分。

### 4.2 Sparse Prefix Caching：少存状态，准确恢复

**问题。** 全注意力 KV 可以按 token 保存与截断；GatedDeltaNet 循环状态通常在一个位置保存一次完整快照。每隔很短距离都存快照会占用大量内存，只存结尾又让分叉后的请求无法在中间恢复。[论文](https://arxiv.org/abs/2605.05219)。

**算法。** 设两请求共同前缀止于 token `t`，已存循环状态的位置集合为 `C`。选最大的 `c ≤ t`，恢复 `c` 的状态，再计算 `c+1…t`，随后处理新后缀。只要所有层所需状态都精确恢复，这条路径在算法上不引入近似。论文根据历史请求分叉深度的分布，用动态规划为给定内存预算挑选检查点位置，而非固定每 N token 一份。[论文](https://arxiv.org/abs/2605.05219)、[代码](https://github.com/arabel1a/sparse-prefix-caching)。

**Sentrix 接法。** 如果将来发现缓存条目很多、长系统/工具前缀在几个固定边界分叉，可以借鉴“在高概率分叉处保留检查点”的决策。此边界可能包括模板结束、工具声明结束、旧对话结束、检索结果集结束。实现位置在 llama.cpp 的状态缓存管理层，不能用 Python 在请求里加一个字段完成。现有 `ctx-checkpoints` 提供检查点能力，论文方法进一步回答“哪些位置值得存”。

**适用边界。** 对单用户、只在历史尾部追加的对话，已有最后状态通常已经足够；稀疏布点收益主要来自多个请求在长公共前缀后反复分叉。公开项目含模拟与有限的模型推理实验，不是完整的 Qwen3.5-4B + llama.cpp + VLM 可部署插件。

### 4.3 Marconi：按“节省计算 / 占用内存”管理缓存

**机制。** Marconi 将请求区分为纯输入前缀共享、聊天式“输入加已生成输出”延续等情形；在值得复用的分叉位置才准入循环状态，淘汰时综合复用可能性、能够免掉的 prefill 计算量和状态内存，而非只看最近访问。[论文](https://arxiv.org/abs/2411.19379)、[研究 artifact](https://github.com/ruipeterpan/marconi)。

**Sentrix 接法。** 如果 Mac 16 GB 上有多个家庭会话、规划器与主 Agent 或多个多模态任务互相挤出缓存，可以把每个候选缓存条目估成：`预期再次命中概率 × 能跳过的 prefill 工作量 ÷ 驻留字节数`。长、稳定、反复出现的前缀值得保留；很短、只用一次的大状态不值得。应用层可以先区分会话、任务类别和固定前缀，缓存器则在底层选择状态。当前已有 conversation→slot 亲和，Marconi 更进一步解决槽位内和 RAM 缓存池的容量分配。

**边界。** 公开 artifact 主要是策略与请求轨迹评估，不是替代 llama.cpp 缓存器的服务。论文最高收益来自其特定并发、复用分布和模型，不能直接预测 Sentrix 单机单用户收益；如果目前没有频繁逐出，就不值得先造复杂淘汰器。

### 4.4 SGLang、vLLM、LMCache：成熟 GPU 服务的缓存结构

**SGLang Unified Radix Cache。** 它用统一前缀树关联不同状态组件，分别管理全注意力 KV、滑窗注意力和 Mamba/SSM 等循环状态；分叉时使用引用/复制策略，避免把整段状态重复物化。会话感知缓存还能把活跃会话的状态标记为较高保留价值。[组件设计](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/mem_cache/unified_cache/components/README.md)、[会话缓存文档](https://github.com/sgl-project/sglang/blob/main/docs/docs/advanced_features/session_radix_cache.mdx)。Sentrix 可借鉴“一个前缀键关联多个模型状态组件”和“活跃 conversation 更难被淘汰”，但不能把 Python 组件复制进 GGUF/Metal 服务就获得同样能力。

**vLLM APC。** 它按块复用前缀，混合 Mamba 模型必须在可复用边界有状态快照。文档中的共享前缀检查点开关 `--enable-mamba-shared-prefix-checkpoint` 默认关闭，并附有 `--prefix-match-unit`、EAGLE/MTP 等前提；不能把它理解成任意混合模型都能独立打开的开关。其 FlashInfer ReplaySSM 也是特定 Mamba 后端、数据类型和推测解码配置，不能直接推广到 Qwen3.5-4B 的 llama.cpp 路径。[vLLM 文档](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)。

**LMCache hybrid。** 其多进程连接器文档已讨论 Qwen3.5 一类混合模型的状态缓存：全注意力块与循环状态作为不同对象组管理，并要求匹配状态与对象组配置。它适于研究 Linux GPU 服务上的外置/分层缓存，不适合作为当前 Mac llama.cpp 的直接扩展。[LMCache 混合模型文档](https://github.com/LMCache/LMCache/blob/dev/docs/source/mp/hybrid_models.rst)。

**Sentrix 的实际用途。** 如果未来 153 服务器采用足够显存的 GPU 并切换到 BF16/FP16 或受支持量化模型，SGLang/vLLM + LMCache 可成为并行服务方案；仍要逐项核对 Qwen3.5-4B 的图像、工具调用、结果序列化和模型质量。它们的设计细节也可以作为自研端侧缓存策略的参考，但运行时切换不是修改 API 协议的必要条件。

### 4.5 Tail-Replay 与 SuffixReplay：用近期输入重建循环状态

**原理。** GatedDeltaNet 通过门控/衰减逐步更新状态，较远历史的影响可能变小。Tail-Replay 保留全注意力的精确 KV，把最近一段线性层输入隐藏状态留下；命中前缀时从较近后缀重放以近似恢复循环状态，避免在每个潜在分叉点存大检查点。[Tail-Replay](https://arxiv.org/abs/2608.30310)。SuffixReplay 加上分层与按 token 稀疏的 anchor，并让重放预算有上界，允许在受支持的页边界复用前缀。[SuffixReplay](https://arxiv.org/abs/2609.33477)。

**论文结果与前提。** Tail-Replay 报告在 5–10% 后缀重放预算下，LongBench/RULER 质量为完整 prefill 的 92.8–99.9%，32K 前缀时其研究设置的 TTFT 加速最高 14.3 倍。SuffixReplay 在 Qwen3.5-4B 等三种混合模型上报告了 91.4–100% 的平均质量、相对 SGLang 默认 8192-token 检查点 0.36–0.51 倍的摊销存储，分叉负载 p50 TTFT 降低 15–70%。这些是论文实验条件的数字，既不是 Sentrix 测试，也不是 Q4 GGUF + Metal 的预测。[两篇论文](https://arxiv.org/abs/2608.30310)、[SuffixReplay](https://arxiv.org/abs/2609.33477)。

**Sentrix 接法。** 适用于大量长、相同前缀在不同问题处频繁分叉，且精确检查点内存太贵的场景。运行时需保存层级 anchor、做 FA KV 与 GDN 状态协调、处理多模态位置及量化误差，并在恢复后保证下一 token 所需状态完整。不能在 Agent 的 Python prompt 构造层“多传一点元数据”来实现。若答案质量特别依赖精确 OCR、人物身份或照片细节，近似恢复的误差可能比一般文本基准更重要。

**成熟度。** 公开论文提供算法与实验描述；截至本报告日期，未核实到可直接用于 llama.cpp/Qwen3.5-4B/GGUF/Metal 的作者发布插件。现阶段可作为运行时研发题目，而非现有服务开关。

### 4.6 DASC：按状态衰减压缩检查点

**机制。** DASC 通过模型权重估算不同 GDN head 或 KDA channel 的保留时长，只保存对较长历史仍敏感的状态单元，形成不等长的压缩状态。恢复时对丢弃单元填零，或用有限后缀重算补齐。全注意力 KV 的处理相对独立。[DASC 论文](https://arxiv.org/abs/2608.30386)。

**Sentrix 接法。** 只有在“循环状态检查点占内存，使本来能命中的前缀被淘汰”成为主要问题时才有意义。它减少状态容量成本，可能允许保留更多分叉点；代价是近似误差、重建计算和底层状态布局改造。论文没有给出 Sentrix 当前 Qwen3.5-4B Q4 GGUF 上的可直接采用实现，因此优先级低于精确检查点布局。其收益应与最简单的“少存几个精确检查点”比较。

### 4.7 oMLX：Apple Silicon 原生替代运行时

**机制。** oMLX 的服务文档声明块式前缀共享、copy-on-write、RAM 热层与 SSD 冷层，支持 VLM、图像输入、工具调用和 OpenAI 兼容 API。对于多轮会话或长文档前缀，当热层装不下而冷层状态仍存在时，可从 SSD 恢复缓存块。项目也提供 KV 压缩选项。[oMLX README](https://github.com/jundot/omlx/blob/main/README.md)。

**Sentrix 接法。** 后端通过现有 Chat Completions 端口指向一个独立的 oMLX 服务，采用其支持的 Qwen3.5-4B MLX VLM 权重与相应图片处理。需保持与当前请求一致的工具 schema、图像表示、thinking 开关、历史消息和采样参数。要特别区分 oMLX 原生 MLX 引擎与它可接入的 GGUF 外部引擎：外部引擎服务 GGUF，并不自动继承 MLX 引擎的分层缓存实现。[oMLX 模型/引擎说明](https://github.com/jundot/omlx/blob/main/README.md)。

**边界。** SSD 命中有读盘与反序列化成本，短前缀可能重算更快；16GB 统一内存也要容纳模型、图像编码、工作区和热缓存。运行时更换会同时改变量化、VLM 预处理、工具解析、数值输出和系统资源行为；公开项目仍有混合模型缓存/工具交互的缺陷报告，不能用宣传中的“兼容 API”代替端到端能力确认。[相关 issue](https://github.com/jundot/omlx/issues/825)。这是一条单独的运行时选择路径，不应与现有 llama.cpp 调参结果混在一起。

### 4.8 非前缀块复用：CacheBlend、Prompt Cache 与 DARIC

**为什么吸引人。** Sentrix 的 `search_memories` 返回候选照片摘要；不同问题可能反复读到同一张照片或同一人物资料，但这些块前面的对话和检索排序会变化，普通“从第一个 token 起匹配”无法直接复用后面的相同片段。

**CacheBlend。** 它尝试缓存检索文档块的 KV，再通过位置处理与选择性 token 重算修复跨块注意力依赖；LMCache 有相关实验示例。[CacheBlend 论文](https://arxiv.org/abs/2405.16444)、[LMCache 示例](https://github.com/LMCache/LMCache/blob/dev/examples/README.md)。**Prompt Cache** 更偏向把提示词写成可复用模块/模板，公开代码的模型覆盖以较早的 Llama2、Falcon、MPT 为主。[论文](https://arxiv.org/abs/2311.04934)、[代码](https://github.com/yale-sys/prompt-cache)。**DARIC** 进一步研究按分布选择需要重算的非前缀 KV，有 [公开 vLLM 代码](https://github.com/Lenovo-ICI/DARIC)，但未核实 Qwen3.5 混合状态端侧支持。[DARIC 论文](https://arxiv.org/abs/2611.09109)。

**Sentrix 接法与难点。** 对相同照片描述块，朴素地按文本哈希拼接旧 KV 是不正确的：位置编码变了，前面问题和别的检索项也改变该块的内部表示；GDN 循环状态更依赖之前所有 token。因此若只想减少当前 Agent 耗时，先让多次请求共享真实前缀、避免重复送无关检索项更合适。非前缀 KV 复用要进入模型层、实现选择性重算，并验证人物名更新、证据绑定和视觉细节任务的准确性。它属于较远期研究选项。

### 4.9 KV 量化、TurboQuant 与多模态特征缓存

**KV 量化。** 减少全注意力 KV 每 token 字节数，可以留住更长或更多前缀，但不改变“两个请求是否有相同前缀”，也不直接压缩 GDN 检查点。Sentrix 已有 q8_0 设置；进一步压缩只有在全注意力 KV 确实挤占内存时才值得关注。[llama.cpp KV 类型说明](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。

**TurboQuant。** 论文提供更激进的 KV 量化思路；相关 llama.cpp 社区实现仍处于实验状态，尤其 Metal 上要把编码/解码开销计入真实速度。它不是提高前缀匹配率的方法，可能降低内存但拉低 decode 速度，故不宜把“缓存更小”等同于“回答更快”。[TurboQuant 论文](https://arxiv.org/abs/2504.19874)、[llama.cpp 讨论](https://github.com/ggml-org/llama.cpp/discussions/20969)。

**图像缓存。** VLM 链路有两类不同对象：图像编码器产生的视觉特征，以及语言模型处理图像 token 后形成的前缀状态。对同一张原图、同一预处理配置、同一 vision encoder，缓存视觉特征有机会省掉重复编码；对不同问题，不能把旧问答或旧语言 KV 无条件复用。键至少应包含模型/视觉投影器版本、图像内容、裁剪与分辨率、预处理参数，缓存失效还要考虑用户更新人物姓名等业务数据。现有 `inspect_photo` 已可能先读离线观测结果，应用级观测复用与底层 KV 缓存也应分开看。[oMLX VLM 实现](https://github.com/jundot/omlx/blob/main/omlx/engine/vlm.py)。

### 4.10 Harness 层：稳定工具声明与固定路由入口

**Qwen Code 的稳定排序。** 其公开设计指出，工具 schema 位于提供方缓存前缀的前部；MCP 工具异步发现可能让同一批工具以不同顺序注册，从而无谓地打断后面的 system 与历史缓存。方案是在生成工具声明的单一入口先做可见性过滤，再按稳定名称排序。它只处理“内容相同、顺序不同”的情况；新增、删减或修改 schema 仍会改变前缀。[Qwen Code 设计文档](https://github.com/QwenLM/qwen-code/blob/main/docs/design/prompt-cache/global-tool-schema-stable-sort.md)。Sentrix 已有本轮工具冻结，若当前 JIT 输出的同一集合可能随 set/字典遍历顺序变化，这一做法可作为低成本的稳定性约束。排序要保留模型可见的真实 schema 文本，并且全链路只在一个位置执行；否则诊断日志与实际请求可能不一致。

**固定桥接工具。** Qwen Code 的 ToolSearch + ToolCall 把长尾工具放在一个固定入口后面：主模型先搜可用工具，再通过通用调用工具执行；搜索和调用过程不重新声明完整 schema，因此主模型的工具前缀可以维持稳定。它以多一次搜索/调用轮次换取更短的初始工具表和更稳定的前缀。[Qwen Code 配置说明](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md)。[CacheRouter 论文](https://arxiv.org/abs/2608.22708) 进一步提出由独立路由模型选择并执行长尾工具，主模型始终只看固定核心工具。论文的价格收益基于云端 DeepSeek 缓存计费，不等于本地 Qwen 的时间收益。

**Sentrix 的取舍。** 当前工具数只有八个，照片级工具、统计工具和 `final_answer` 已有明确规划与后端策略。若新增 ToolSearch/ToolCall，4B 模型可能多一轮“搜索工具→调用工具→回答”，而 Mac 上 decode 和模型步本身都不便宜。可借鉴的是**固定、最小的核心工具表**和**同一工具集的稳定排序**；是否引入桥接入口，取决于未来工具数与 schema 是否明显增长，不能因为 Qwen Code 的大型工具生态采用了它就直接迁移。对于 `final_answer` 等终止动作，后端预算规则仍是权威，不能靠桥接工具绕过。

**DeepSeek Harness 的启发。** 官方 SDK 文档明确说明，JSON-RPC 框架、会话通知等服务端 bookkeeping 不进入模型上下文；用户消息按原块顺序追加，已保留历史的前缀可复用；历史压缩替换会改变此前缀。[DeepSeek Harness SDK 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sdk/server/README.md)。Sentrix 因此应区分“后端内部状态或日志字段”和“最终送给模型的序列化消息”；只有后者才增加 prefill。保持已确认历史的追加式构造有利于命中，若为缩短上下文而改写旧历史，必须把缓存失效视为压缩的成本之一。这是 Harness 设计原则，不依赖 DeepSeek API，也无需更换现有 Chat Completions 协议。

## 5. 适合 Sentrix 的三条技术路线

| 路线 | 可以借鉴什么 | 需要动到哪里 | 适用前提 | 主要风险 |
| --- | --- | --- | --- | --- |
| A. 继续 llama.cpp | 模板/token 稳定、精确检查点、Sparse/Marconi 的选择策略 | 当前消息序列化与 llama.cpp 缓存配置；更深方案需改 C++ 运行时 | 已有相同前缀，但状态恢复或淘汰仍浪费 prefill | 误以为 JSON 字段顺序或 `--cache-reuse` 能解决混合状态问题 |
| B. Mac 原生替代 | oMLX 分层缓存、MLX 混合模型缓存、视觉特征复用 | 独立模型服务与 MLX 权重，API 端口可保留 | 希望在 Apple Silicon 上改善热/冷缓存与并发 | VLM、工具调用、量化、内存行为与现有 GGUF 不一致 |
| C. 后续 GPU 服务 | SGLang/vLLM/LMCache 的块级混合状态管理，研究 Tail/SuffixReplay | 新推理运行时与 GPU 资源 | 未来部署在有合适显存的 Linux 节点，或长分叉负载很重 | 资源和维护成本高，研究论文收益不能直接迁移 |

目前看，**A 是与现有工作最连续的路径**。若后续真实瓶颈是持续 15 秒级 prefill，却没有可复用前缀（每轮检索内容都不同），任何缓存管理器都只能有限改善；需要从减少模型轮数、缩短动态证据和减少重复视觉调用着手。若耗时主要在 decode，前缀缓存同样不能解决。这里的判断来自机制，不是新基准结果。[vLLM 对 APC 适用范围的说明](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)。

## 6. 判断是否值得采用时需要分清的指标

本节只是技术选型判据，**本报告不执行测量**。

1. **请求形状。** 区分规划器、主 Agent 第一次调用、工具结果后续调用、图片观察调用、普通聊天；缓存机会完全不同。
2. **首个不同 token 的位置。** 同一请求的文本字符数不足以推断命中。工具模板、`<think>`、媒体标记和历史消息序列化可能改变更早的位置。
3. **状态恢复位置。** 区分“公共 token 前缀长度”与“混合状态最后可恢复检查点”；两者差距就是需要重算的后缀。
4. **容量和存储层级。** 同时看模型权重、FA KV、GDN 状态、图片特征、RAM/SSD 读取成本；不能只比较 KV 字节数。
5. **质量与总时延。** 近似状态重建、模板改变和新运行时都要看最终答案、照片交付、人物身份、OCR、工具错误及端到端耗时，而不只看 TTFT 或缓存 token 比例。

## 7. 资料清单与成熟度说明

所有链接指向论文、项目本身或官方文档。论文数字均保留其原有测试条件；本报告没有运行 Sentrix 模型。

| 类别 | 资料 |
| --- | --- |
| 模型与现有引擎 | [Qwen3.5-4B 配置](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)、[Qwen chat template](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/chat_template.jinja)、[llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) |
| 精确混合状态缓存 | [Sparse Prefix Caching 论文](https://arxiv.org/abs/2605.05219) / [代码](https://github.com/arabel1a/sparse-prefix-caching)、[Marconi 论文](https://arxiv.org/abs/2411.19379) / [代码](https://github.com/ruipeterpan/marconi) |
| GPU 服务架构 | [SGLang Unified Radix Cache](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/mem_cache/unified_cache/components/README.md)、[vLLM APC](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)、[LMCache hybrid](https://github.com/LMCache/LMCache/blob/dev/docs/source/mp/hybrid_models.rst) |
| 近似混合状态重建 | [Tail-Replay](https://arxiv.org/abs/2608.30310)、[SuffixReplay](https://arxiv.org/abs/2609.33477)、[DASC](https://arxiv.org/abs/2608.30386) |
| 端侧运行时 | [oMLX](https://github.com/jundot/omlx/blob/main/README.md) |
| 非前缀模块缓存 | [CacheBlend](https://arxiv.org/abs/2405.16444)、[Prompt Cache](https://arxiv.org/abs/2311.04934) / [代码](https://github.com/yale-sys/prompt-cache)、[DARIC](https://arxiv.org/abs/2611.09109) / [代码](https://github.com/Lenovo-ICI/DARIC) |
| Harness 前缀设计 | [Qwen Code 工具稳定排序](https://github.com/QwenLM/qwen-code/blob/main/docs/design/prompt-cache/global-tool-schema-stable-sort.md)、[Qwen Code 桥接工具](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md)、[DeepSeek Harness SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sdk/server/README.md)、[CacheRouter 论文](https://arxiv.org/abs/2608.22708) |
| 内存压缩 | [TurboQuant](https://arxiv.org/abs/2504.19874)、[llama.cpp 社区讨论](https://github.com/ggml-org/llama.cpp/discussions/20969) |

**成熟度用语：**“论文有结果”不等于“有开源推理服务器”；“开源服务器支持 Qwen3.5”不等于“当前 Q4 GGUF、VLM、工具调用与混合状态缓存均受支持”；“相同 API”也不等于 token 模板和模型输出相同。具体实现选择应以要采用的那一条运行时、模型格式与任务能力为单位判断。

# Sentrix 在线 Agent 加速技术调研（候选方案，未授权实施）

日期：2026-11-10。代码基线：GitHub `main@65ce640bc7697970c3a414eb2205d22accd658cf`。目标是缩短一次问答的完成时间，保持已确认的答案、证据和选图语义。本文件是研究与试验顺序，不修改已确认的提示词计划，不表示任何方案已在 153 测得收益。用户部署并验证；实施者按既定约定只做语法检查。

## 先看 Sentrix 当前的时间花在哪里

`RunTurn.run()` 先用同一个主模型执行 `GoalPlanner.declare()`，成功后才进入 `_converse()`；后者每一步先重算 JIT 工具表，再调主模型，工具调用完成后继续下一次模型调用。最终回答可能额外经过按风险触发的 L2 或 L3 模型核验。独立工具调用目前按模型给出的顺序逐个执行。`OpenAIChatModel` 发 `stream:false`，每次 `_post()` 新建 `httpx.Client`，只记录总耗时和常规 `usage`，没有把 llama.cpp 的 `timings.cache_n/prompt_n/prompt_ms/predicted_ms` 持久记录为稳定字段。参见 `application/assistant/run_turn.py`、`goal_planner.py`、`infrastructure/assistant/chat_client.py`。

`RetrieveEvidence.execute()` 先依次运行 metadata、entity、lexical、visual ANN、text ANN 五条 primary 通道，再融合、扩图和可选重排。通道结果插入顺序影响 anchor 加分，但**执行可并行、合并仍按固定顺序**；目前字典字面量中的五次 `record(...)` 是顺序执行。参见 `application/retrieval/retrieve_evidence.py`。这意味着减少模型输入、减少串行模型轮数、并行独立 I/O、服务器缓存/解码调优是四种不同的加速杠杆。

## 成熟 Harness 的可借鉴做法

| 来源 | 公开机制 | 对 Sentrix 的意义 |
| --- | --- | --- |
| [DeepSeek Harness：system prompt](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/system-prompt/README.md)、[agent loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md) | 固定顺序组装提示词和可见工具；重试复用已装配请求；普通对话/工具结果追加进历史。支持时才把变化的 system 作为历史更新。对可并行工具使用有上界的执行池，对独占工具设屏障。 | 保留 Sentrix 已定的稳定前缀和后端门禁；可尝试独立工具并行，但不要把数据库写入或“先检索后看图”的依赖并行。端侧 Chat Completions/llama.cpp 不具备其所有原生更新能力。 |
| [DeepSeek Harness：compaction](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/README.md)、[tool result pruner](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/compaction-tool-result-pruner/README.md) | 大工具结果先限长，旧上下文接近预算时再压缩；原始事件留在日志，给模型的是有界替代。 | Sentrix 已按固定 6 张预览和精简工具结果迈出同方向；旧工具往返只在超窗时裁剪，进一步压缩要保留真实资产绑定和答案证据，不能删掉当前轮关键照片。 |
| [Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)、[OpenAI Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201) | 工具、系统指令和历史顺序保持稳定，配置变化尽量追加消息；工具增删和旧历史改写会断开精确前缀。官方 OpenAI 的 `allowed_tools` 可在不改固定工具清单的情况下限制本次调用。 | 可借鉴前缀诊断和追加历史；`allowed_tools` 是官方服务能力，当前 llama.cpp 兼容端点不能假定支持。Codex 使用 Responses API，不是要求 Sentrix 换协议。 |
| [Claude Code 工具搜索的维护者说明](https://github.com/anthropics/claude-code/issues/12836)、[Anthropic 性能建议](https://claude.com/resources/articles/reducing-cost-and-improving-performance-with-claude-platform) | 大工具目录只先给名称，按需找完整 schema；动态上下文追加在缓存后。 | Sentrix 只有约 8 个小工具，额外 `ToolSearch` 模型轮次可能超过省下的 schema 成本；其服务端 `defer_loading` 也不是 llama.cpp 原生兼容能力。目前应先比较逐步 JIT 与本轮固定精简工具包。 |
| [Qwen Code 工具稳定排序](https://github.com/QwenLM/qwen-code/blob/main/docs/design/prompt-cache/global-tool-schema-stable-sort.md)、[压缩缓存共享](https://github.com/QwenLM/qwen-code/blob/main/docs/design/chat-compression-cache-sharing.md) | 相同工具集合按稳定顺序序列化；压缩时优先在原会话前缀后追加压缩指令以复用缓存。 | Sentrix 已按固定 `TOOL_SPECS` 排序；主要问题是 JIT 增删工具，不是注册顺序。可借鉴“压缩触发时尽量保留旧请求前缀”，但短家庭问答未必需要另一次摘要模型调用。 |
| [Gemini CLI 配置](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md) | 工具选择前可按任务过滤，旧工具输出有独立的截断/遮蔽预算。 | 与 Sentrix 的后端 JIT、预览上限类似；更适合把工具结果的模型可见投影做成显式预算，并只在长对话中遮蔽旧结果。 |

## 对当前端侧部署最值得尝试的顺序

### 0. 先补低开销的耗时与缓存诊断

这是后续选择方案的判据，不是要求现在做模型实测。每次模型调用记录 `call_type`、模型/模板指纹、工具名顺序/Schema 指纹、请求总时长、llama.cpp `timings.cache_n/prompt_n/prompt_ms/predicted_ms`、排队时长、是否重试；每个工具记录自身耗时，检索已有的逐通道 `ChannelTrace` 继续使用。不要记录用户问题和照片文字全文。llama.cpp 的 OpenAI 兼容响应公开提供这些 timings 和 `usage.prompt_tokens_details.cached_tokens`；Sentrix 现有客户端只提取常规 `prompt_tokens/completion_tokens`。[来源：llama.cpp server 文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。目标指标是整轮完成时间、总 `prompt_n`、总生成 token、模型调用次数和错误/重试次数；单独的缓存命中率不足以判断收益。

### 1. 先确认 Qwen3.5 的缓存能否按预期复用

Qwen3.5-4B 官方配置有线性注意力和每四层一次的全注意力；它的历史状态不仅是普通逐 token KV。[来源：Qwen 官方配置](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)。llama.cpp 维护者在 Qwen3.5 中段修改引起全量重算的报告中指出，回退到较早位置可能需要上下文检查点；该报告已按“混合架构的预期行为”关闭，不是一般模型都能套用的缓存规律。[来源：llama.cpp #20643](https://github.com/ggml-org/llama.cpp/issues/20643)。153 旧部署快照是 `--cache-ram 128 --ctx-checkpoints 0 --parallel 4`；main 将来部署是否沿用尚未知。`--cache-prompt` 默认开启，`--cache-ram`、`--ctx-checkpoints`、槽位相似度和 `--parallel` 是不同设置。[来源：llama.cpp server 文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。

建议部署后先做三种请求形状的对比：完全相同请求、仅尾部追加工具结果、早期工具列表发生变化；分别查看实际 `cache_n/prompt_n`、`prompt_ms` 与总耗时。当前非流式客户端没有首 token 时间；若要测它，需另用隔离的流式探针。再在资源允许的隔离配置中比较 `ctx-checkpoints=0` 与小规模非零值、`cache-ram` 和并发槽数。检查点会占内存，不能直接照抄默认 32；Qwen3.5 中段回退也不保证因此变快。若早期变化始终重算，优先让一轮中的工具清单稳定、减少总输入和模型轮数；若尾部追加能复用，继续保持 append-only。

### 2. 选择稳定且小的本轮工具包

主计划已把逐步 JIT、按规划结果固定本轮工具包、全量工具列为待实验的 A/B/C。[Qwen3.5-4B 官方 chat template](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/chat_template.jinja)把有工具时的工具声明排在主 system 正文前；如果实际 GGUF/服务端沿用此模板，每步增删工具会很早改变前缀。根据当前 Qwen3.5 混合缓存问题，B 比此前更值得试：每题仍只给相关的几份 schema，但同一轮的工具清单固定，检索后不在前缀最前面插入看图工具。**这是基于公开实现和当前代码的推断，不是已测结论。** 必须同时统计 4B 因提前看到暂不可用工具而多犯的错误，以及工具预算耗尽/`final_answer` 特殊 JSON Schema 分支的前缀变化。若错误模型步增加，A 可能仍更快。不要为仅 8 个工具先增加 Claude 式 ToolSearch 轮次；不要只重排请求 JSON 的 `tools` 字段，服务端模板决定模型可见顺序。

### 3. 并行独立检索通道，保持融合语义

五个 primary 检索通道目前顺序执行，可将只读通道提交到有界执行池，按原 `metadata → entity → lexical → visual_ann → text_ann` 顺序取回结果、构造 `channel_hits`、写 `ChannelTrace` 并执行既有融合；扩图必须等 primary 融合后再做。这样候选排名和 anchor 判据可保持一致，收益上限取决于最慢通道和共享 embedding/数据库资源。实施前确认各仓储连接、向量侧车和线程安全，限制并发，防止与导入争主模型槽位；用户部署后比较相同问题的候选顺序/证据等级以及检索耗时。该建议来自对当前代码的推断，DeepSeek 的有界并行工具执行只说明这种调度模式可借鉴，不证明 Sentrix 检索已适合无条件并行。

### 4. 并行同一步相互独立的工具调用

当前模型一次回复若给出多个工具调用，`RunTurn._converse()` 依次 `_run_tool()`。可只对独立读操作建立有界并行组，例如已存在结果集上的两个不同 `query_photo_people`，或互不依赖的统计与档案读取；原始 call 顺序、每个 `tool_call_id` 的配对响应顺序、证据账本提交顺序仍固定。`search_memories` 与依赖其结果集 ID 的读字/看图必须串行；`final_answer` 必须等其他调用完成。若 Qwen 4B 很少在一条回复中发多工具，该项没有收益。DeepSeek Harness 的 bounded pool 与 barrier 是参考实现。[来源：DeepSeek agent loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md)。

### 5. 减少一次问答中的串行模型轮数：作为隔离方案验证

多数照片题当前至少经历规划模型、Agent 选择检索、Agent 读到预览后回答三次生成。候选方案是只在规划器给出明确 `photo_set` 且范围可由原问题直接表达时，后端预先执行一次只读检索，记录为权威工具事实，再把结果以合法的对话/工具往返形状提供给 Agent；Agent 仍可补查、看图及独立撰写最终答案。另一种低风险尝试是在规划模型运行期间以原问题并行预取检索结果，仅当后续真实 `search_memories` 调用的 query/scope 与预取键一致时复用，这只隐藏检索延迟，不节省 Agent 模型步。任何自动检索都必须保持结果集、scope、资产 ID、证据账本和图交付门禁一致，不能把猜测结果伪装为已经由 Agent 调过的工具。此项是针对当前代码的架构推断，尚无 Harness 来源证明对 Sentrix 有净收益；若预取浪费或影响答案，应舍弃。

### 6. 长对话的工具结果预算与压缩时机

已确认的成功工具结果精简应先实施。随后只对超出单槽上下文预算的旧工具结果做类型化裁剪：保留本轮已交付照片的 handle、真实证据关联、统计筛选范围与答案相关文字；旧的失败堆栈或重复预览可缩短。当前代码是收到 `PromptTooLongError` 后逐轮删除旧工具往返，修改旧历史会断开变化点后的缓存。提前在同一轮连续请求间重写旧结果也会断开缓存；因此以“避免超窗与避免重算”的总耗时选阈值。可借鉴 DeepSeek 的先修剪大工具结果、再压缩旧历史，以及 Qwen Code 的压缩请求尽量复用现有前缀，但不能直接照搬其云端缓存标记。

### 7. 解码、服务与体验层的低优先级选项

- llama.cpp 支持多种 speculative decoding，但 Qwen3.5 的混合状态曾限制草稿模型兼容；合并后的实现有人报告在一台机器提速、另一台机器变慢。当前主模型仅 4B，草稿模型会再占内存和计算；先测真实 `predicted_ms` 是否占大头，再考虑独立试验。[llama.cpp 参数文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)、[Qwen3.5 讨论记录](https://github.com/ggml-org/llama.cpp/issues/20039)。
- `--parallel` 越大不必然让单个问答更快：它影响单槽上下文、排队、缓存槽位与导入并发。以实际并发用户和导入争用为边界测 2/4 等配置，不为了单题加速直接减少生产吞吐。[llama.cpp server 参数](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。
- 将 `stream:false` 改成流式响应主要改善用户**看到首段内容的时间**，不一定缩短完整答案时间；Sentrix 最终还需证据门禁和 `selected_image_handles`，不能把未审定的中途文字直接交付。可先流式展示阶段进度，不承诺壁钟加速。
- Qwen 官方同时给出 vLLM/SGLang 的 OpenAI 兼容服务示例；切换推理引擎会改变 GGUF/量化/图像和显存条件，是另一项部署试验，不是改 Chat Completions 协议的必要前提。[Qwen3.5-4B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-4B)。
- 结果缓存优先限定于只读检索/统计，键必须含 scope、规范化条件及相册数据版本；人物命名、关系更正、导入/删除后失效。完整自然语言答案缓存容易把旧姓名、旧照片和未核实的结论带入新回合，暂不建议。

## 建议的验证先后与停止条件

1. 先使现有追踪能区分排队、预填、生成、检索、看图、核验与恢复重试；不记录全文。部署后由用户跑代表性问题，确认最大耗时段。
2. 先判 Qwen3.5/llama.cpp 对完全相同、尾部追加和早期工具变化三种前缀的真实复用，再试 JIT A/B；检查点和缓存 RAM 只在资源允许时调。
3. 若检索占明显比例，先尝试五通道有界并行；若生成轮数占明显比例，再尝试规划后预检索或并行预取。每次只变一个机制，检查答案、选图、证据绑定、错误步数与总耗时。
4. 若 `predicted_ms` 明显高于其余部分，再看 speculative decoding；若只是首屏感知慢，讨论流式进度。工具搜索、全量工具、切模型和完整答案缓存目前缺少针对这个 8 工具、4B、端侧部署的收益依据。

这些实验均待用户后续选择和部署执行。本调研没有做模型性能实测，也未改业务代码。

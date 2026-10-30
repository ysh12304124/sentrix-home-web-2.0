# PhotoBench metrics contract v2

本契约是 T006 指标正确性修复的兼容性基线；不补齐 vLLM 专项指标。

## 2026-10-27 不可回答题的媒体指标

`answerability=insufficient_evidence` 与旧值 `unanswerable` 均排除在普通媒体交付、候选检索、图片/视频、micro/macro 和排名指标的分母之外；拒答质量评分继续统计。部分可回答题保留原指标资格。
新执行结果及历史逐题只读投影使用 `retrieval_metrics_applicable=false`、`retrieval_metrics_reason=unanswerable`，各项 P/R/F1 为 null，排名与计数指标不适用。界面显示“不适用”，导出保持 null；记录的 GT、实际候选、交付媒体和工具轨迹保留，定位诊断引用不作为答案 GT。历史原始文件及快照不重写；已冻结的历史汇总不批量重生成。

## 2026-10-22 采样与分量观测补充（memory_observation_version=1）

新增可选 `memory_observations`（来源ID到观测事实）、`memory_dependencies`（指标到必要来源ID列表）、
`memory_observation_version=1` 与 `sampling_diagnostics`。来源事实包含 `source/status/reason`、
`observed_at_monotonic`（开始读取时刻，保守计龄）、`finished_at_monotonic`、`ttl_seconds`、
`cache_hit`，进程来源另含PID及启动标识。nvmap缓存复用原观测时间，不以返回时间刷新。
合计依赖最老的必要分量；过期/缺失/进程身份变化使该分量及其依赖合计为null，其他有效分量保留。
明确不存在的辅助分量可为0，但仍依赖新鲜topology观测；空依赖不可证明有效；历史无version不补造时间，新采集严格验证在domain集中执行，
写入、读取及汇总共用。系统内存独立观测，不因GPU来源失效而清空。

`sampling_diagnostics`记录锁等待、三个provider耗时、采集总耗时、计划延迟、错过周期及上次回调耗时。
采样按单调时钟计划，超预算跳过错过的周期，不忙等追赶；所有实际点及超时事实保留。
阶段内滚动聚合由单个可停止的发布线程读取全样本快照执行，不在采样锁中计算，不建立无界队列。
原始JSONL仍逐点写入；写回调失败记录原因，不能静默宣称保存完整。`gpu_metrics.sampling_integrity`记录callback/publisher/collection失败及原因、missed_intervals和stop_pending_threads；正常停止后才发布任务终态，若共同等待10秒仍有线程则明确记录停止未确认（telemetry_live.status=stop_pending），严格验收失败，不把遥测增强改为终止生产任务。成功停止为stopped。
可选探针缩短预算，进程扫描利用冻结拓扑；内核单次不可抢占IO仍如实记录超预算，不宣称硬实时保证。
失败/取消未走完采样阶段时以gpu_metrics.status=partial保留已有统计，前端不得清掉partial标记。四方法端口和生产fail-open语义不变，无新部署开关；3秒覆盖门槛不放宽。本轮仅本地修复，不启动真实测评。

## Additive interfaces

- New runs persist `metrics_contract_version=2`, `build_revision` and an immutable,
  secret-free `runtime_snapshot`. Model, Sentrix and telemetry clients use this
  snapshot; changes to saved configuration affect subsequent runs only.
- TelemetryProvider retains its four existing methods and status envelope.
  Optional data describes `memory_accounting`, `memory_complete`, raw memory
  components, power rail/scope, observation age and freshness. Unknown numeric
  components remain null; incomplete totals remain null. Explicit absence is 0.
- Samples preserve independent sources when another source fails. Stream values
  expire after three source periods and are never re-stamped as new observations.
- Product model-call traces add optional tool-argument parse counts and outcomes.
  They contain execution facts, not benchmark judgments or raw prompts.
  **2026-10-26:** raw prompts are available only through the opt-in
  `include_debug=true` turn request, as `trace.debug_calls` (actual messages,
  actual tools, raw content/thought/tool calls). The flag is off by default;
  PhotoBench turns it on for evaluation. Absence is not an empty model call.
- Native tool-argument JSON parsing has its own denominator; plain-text answers
  are not JSON attempts. Legacy JSON-action metrics remain available for legacy
  structured-action traces. Missing observations are not inferred successes.
- v2 item `wall_clock_ms` is Agent elapsed time excluding Judge. Phase throughput
  uses measured monotonic wall time, including overlap, never sums item latency.
- Optional build identifiers are exposed through existing config/diagnostics
  responses. Existing URLs, error envelopes and required fields remain unchanged.
- New answer-quality Judge results add optional `judge.consistency_version=2`.
  Consistency checks must stay within punctuation-delimited clauses and must not
  mistake question answerability or the reference answer for praise of the model.
  A legitimate zero remains zero; contradictory praise still triggers one retry.
  Unversioned historical Judge records retain version 1 semantics. Missing scores
  cannot be recovered from an earlier raw response when retry facts are absent.

## Accounting and historical compatibility

Current local Jetson system RAM (`linux_physical_nonfree_including_cache_v2`)
is MemTotal - MemFree, including resident reclaimable caches and physical zram
storage. MemAvailable remains an independent available-capacity estimate; it is
not the complement of this used value. Historical `host_all_processes` system
values and NVIDIA retain MemTotal - MemAvailable.
Jetson model=smaps RSS+nvmap; product=related PSS+main/auxiliary nvmap. These are
attribution/allocation estimates. CPU-mapped device allocations and shared GPU
buffers require driver-specific overlap validation; no disjointness or exact
physical-page deduplication is claimed. Positive VmRSS residual is a labeled
fallback only. NVIDIA host RAM and device VRAM remain distinct.

Current Apple `memory_accounting=apple_footprint_resident_v2` uses physical
non-free system RAM: hw.memsize - free_count * page_size (scope
`apple_physical_nonfree_including_cache_v2`). Resident caches and the compressor's
actual physical storage are included; disk swap is not added. Model and product
are independently observed PID groups, each using footprint's deduplicated
summary.total: Dirty - Swapped + Clean + Reclaimable, normalized to bytes from
unit/bytes-per-unit. Swapped includes compressed and disk-swapped logical pages;
wired is a dirty subset and is not added again. This is a tool-attributed
resident estimate. Compressor physical bytes remain system-only because exact
per-process physical attribution is unavailable. Shared kernel/user wired
objects and driver attribution remain tool limitations. Model/product values
are not obtained by adding per-PID footprints or mapping lengths. Device GPU
counters remain raw diagnostics and are not summed into these three curves.

Both Apple groups check complete PID membership and creation identity before
and after collection, preserve original times/components in existing optional
memory_observations, and obey the existing 3-second TTL. Timeout, permission,
partial JSON and group changes produce null. No cropping to system RAM occurs.
The envelope remains contract version 2; distinct accounting, unit and scope
identifiers version the formulas. API shapes and the four provider methods stay
unchanged. Read/API/summary/export use recorded versions. Legacy Apple
`apple_capacity_estimate` keeps footprint + mapped weight length as a capacity
estimate, and active+wired+compressor as its platform-specific system metric.

Historical files are not rewritten. Deterministic read projections may be
recomputed from recorded facts, using the declared contract version. Missing
attribution, parse observations, timing or samples cannot be reconstructed.
P95 uses nearest-rank; mean is sample mean and peak is sampled peak. Statistics
use valid full-resolution observations before display downsampling. Read and
write projections apply the same value validation.

## Acceptance

The regression report distinguishes metric correctness from full-chain success;
it does not redefine `run_valid`. Four devices run the same committed release,
dataset and 10 QA in full mode. Every service is local except cloud Judge. Each
required dependency is exercised before starting; all recorded failures and
Judge denominators remain visible. General telemetry remains optional.


运行拓扑冻结同样覆盖产品重启：bootstrap按run快照构造subprocess/systemd控制器，
以内容哈希寻址、原子发布的只读runtime_connection.json作为既有PHOTOBENCH_CONFIG_ROOT。
实际启动脚本不能重新读取后续全局配置。控制记录同时保留无密钥拓扑；同模型但嵌入拓扑不同
不得视为already_pointing_here。旧未注入快照入口保留；端口方法和公开schema不变。


2026-10-22真实10QA进一步确认预算事实位于runtime_turns[*].agent2_trace.budget_outcome，
题目顶层无该字段。步数内完成率必须按实际回合读取；多轮题全部回合满足终止原因、非空答案、
状态与预算才算完成，已知失败优先于其他回合的缺失。只有事实缺失时保留null，不回填预算。
独立验收从回合原始事实复核完成率、工具参数JSON分母及阶段墙钟吞吐，防止顶层夹具掩盖真实结构差异。

2026-10-22补齐前端实际投影：阶段快照的峰值回退只接受有限数值，null/布尔/空串不转0；
降采样历史不得冒充缺失的完整阶段峰值。实时行与阶段行优先使用memory_accounting，
Jetson显示RSS/nvmap/PSS各分量及板级/轨级功耗，Mac显示容量估计与active+wired+compressor；
旧无指标定义的历史source仅用于兼容。生产schema与失败语义不变。

2026-10-22设备源新鲜度补充（device_freshness_version=1，可选投影）：
设备读数在采样持久化时按同一单调时钟重新计算age，不能沿用gpu_stats返回时的age。
新增可选device_observation_ttl_seconds（流源为三个真实刷新周期），并保留device_observed_at_monotonic。
TTL失效仅清空设备数值及轨道读数并标source_status.device=unavailable，保留有效进程/系统内存。
历史已有完整单调时间戳时允许按记录TTL派生；旧缺TTL用明确标注的3秒兼容门槛，
派生结果标device_freshness_version=1，原始文件不改写。没有时间事实时不补造新鲜度。
独立验收从采样时间减源观测时间复核age和TTL，禁止仅信任已保存的age。

## Judge score标准化v1与验收分维（2026-10-22）

新增可选字段`score_normalization_version=1`、`score_normalization`（rule_version/input_type/status/converted/score）、`attempts`（每次input/raw_response/raw_text/解析及一致性事实）。只从score严格解析ASCII十进制数字字符串，接受有限整数0/1/2；bool/null/空串/非有限/小数/越界/混合文本拒绝。reason不用于猜分。真正矛盾最多重问一次；失败保留全部已收到响应并无分，不能用首次分数回退。

历史原文不改。只凭明确保存的score或无歧义raw_text生成`score_derivation`（规则版本、源、源文本SHA256及派生分），磁盘原score/status不改；API派生投影score为标准分并附original_score，缺失重问事实或错误收尾不补造。API派生/汇总/前端采用domain同一规则，派生不代表原执行评分成功。

独立验收分服务/指标、任务表现、评分证据：受控恢复须有既定异常类别和后续成功轨迹；真实任务失败单列，未知终态、漏题、丢记录、算分错误归服务缺陷；OOM/断连/重启单报基础设施。10QA全部有终态，评分完整仍要求10/10，不补0；均值注明有效覆盖分母。错误率报告分子/分母和对照版本，不为通过调整阈值。3秒内存覆盖及TTL门槛保持。

Judge一致性v3进一步限定为明确“模型/助手的回答/答案/结论”或分句开头针对回答的肯定断言；参考答案已回答正确地点而模型未能作答的合法0分不重问。真正模型回答正确却给0仍重问一次；v1/v2历史语义保留，新执行consistency_version=3，标准化v1不变。


### Jetson 核心采样关键路径（2026-10-22）

9dad22b 实际 AGX 导入仍出现3.493秒缺口。继续定位发现gpu_stats先读主模型PSS，
process_memory又对同PID读完整PSS/RSS；设备探针不再重复遍历smaps，唯一的新鲜进程
观测归process_memory。可选llama.cpp /metrics各IO超时从2秒收紧至0.2秒，繁忙时KV为
不可用，不保留前次值；不改变主模型HTTP请求超时、内存公式、源TTL或严格3秒覆盖门槛。
这是Jetson适配器内部修复，无公开schema、端口、装配点或部署配置变化。真实HTTP繁忙
测试确认在服务端任务结束前返回不可用；完整采样测试确认每PID仅一次smaps遍历。
影响范围仅NX/AGX；固定新提交、完整CI后这两机新scope full10QA。3090/Mac继续使用
9dad22b正式证据及该版本前端产物，不将旧run改标新SHA，版本分别追踪。


## 2026-10-22 执行与构建契约（新增可选字段）

创建时冻结拓扑与预算，实际 HTTP 与遥测共用同一快照，排队后全局配置修改只影响新 run。reuse 也执行产品同步与诊断核验；resume 使用原运行依赖事实并记录 `snapshot_source=resumed_run`，兼容旧记录时明确 `legacy_resume_current_config`。自动重启限部署登记的本机API。

build 不解析题库或 Judge 配置，题集为空、题数0、结果空列表；评分和运行评分有效性未适用，`run_valid=null`（保存、读取、列表、前端一致）。`scope_build={status,embedding,source_run_id}` 为新增来源事实，只有身份种子/导入/处理全部完成才 ready；已知嵌入配置变更或构建未完成的 scope 不得 reuse。历史无该字段的 scope 保留兼容语义，不补造构建证据。

`model_identity={status,raw_id,display_name,candidates,reason,observed_at}` 保存实际端点身份；只去除明确权重文件扩展名，不截断 Qwen3.5 等型号。多候选且无明确 ID 时保留未知，不选第一项。

`execution_budgets`、`product_settings` 和部署诊断中的有效预算/provider 都可追溯。POST 发送后的结果不确定会记录 `fatal_error_details`/相位 `request_failure`，不自动重放；旧服务无进度协议时，同步导入未返回期间只展示目录、目录计数和等待时长，媒体完成数与失败数未知时保持 `?`，不得用 `0` 伪造进度。

导入阶段墙钟以 `photo_import.total_seconds` 为准，兼容历史 `upload_seconds` 或完整起止时间；`timings.total` 只作为“后端计时”单列，不能替代请求墙钟。真实 Mac 记录为墙钟1208秒、内部计时738.1秒，二者范围不同。前端阶段耗时缺少完整起止事实时，`null`、布尔、空字符串和负数保持不可用；真实数字0保留原有起止时间回退语义。本次只改前端投影，不改原始记录、后端公式、执行或公开schema。

本轮共用执行语义变化，固定版本后四机 full10QA，并在 NX/Mac 各做独立 build→reuse10QA。build 的严格遥测验收只覆盖导入/处理；reuse 只覆盖 QA，未适用阶段不补点。单次及首尾3秒门槛、null与TTL、三类验收结论、历史保全均沿用。

输入初始化失败：`failed_phase="initialization"`，`qa_count=null`、`input_integrity=null`表示无法获得输入事实；保存真实failed记录且不调用产品。build正常初始化的题目数仍为0，验收不得要求10题；评分不适用不豁免遥测覆盖。

## 长期运行读取与提交（2026-10-23）

新增可选 `RunRevisions` / `RunReadViews` 端口，旧 `RunStore` 和写端口保留。
`GET /runs/{id}` 增加 `include_history`（默认 true，兼容旧客户端）；false 只返回最近成功提交的检查点摘要、实时窗口，不读原始遥测全文件。响应可带 `read_revision` 和 `history_mode=full|checkpoint`，表示本次读取版本和统计来源；checkpoint 不能冒称全历史重算。前端普通轮询用 checkpoint，完整曲线可手动读取；分析展开才加载。

新快照先写 `.snapshots/<generation>/{run.json,results.jsonl,metadata.json}`，文件和目录 fsync 后原子发布 `snapshot-commit.json`。该指针选择的一对文件为权威；两个根文件保留旧格式兼容导出，不能把两次 rename 当事务。提交前失败保留旧提交；提交后导出/清理失败记录日志，须从指针选中的版本修复，不回滚已成功提交的数据。根目录兼容导出只适合离线工具，运行中一致读取必须遵循提交指针。保留当前与上一提交；旧历史原文不迁移，旧目录仅新增可重建的文件版本索引。进程内按 run 的有界条带锁协调读写、删除与清理；仍要求单实例目录租约。

持久化快照持状态锁复制，IO 在锁外。等待提交/停止有共同截止，失败不记成成功写入；即使磁盘失败也发退出信号。未退出明确 `persistence.status=stop_pending` 并阻止新轮和删除；写盘异常为 failed，日志保留原因。正常结束释放执行体与线程注册。终态缓存按修订失效，同时限制条数/字节；删除后不得继续返回缓存。

写盘失败的诊断可能无法写回同一块已满磁盘。此时进程内瘦索引保留失败原因，详情以 `persistence.diagnostic_source=current_process` 和 `checkpoint_status` 明示运行状态比最近提交更新；`read_revision` 仍指最后成功的检查点，原文件不伪改成已保存。进程退出后的状态由原有备份恢复规则接管。首次历史索引尚在构建时前端展示 scan 状态并继续轮询，不把部分列表当全部结果。

## 导入观测投影（2026-10-23）

`phases.photo_import.progress`可选operation_id、availability、observation、last_received_at、last_checked_at、query_error、request_status/request_error、observer_stop_pending；observation为产品通用原事实（operation_id/scope_id/status/stage/total/completed/imported/deduplicated/failed/current_file/sequence/updated_at/observed_at/error），不从manifest补数。每目录最终progress保存在directories条目。availability不可用时保留旧观测和原接收时间，前端明确为上次事实，不冒充实时。total=null不转0，媒体100%不代表批末或整个阶段完成。同步POST返回成功与独立观测状态单列；POST结果不确定时不盲重发，也不宣称产品已停止。查询故障不自动使已返回的导入失败；观察记录不能保存或观察线程停不下属于测评服务故障。

前端进行中阶段按started_at显示持续墙钟，终态仍用保存的真实墙钟。产品步骤updated_at不等于采样心跳，query observed_at/本地last_received_at用于区分查询断连与长步骤；遥测值龄门槛不套用到已结算计数。产品进度内存有界、重启不可用；PB已保存观测不删除。

终态导入新增可选`scanned_count`，只在产品响应提供完整合法的新增/去重/失败计数时计算；它表示实际已结算的扫描媒体总数，`total_media`仍保留manifest预期数量。前端新记录显示完成/实际扫描、新增、去重及失败，旧记录缺事实时不重算分母。进程内保存失败投影同时携带真实结束时间/错误，终态页面不持续累计旧running检查点的墙钟。


### T006 Judge实时进度（2026-10-23）

QA阶段的规范进度存于`phases.qa_eval.progress`：`agent_completed`为已终态题数（含明确任务失败，不含scheduled/running），`completed`保留成功完成题数；`judge_completed`为已结束的Judge题数（含评分失败，不含跳过），`judge_total`为应评题数，`judge_skipped`单列。有效分数仍由统一评分规则统计为`summary.judge_valid_count`，合法0分有效，失败不补0。新执行在持锁检查点同步items和进度；阶段外层agent/judge计数保留兼容并与内层相同。未配置Judge时内层Judge计数为null、并发为0，前端显示未配置。

Agent和Judge仍使用独立线程池，协调器等待任一池完成即提交结果，不再等全部Agent结束才回收Judge。恢复运行从已有明确Judge终态初始化计数，跳过事实单列，不重新请求既有评分。Agent阶段墙钟在最后Agent结果返回时结束，不包含等待Judge尾部。前端阶段卡和列表共用同一读取函数，优先内层、缺失时兼容外层；没有记录显示?，不以0替代未知。历史原文不改写，旧运行自身未更新的计数仍属于旧检查点事实，不能据有效评分数补造完整Judge终态。

复现测试覆盖一题Agent阻塞时另一题Judge的合法0分、返回失败和抛异常及时可见；resume既有评分、失败/跳过、取消未开始题不假报完成、可空投影及历史外层字段UI。仅本地验证，未触碰四机或启动真实测评；后续发布按公共执行路径影响验证。


## T006 2026-10-23 调用事实与清理状态

详细契约及测试边界见[取消、调用事实与采样优化](../refactor/2026-10-23-cancellation-trace-deviations.md)。domain端口、application编排、infrastructure探测及bootstrap装配分层不变；产品同步导入配合协作取消，不引入持久队列。停止全部只请求测评相关任务停止，pending与worker真正退出分开。原始历史、null、TTL和3秒覆盖门槛保持；新运行的L2调用事实纳入真实模型调用统计，旧记录不补造。四机更新前重新核验占用，只激活授权8291/8872。受影响设备真实回归须使用新SHA，不能复用旧run标成新版。

## T006 2026-10-24 运行终态汇总规则

运行总状态沿用八种：pending、running、cancelling、completed、completed_with_errors、failed、cancelled、interrupted。阶段已有done/partial等状态保持，不增加细分类状态。domain.runs.execution.qa_completion_errors集中验证完成事实，application在Agent/Judge池回收后调用；不在API、前端或基础设施决定业务终态。TelemetryProvider、装配点、模型调用及外部schema均不变。

- 必需流程结束、每个QA具有唯一且匹配的终态记录，应评样本具有合法有效分数，且没有最终Agent/Judge失败：completed。合法0分、低质量、合理澄清/拒答以及已恢复的中间错误不单独导致失败。
- 部分样本Agent失败/超时、Judge最终失败（包括任务/证据子评分或既有多轮记录中的明确失败）或应有回答质量评分缺失：qa_eval=partial；总状态completed_with_errors。阶段failed_count统计失败样本，一题只计一次；error_details沿用既有sample_id/status/error_type/reason，Agent和Judge原因分列。不补0，不减少总样本分母，不为等满分子重复入队。
- 缺失/重复/意外QA ID、缺少Agent终态属于结果完整性缺陷，抛出错误使该阶段及总状态failed。原始异常记录保留。显式取消优先走RunCancelledError/cancelled，尚未开始题仍scheduled，不能误记为失败或已完成。
- 未配置Judge时评分不适用，不因占位分数缺失判错误；build不执行此QA规则。重试仍有界，以最终结果判断；历史attempts中的已恢复错误不计最终失败。run_valid不作为新验收结论；在QA收尾前先清除旧成功标志，完整性/最终评分错误时保持false，避免原始记录留下成功值。既有质量/召回/遥测公式及采样门槛不变。
- 只影响新版新执行及resume生成的新run；既有run原始状态和SHA不改写。重判仍保留独立操作记录，不反改原运行当时的状态。列表xx/yy继续使用有效回答质量评分样本数/本轮样本数，因此可出现completed_with_errors与9/10，也可在子评分失败时显示10/10。

本地验证包含先失败复现、domain完整性/计分边界、完整执行正常收尾/落盘/列表投影、取消及前端停止轮询；完整make ci固定提交后执行。发布只在无受管工作设备激活8291/8872，忙机仅暂存；共用执行逻辑的新版真机回归另行记录，不以本地假产品用例或旧run替代。

### 2026-10-24 真机回归缺陷修复

- 产品回合的`partial`不能单独判断成败。新执行完成检查同时读取每个最终回合的termination_reason/status/error；预算耗尽、解析/模型失败等最终失败进入completed_with_errors。answered_with_gap、合法0分及中间调用成功恢复保持原语义；多轮题任一最终回合失败须保留。一题仅计一次错误，未结束的产品回合属于完整性错误。execution_status=completed仍表示测评执行已收尾，不再替代产品任务成功。历史原文不改写。
- Jetson拓扑探测使用独立单任务槽、有界等待，保留发现的原始起止；继续使用的前次已完成发现不刷新时间，超过原TTL照常失效。监听定位每次核验当前socket签名及持有者启动标识，不因固定2秒到期重扫全机fd；进程退出/监听变化仍重新定位，子孙进程仍重新发现。内存公式、源TTL、采样覆盖3秒门槛均不变。
- 独立验收规则v4额外检查原始总状态、API状态和逐题分类/评分/阶段部分失败的一致性；能够识别受控失败并不自动证明运行总状态正确。

2026-10-24负载实测补充：AGX视觉推理期间当前smaps_rollup读取会超过0.6秒，旧等待窗口会生成不完整采样。内存探测单次有界等待改为1.5秒（最多4并发，拓扑仍独立最多等0.1秒）；只等待真实读取，不回填先前内存值，超时仍null并登记pending线程。源TTL与严格覆盖3秒及全点完整要求保持，发布后必须重新做真实full验证。

同轮取消专项发现并修复套件线程归属：串行套件工作线程同时按各run ID登记，active-work不再把仍在执行的导入报告为worker_alive=false；显式cleanup只能在拥有该run的线程真正退出后执行，不能与执行/采样/写盘并发收尾。同步导入取消仍等待已开始的工作协作退出，pending期间继续保留归属。

2026-10-24拓扑值龄补修：真实负载进一步暴露0.1秒拓扑等待会复用接近TTL的发现，内存读取结束时拓扑过期。拓扑有界等待改为最多1秒；若刚收取前一回调的已完成任务，在同一截止时间内再启动本次发现。拓扑与最多4个smaps探测共用2.5秒总等待窗口，保留真实源起止，超时或过TTL仍null；3秒TTL和严格覆盖/全点完整要求不变。

2026-10-24继续负载修复：AGX内核实测不提供task children接口，4fc8d0f的服务树优化会回退完整扫描，未据此宣称改善。端口限定发现改为调用本机ps仅取PID/PPID，由原生进程扫描父子关系，避免Python线程逐个打开数百个无关stat时争用同一进程GIL。之后仅对相关服务树读取stat/内存并核验父子关系、启动身份及当前端口归属；ps异常/超时退回完整祖先图，显式PID/关键词及legacy仍走完整发现。拓扑等待1秒、拓扑与内存总等待2.5秒保持，不增加TTL、不重标旧时间；完整性及3秒覆盖仍需真机full验收。

2026-10-24并行发现收取修复：若拓扑在初始1秒窗口未完成，会继续与smaps并行；内存读取结束后在同一2.5秒总窗口的剩余时间内等待并收取现有拓扑（不再启动任务、不扩展总预算）。只有前后PID/启动身份/父子关系/监听端口/命令身份完全一致，才采用这次真实拓扑及其新VmRSS；PSS和smaps RSS保留各自真实原时刻。成员或身份变化使当前样本明确不可用，下一回调按新成员探测；不把旧内存重新标时，不放宽2.5秒总预算、3秒TTL/覆盖或每点完整要求。

2026-10-24并行等待补修：已有服务树时拓扑发现零等待启动/收取，立即读取当前内存，随后只用2.5秒总窗口剩余时间收取拓扑；冷启动仍最多先等1秒。若前回调任务在内存读取期间才完成，槽位释放后可在同一截止时间内启动当前发现，最多一任务、不积压。归属核验与源真实观测时间不变。拓扑观测新增可选discovery诊断（原生/回退路径、原因、父子快照/监听/总耗时），仅记录运行事实；3秒TTL、覆盖和全点核心内存完整要求保持。

2026-10-24整轮读侧补齐：`benchmark_process_gpu_memory_mib`同时进入实时、整轮全样本峰值与统计聚合；读取完整曲线不能丢掉已记录的测评系统GPU峰值。统计先于240点抽稀，不能从抽稀曲线补峰值；历史仅从已有真实采样生成读侧投影，不回写旧run。

2026-10-24监听核验补修：首次发现保存监听socket对应的FD；后续每次重新读取当前监听表、PID启动身份及该FD的readlink，三者一致才复用归属，避免遍历相关进程全部无关FD。FD关闭/复用、PID变化或监听集合变化仍完整重新定位；缓存不刷新源观测时间，不变更TTL和覆盖要求。


### 2026-10-24 Jetson受管进程探测

Jetson正式采样的核心进程内存读取移入单个spawn子进程，与HTTP完整曲线读取和全样本发布分离。
可选ManagedTelemetryWork由GpuSampler启动前准备、采样线程finally关闭；四方法原端口不变。
同一时刻最多一个请求，超时不追加请求、不复用前次值；迟到结果保持原观测起止，domain仍按3秒TTL验证。
关闭管道后子进程结束当前读取并退出；未退出PID经OwnedTelemetryWork计入stop_pending，不能冒称回收完成。
仅创建与关闭本轮探测进程，不启停共享模型。探测进程作为产品后代参与PSS归属；使用当前绑定解释器，无新增依赖或部署开关。
直接单步sample_once保留进程内诊断路径；正式start始终使用受管进程，启动失败明示unavailable，不静默回退。

## 派生错误分析（规则版本1，2026-10-24）

原质量、行为、检索和遥测公式不变。新增错误分析分别报告异常事件数、受影响题数、最终执行失败题数、明确恢复题数，题级/轮级/调用级分母独立；缺失和未覆盖保留未知。按角色、轮、步骤及重试ID保留证据；文本识别注明依据。两轮匹配以QA ID和定义指纹为准，配置不可比时不汇总提升。来源SHA256、过期检测、离线导出和可选AI约束见[错误分析设计](photobench-error-analysis.md)。


### 2026-10-28 显式导入与流水线进度

新链中 `photo_import.execution_mode=explicit` 仅统计资产登记；`pipeline_processing.progress` 的可选 `stage=media_processing|finalizing|completed` 和 `request_status=running|returned` 表示媒体计数与同步请求的状态。processed、failed、skipped 为互斥计数。媒体计数达到 total 不代表汇总请求已经返回；阶段终态仍由真实响应决定。历史没有这些字段时保留原计时，前端只调整处理详情所在卡片。查询失败保留上次观测，不能由缺失值推断完成。显式请求取消后的活跃本地线程继续报告 stop_pending；尚无远端 operation_id 中断接口。


## 2026-10-30｜首屏主模型内存与显存

关键指标固定六项，独立展示主模型内存采样峰值（`model_process_system_memory_used_mib`）与主模型GPU分配采样峰值（`model_process_gpu_memory_mib`）。历史NVIDIA数据没有新资源协议时，`model_process_memory_used_mib`只作为原显存口径展示，不复制成主机内存。后端采样/聚合字段与原始曲线保持原样。

Mac运行时未给出主模型GPU独立归属时，该卡保留并标“未记录”；整机GPU分配峰值只能在说明中标为参考，不放进主模型数值。Jetson GPU/UMA分量保留原采样来源说明；统一内存/UMA分量与进程内存可能重叠，不相加。缺失、非数或负数不补零；实时峰值优先使用完整采样统计，截短曲线不恢复缺失的完整统计。布局随视口采用六/三/两/单列。

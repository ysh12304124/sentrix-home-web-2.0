# PhotoBench 四机固定版本部署与 10QA 验收

适用 T006；先本地完成 `make ci`、提交并固定 SHA，再发布该 SHA。四机使用相同源码和前端，保留各自配置、权重、数据、历史结果和 Python 环境。指标契约见 [photobench-metrics-contract.md](../architecture/photobench-metrics-contract.md)。本轮不补 vLLM 专项指标。

2026-10-22用户临时调整：NX暂停适配并释放本轮8291/8872、序列及reader占用，共享模型保留；仅接收独立release代码，不激活、不启动、不测试。截图相关前端修复（未知导入计数、阶段墙钟与后端内部计时分列、阶段耗时null保留）按用户明确要求不跑测试，只做构建、固定提交及制品hash核验后同步。此例外不等于新SHA通过完整CI；旧de14真实运行、后端CI与新前端产物分开追踪，不改标旧run，也不据此放宽3秒采样门槛。其余三机既有回归继续，避免为前端改动中断活跃run。

## 当前暂停边界与采样修复验证

2026-10-22用户进一步要求先修可控制的代码、硬件驱动暂缓、暂不启动测评。本次只在本地开发/测试/提交；不激活设备release、不做模型探针、不重启服务，NX继续保持暂停。前文较早“其余三机回归继续”描述仅适用于已结束的de14轮。

恢复验证时，共用采样/发布语义变更须固定新SHA并验证受影响设备；当前不执行。新的Jetson样本必须同时核验memory_observations原始起止/TTL、memory_dependencies及最老必要输入；辅助不存在的0需有新鲜topology。查看sampling_diagnostics分辨设备、进程、系统及回调耗时，查看sampling_integrity的写盘/发布/采集异常和stop_pending_threads。未退出线程不能认定释放成功，不应与旧任务并发同scope。

3秒门槛不变；单次内核smaps不能硬抢占，不通过扩大TTL或沿用旧值掩盖缺口。本地门禁与真实采样分别报告，旧run版本和原始文件保留。

## 设备与依赖

资源采集工具按[固定版本清单](telemetry-tools.md)和`apps/photobench/telemetry-tools.lock.json`配置；psutil/nvidia-ml-py使用包声明的精确版本，macmon保持0.8.2，系统工具随对应OS/驱动基线保存。本轮只读记录版本，现有运行环境保持；后续部署核实际版本并将差异记入回执。

默认端点指设备自己的 `127.0.0.1`；产品8291、PhotoBench8872。仅云端 Judge 可访问外部模型。2026-10-21现场核验AGX主模型仅绑定本机192.168.0.118，用户明确批准保留该同机LAN端点，不重启模型。

| SSH | 主模型 | 文本嵌入 | 图片嵌入 | 旧新栈根目录 |
|---|---|---|---|---|
| 153 | 8100/v1 | WeMM8102 | WeMM8102 | /home/asus/xy/sentrix-home-web-2.0 |
| nx46 | 8100/v1 | 8101 | 8103 | /home/nx/xy/sentrix-home-web-2.0 |
| orin118 | **192.168.0.118:8103/v1（同机）** | **18101** | **18103** | /home/orin/xy/sentrix-home-web-2.0 |
| mac158 | 8100/v1 | 8101 | 8103 | /Users/saas/xy/sentrix-home-web-2.0 |

模型不同，不能解释为同模型性能横评。四机没有 Git 元数据，运行版本由源码及前端 SHA256 清单、实际 Python 导入路径、服务回显和 run 快照共同证明。

## 本地开发与打包

```bash
# 仓库根；包括全量测试、≥85%覆盖率、两个前端构建、两份OpenAPI
make ci
git add <已检查的代码、测试、文档>
git commit -m 'fix(photobench): correct cross-platform metric accounting'
# 在最终提交上再次通过门禁；不得用跳过失败测试代替
make ci
.venv/bin/python scripts/deploy/photobench_release.py package --destination /absolute/release-output
```

输出 `<SHA>.tar.gz`、包校验文件和解包目录。包内包含 `build-info.json`、逐文件 `release-manifest.json` 和统一构建的两个 dist。打包要求工作树干净且 dist 存在。不将配置、密钥、venv、权重或历史结果打包。

本地磁盘不足时，真实嵌入CI fixture可通过临时测试桥访问已登记的真实模型，须在验证报告明确说明。正式四机回归禁止沿用此桥。

## 发布与回滚

在远端使用独立目录 `~/xy/releases/<SHA>`，运行绑定保存在 `~/xy/runtime/<SHA>`，不要覆盖旧新栈目录。先校验包SHA256，再解包。首次激活要求旧8291/8872仍在运行以捕获有效环境。

```bash
# 远端现有venv内的Python；以下路径需替换为实际绝对路径
<old>/.venv/bin/python <release>/scripts/deploy/photobench_release.py verify --root <release>
<old>/.venv/bin/python <release>/scripts/deploy/photobench_release.py activate \
  --root <release> --previous <old> --runtime <runtime>
```

激活器复制配置到受保护的运行目录；原配置不改。仅已确认属本机的LAN地址可规范为loopback；真实远程依赖拒绝。旧data、datasets、results、venv显式绑定，两个dist和PYTHONPATH指新release。所有相对数据路径均相对旧根解析。

AGX激活额外指定`--local-model-url http://192.168.0.118:8103/v1`。该显式例外必须通过本机接口地址核验，不能换端口；记录于topology-adjustments并加入NO_PROXY。preflight仅对登记的主模型地址接受此例外，其他依赖仍要求回环。该地址不能改成其他设备的LAN地址。

发布前检查 `product` 清单：独立Qdrant必须有本机监听；进程内嵌入式Qdrant无需6333服务，确认实现后从新配置副本移除该独立服务声明，不能把探测失败当不存在。主模型和文本/图片嵌入端口均从URL推导，不能再维护一份遥测端口文件。

激活器只核验并停止8291/8872的uvicorn，活跃run拒绝发布；不管理模型服务，不动8091/8191/8771/8772。人脸固定CPU，自动线程上限在数值库导入前生效；0表示不限制。权重必须预先本地存在，正式运行设离线模式。启动后等待两个服务回显同一SHA，保存日志、PID及activation记录。

API启动后将其真实PID、模型端点和无密钥runtime_connection登记于`runtime/sentrix-process.json`；PhotoBench通过既有
`PHOTOBENCH_SENTRIX_PID_FILE`读取该记录，`PHOTOBENCH_SENTRIX_LOG_PATH`指向同一runtime的`api.log`。
full run模型和嵌入拓扑一致时不能再次重启API；源码release内不保存这些可变控制文件。
如需重启，按run快照在runtime/startup-snapshots生成内容寻址只读配置，并传给启动脚本；全局配置后改不影响本run。systemd同样绑定只读快照，旧入口兼容。

2026-10-21用户批准的第二个现场例外：NX既有CLIP8103的CUDA内核不兼容，健康200但编码500。
原权重离线CPU验证图片及文本编码通过后，仅该侧车按显式授权重启为CPU；8100主模型及8101文本嵌入不重启。
该变更不由产品应用管理，部署证据另存，四机同版本重新执行完整测评。

```bash
<old>/.venv/bin/python <release>/scripts/deploy/photobench_release.py rollback \
  --root <release> --runtime <runtime>
```

回滚切回bindings中记录的previous源码并重启8291/8872，保留当前release、激活记录和全部结果。启动失败先读运行目录的api.log/photobench.log；不修改远端源码修问题，回本地修复、提交新SHA、重新通过CI和四机完整回归。

## 启动前门禁与正式执行

```bash
<release>/.venv/bin/python <release>/scripts/deploy/photobench_acceptance.py preflight \
  --root <release> --runtime <runtime>
# 汇总四机preflight，确认dataset/QA/GT哈希完全一致后再执行
<release>/.venv/bin/python <release>/scripts/deploy/photobench_acceptance.py run \
  --root <release> --runtime <runtime>
```

preflight要求版本一致、完整diagnostics正常、没有活跃孤儿；核对有效产品环境与PhotoBench配置；检查实际本机监听进程，拒绝代理/重定向/远程卸载；分别执行主文本、主视觉、文本嵌入、图像嵌入真实请求并校验向量维度及有效值。核对显式人脸provider，在独立进程实际推理本地RetinaFace、SCRFD、ArcFace权重，记录哈希和profile里的实际kernel provider；请求CUDA而实得CPU必须失败，明确CPU合法。存储使用本机数据路径，不下载权重。Judge采用独立配置，不把密钥写入报告。

NX文本8101现为BGE-M3 q8_0 GGUF、llama.cpp pooling=none；产品和独立预检均须实际执行有界协议协商，不能用`/health`200作为编码成功。原生token矩阵使用官方CLS+L2，报告`text_embedding_protocol=llama_cpp_bge_m3_cls_l2`；禁止mean pooling或重启模型规避协议问题。Mac/AGX原Python侧车继续使用`/embed`。

run保存唯一请求和响应，`album3-14 / compact-10q / mode=full / models=["__current__"]`，创建新scope，QA并发1、turn_timeout900秒、import_timeout14400秒。每次尝试使用独立runtime记录，旧请求与结果不得覆盖。需要重试时明确记录原因和前一run，不能挑选或删除失败题。

完成后：

```bash
<release>/.venv/bin/python <release>/scripts/deploy/photobench_acceptance.py audit \
  --root <release> --runtime <runtime>
```

独立报告分别给出`service_pass`（兼容`chain_pass`）、`metrics_pass`、`tasks`、`scoring_complete/scoring`、`infrastructure_errors`，不以run_valid替代。10题全部调度且终态可核查；受控恢复须有既定类别、压缩事件和同一步后续成功调用，明确预算/解析/模型失败收尾单列任务失败，未知终态/丢记录/错误计分属测评程序缺陷。恢复和任务失败不因中间error自动否定服务；OOM/断连/意外重启单报基础设施，不宣称健康。任务收尾与GT质量分开，正常需要Judge的10题仍以10/10有效评分为评分完整目标；不足注明覆盖分母，不补0。

指标须验证原始样本→保存统计→API/前端一致，分量可复算、未知/过期保持null、功耗轨带范围；导入/处理/QA均有新鲜核心内存，报告样本数/间隔/首尾与最大缺口。既定单次/首尾超过3秒门槛不变。调用错误率报告分子/分母及固定版本历史基线；不设事后任务质量容忍阈值，10QA只验证流程，不能证明低概率故障消除。

共用公式/有效性/快照/计时/Judge/主流程变更：固定新版本四机完整回归。仅平台变更说明范围后验证该机及共用逻辑；纯文档样式运行相关检查/前端构建，null/单位/指标投影需真实数据核验。旧证据注明适用SHA，不改旧run构建版本，源码/后端/前端均可追踪。

旧运行无法验证新版本时先保全结果/日志/版本，通过取消接口有界停止，确认线程池已join、active_count=0、无同scope子任务，再切换新scope；必要时仅重启8291/8872。人工取消记录替代原因并保留旧run，不能报为崩溃或最终验收。AGX保留已批准同机LAN8103，NX已批准8103 CPU侧车例外不代表可任意重启模型。

Judge标准化v1的score规则见指标契约；每次原响应及转换事实保存。历史只读派生带source/hash/original_score，不覆盖原文、不补猜分、不伪装旧执行成功。

## 口径与历史

- NX/AGX一致：整机MemTotal−MemAvailable，主模型smaps RSS+nvmap，产品相关PID去重PSS+主/辅助nvmap。分配/归属估计；nvmap不可读时正残差可作标明来源的估计，非正残差不可用。
- NVIDIA：主模型RAM按选定端口的监听PID及后代PSS；VRAM按同一PID归属。其他实例只进整卡/其他进程统计。
- Mac：整机active+wired+compressor为平台特定统计；footprint、映射权重和容量估计合计分别展示，不宣称精确物理驻留。
- source退出或超过三个刷新周期后读数失效；单来源失败保留其他有效来源。null不是0，必要分量未知时总量为空。
- P95用nearest-rank，完整有效样本先统计后降采样；均值为样本均值、峰值为采样峰值，保留首尾及阶段边界。
- 历史原始结果不改写；仅从已记录完整事实做带版本派生。历史归属错误、缺失解析事实或采样不能补造。

2026-10-22最终原始数据审计发现：gpu_stats返回后，进程/系统采集可能阻塞，
设备原观测65.6秒前但旧age仍为0.53秒。采样持久化改按同一单调时钟重算age，
流源传递三个实际源周期的TTL；过期只清空设备读数并保留进程/系统内存。
历史投影从已有时间事实派生、标device_freshness_version=1，不改原文；
缺TTL旧记录采用显式3秒兼容门槛，不补造真实刷新频率。验收器独立从时间戳核验。
此修复须形成新固定SHA，四机统一发布后重新full 10QA，旧轮次全部保留。

Judge一致性规则核验：新正式run的回答质量judge必须记录consistency_version=2；
合法0分计入有效分母，缺分保持null。旧无版本原文继续v1；禁止从不完整重问轨迹补分。
任何该规则代码修复也必须新SHA、完整make ci及四机新scope full10QA，不能重判旧run代替。

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


## 2026-10-22 新轮验证与硬件环境

固定新SHA后四机full10QA；NX/Mac再各执行build→reuse10QA，所有scope独立，目标60条QA终态/有效评分，任务能力失败另报。Mac使用集中mac预设（Agent600、Chat180、QA2、导入86400），显式配置优先，QA0仍表示自动；server保持原预算。保留原run及适用版本，不能改标旧证据。

配置源为runtime_connection.json；白名单product_settings可指定deployment_profile、agent_wall_time_s、chat_timeout_s、face_providers、retinaface_providers。配置API更新仅影响新run，队列和resume保持快照。不同产品URL不授予按同端口杀本机服务的权限，只有登记本机API允许自动重启。

发布器回滚到没有`Settings.runtime_configuration()`的旧release时，预检读取旧版实际Settings的Agent/Chat预算与已有provider环境变量，不要求旧版实现新增诊断方法，也不伪报其支持部署预设。仍在停止任一监听之前核验Python≥3.11、源码导入路径及配置可构造性；旧环境损坏时先绑定独立验证过的兼容环境。此兼容修复仅影响发布/回滚工具，不改变应用执行、指标、接口或前端；真实测评证据沿用其实际应用SHA，工具提交单独追踪。

硬件环境先运行当前解释器下的runtime_acceleration_probe.py，保存实际包路径/版本/RECORD哈希、用户site、动态库和真实算子结果。可用`--torch-device cuda|mps|cpu`和`--onnx <本地权重> --provider CUDAExecutionProvider|CPUExecutionProvider`；每个探针有总超时。生产预检逐个人脸权重做实际推理并检查profile kernel provider。

新安装指定全新SENTRIX_INSTALL_VENV；Jetson还必须提供SENTRIX_DEPENDENCY_LOCK完整带哈希锁（含匹配JetPack/Python的原始wheel及SHA256）。安装不覆盖现存环境，pip check后保存runtime-inventory.json与requirements.observed.txt，后者只表示已安装事实，不能单独充当带来源哈希的重建锁。CUDA候选侧车可用自己的Python版本；不能用Python3.10运行要求≥3.11的产品包。

CUDA/cuDNN大版本必须匹配。LD_LIBRARY_PATH只允许候选进程指向已核验匹配的真实库；不能把lib*.so.12软链成.so.13。不要将两个ORT发行包共用命名空间或用户site污染视为正常加速证据；独立环境选择一个实际运行版本，并核验import与kernel。隔离候选未通过前现用服务和模型不切换。

官方依据：https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html 、 https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html 。PyPI ORT≥1.27默认CUDA13，不适合直接套用CUDA12.6 Jetson。已核验的设备事实与候选测试记录另见本轮独立报告。

前端内存说明补核版只改变标签/说明及accounting元数据投影，后端生产文件与3cb480a逐字节相同。完整CI、固定提交与统一制品后，等待各机当前验证序列终态再切release；四机重新预检，并用保留的真实API结果验证新前端的内存标签、公式和null显示。保留3cb实际运行版本与原始证据，不将其改标为新SHA，也不因此再调用60题模型。切换前先保存PID/资源连续性和日志，切换本身作为授权部署记录，不报意外重启。


#### 实时详情轮询负载回归

轮询等待必须从上一轮完成时计起，正常2秒、异常5秒；渲染或QA loader引用变化不重启计时器。
同run重叠详情读取共享在途计算；完成后下一批重新观测，错误也传播给全部等待者并清理。
验证时保留有实时详情访问的运行证据，并检查遥测最大间隔、首尾缺口、读取响应和内存压力。
若发现采样停滞，保全raw/API/日志后按严格门槛判定，不能删除异常采样或扩大3秒阈值。
本次共用读侧修正固定提交后四机full10QA，NX/Mac再build→reuse；旧失败轮保留，不按质量筛选。

T006终态归档：新版必须先结束采样与末尾观测，再同时发布终态/finished_at。
收集证据时还需确认current_phase为空、末尾快照存在、执行套件已退出并且原始文件稳定；不能只凭status=completed开始下一轮。
旧版曾存在提前终态，若两份自然收尾文件不同，保留两个哈希和逐字段差异，不能覆盖原采集证据或伪造版本。


### 2026-10-22 Jetson监听缓存按端口选择隔离

NX新版实测发现同一采样周期先查询主模型端口、再查询完整产品端口集合；旧单槽缓存相互覆盖，持续重复全量扫描/proc/fd。改为按排序后的端口集合保存独立条目，每次仍核验监听socket和PID启动标识，TTL保持2秒；清除过期条目且最多保留32份，避免配置变化造成无界缓存。不改变端口、公式、null/TTL或3秒验收阈值。回归测试交替查询单端口/多端口并复用PID，先红后绿。仅Jetson基础设施路径受影响；发布后NX重新full→build→reuse，AGX待主模型恢复再回归。153/Mac不调用此实现，原实际SHA证据继续保留。真实时序效果须由新版本运行确认，不能由缓存测试提前记为通过。


### 2026-10-23 测试分层调整

固定提交发布前仍须 `make ci` 全绿；真实资源测试独立为 `make test-real`，
保留原断言，缺依赖/skip不算通过。按影响范围运行专项和真机10QA，详见 [testing.md](testing.md)。
本次Jetson缓存只影响NX/AGX，不因测试编排提交要求153/Mac重复10QA。
旧4项WeMM依赖失败及3项地点fixture错误保留，不由拆分CI改写成成功。


### 2026-10-23 Jetson进程发现缩小读取范围

NX c3dbd09实测拓扑最慢2.295秒，核心内存有效缺口6.078秒，未通过3秒门槛；同时存在严重内存/交换压力，不能将全部延迟归因代码。端口回退发现改为只核验实际监听候选；进程清单每PID先单读stat建父子图，再仅给选中监听及当前子孙读取RSS/命令行并复核start_ticks。新子进程每轮发现、共享子孙去重；PID复用或消失不沿用旧值。无scope的旧关键词规则、本机显式PID或无端口关键词配置仍全量发现，远端local=false不触发本机扩展。公式、预算、null、缓存TTL、接口及3秒阈值不变；无新增模块、端口或装配点。专项覆盖候选过滤、新子进程、PID复用及兼容分支，真实改善须由新固定版本NX full→build→reuse验证；AGX仍等待模型恢复，153/Mac不调用该路径。

### 2026-10-23 本地长期运行修复（暂不部署）

本轮仅本地开发与日常 `make ci`，四机都在共享使用时不 SSH、不发布、不重启、不发模型请求或开测评。用户取消24小时遥测专项。后续部署固定SHA时同步后端/前端产物版本并先备份结果根；本次改到共同持久化和Judge结果提交路径，后续按影响执行真机回归，本地合成测试不当作真机通过。

新增格式见 [长期运行偏差](../refactor/2026-10-23-long-running-deviations.md)。在线读取必须按每run的 `snapshot-commit.json` 选取一代完整结果；根run/results为兼容导出，不直接编辑它们做恢复。提交后导出失败会明确记日志，修复导出应在停写后从已提交generation复制，核对两文件一致；不要重新猜分或用旧导出反覆盖。历史派生 `.photobench-read-index.json` 可删重建，原run/遥测保留。回滚必须保留提交目录、备份和原release，旧读者只读根导出，切回前校验其与清单一致。

普通详情显示checkpoint来源，完整遥测按钮按全有效raw重算后降采样；null不变0。保存异常/stop_pending不得记完整通过，也不能删除/覆盖仍有工作线程的run。Python不能安全强杀卡在内核IO的线程，未确认停止时保持冲突保护，现场核实退出后再切版本。

容量：当前代+上一代+独立兼容导出，稳定态检查点空间最多约为原先的3倍，提交前还会暂存一代；raw不缩减。发布前需据实际大run确认剩余磁盘，不能默认新格式节省磁盘。磁盘满时详情可显示current_process保存失败诊断，并注明旧检查点状态；该诊断未必已落盘，不能当原始记录被成功更新。

### 同步导入进度的运行与排查

新版PhotoBench和产品需要一起发布才能看到逐媒体观测；仅更新PB、产品旧版或多worker把查询分配到其他进程时会明确显示不可用，不以0代替。产品维持现有单worker部署，无新增环境变量、外部服务或目录配置；活跃32/终态128为适配器固定上限，GET只读内存。operation_id仅用于关联和在册重复防护，不能作为永久幂等键或任务取消接口。观察线程轮询1秒/HTTP3秒/停止5秒；UI超过10秒未刷新提示旧观测，这些是进度协议预算，不改变遥测TTL与3秒采样验收阈值。

检查GET `/api/v1/imports/{operation_id}/progress`与run.progress.observation的同operation_id/scope_id/sequence；媒体completed应等于imported+deduplicated+failed，total为实际扫描+limit后的支持文件数。processing满计数后检查事件总结、参与者、图谱、画像步骤；不要仅据无新媒体计数重启。404可能为尚未登记、旧版、重启或已淘汰，保留最后观测及查询错误。同步POST断连后的产品执行可能仍在继续，先核实产品，不另起冲突scope。

共同提交指针发布后目录fsync失败属于持久性不确定，会报告错误并保留代，不用旧根文件反盖；只兼容导出失败则按已选代修复导出，滚动清理独立执行。完整日常CI覆盖故障注入与本地假产品，不需模型；后续设备空闲后再按执行与协议影响补四机full及NX/Mac build→reuse，当前未开展。

### Judge进度显示排查

对比`phases.qa_eval.progress.judge_completed`与兼容外层`judge_completed`，新版两者相同，且与同一items检查点提交。Judge已结束包含评分失败，跳过另列；有效评分另看`summary.judge_valid_count`，合法0分计入。前端缺少内层时读取历史外层，均无值显示?；不根据均分猜进度。旧执行体等所有Agent结束才回收Judge的行为不能靠刷新页面纠正，新协调器须随固定后端版本发布后生效；不得为修显示中断共享设备或改写旧run。本轮只本地验证，未发布。


## 2026-10-23 本轮更新规则

详细契约及测试边界见[取消、调用事实与采样优化](../refactor/2026-10-23-cancellation-trace-deviations.md)。domain端口、application编排、infrastructure探测及bootstrap装配分层不变；产品同步导入配合协作取消，不引入持久队列。停止全部只请求测评相关任务停止，pending与worker真正退出分开。原始历史、null、TTL和3秒覆盖门槛保持；新运行的L2调用事实纳入真实模型调用统计，旧记录不补造。四机更新前重新核验占用，只激活授权8291/8872。受影响设备真实回归须使用新SHA，不能复用旧run标成新版。


## 2026-10-24 运行终态修复发布

新执行沿用八种总状态。Judge最终失败或应有评分缺失时正常回收全部任务，qa_eval=partial、run=completed_with_errors，列表保留实际xx/yy；合法0分与已恢复重试可completed。漏题/重复/缺少Agent终态为failed，取消仍cancelled，未配置Judge/build不误报缺分。详细契约见[终态汇总规则](../architecture/photobench-metrics-contract.md#t006-2026-10-24-运行终态汇总规则)。历史原始状态不迁移、不改写。仅重判不反改原运行终态。

先固定本地提交并完成make ci，再统一制品同步四机。更新前复核8872 active-work与旧栈占用；空闲新栈仅更换8291/8872，运行任务中的设备仅暂存，不终止他人任务、模型或旧栈。检查版本回显、实际导入路径与前端清单。此次共用执行规则修复需要新版真实回归验证；设备占用/依赖未就绪时明确列为待验，不以旧run或本地受控用例改标通过。

### 2026-10-24 采样与终态回归

本轮用户已授权修复后发布回归，取代上文10-22“仅本地、暂停验证”的阶段边界；仍保留共享模型、EAGLE3和无关任务，NX/Mac须刷新占用后决定能否激活。AGX已恢复18101直接BGE-M3 GGUF嵌入（CLS+L2）和18103 Chinese-CLIP，本机8103主模型未重启。独立验收v4须额外确认partial+预算/解析失败进入completed_with_errors，以及saved/API总状态一致。AGX旧028519与153旧ba9d8c保留为修复前证据，不能改标新SHA。严格覆盖不通过则继续定位，不连空值或放宽TTL。

2026-10-24负载实测补充：AGX视觉推理期间当前smaps_rollup读取会超过0.6秒，旧等待窗口会生成不完整采样。内存探测单次有界等待改为1.5秒（最多4并发，拓扑仍独立最多等0.1秒）；只等待真实读取，不回填先前内存值，超时仍null并登记pending线程。源TTL与严格覆盖3秒及全点完整要求保持，发布后必须重新做真实full验证。

同轮取消专项发现并修复套件线程归属：串行套件工作线程同时按各run ID登记，active-work不再把仍在执行的导入报告为worker_alive=false；显式cleanup只能在拥有该run的线程真正退出后执行，不能与执行/采样/写盘并发收尾。同步导入取消仍等待已开始的工作协作退出，pending期间继续保留归属。

2026-10-24拓扑值龄补修：真实负载进一步暴露0.1秒拓扑等待会复用接近TTL的发现，内存读取结束时拓扑过期。拓扑有界等待改为最多1秒；若刚收取前一回调的已完成任务，在同一截止时间内再启动本次发现。拓扑与最多4个smaps探测共用2.5秒总等待窗口，保留真实源起止，超时或过TTL仍null；3秒TTL和严格覆盖/全点完整要求不变。


### 2026-10-24 Jetson受管进程探测

Jetson正式采样的核心进程内存读取移入单个spawn子进程，与HTTP完整曲线读取和全样本发布分离。
可选ManagedTelemetryWork由GpuSampler启动前准备、采样线程finally关闭；四方法原端口不变。
同一时刻最多一个请求，超时不追加请求、不复用前次值；迟到结果保持原观测起止，domain仍按3秒TTL验证。
关闭管道后子进程结束当前读取并退出；未退出PID经OwnedTelemetryWork计入stop_pending，不能冒称回收完成。
仅创建与关闭本轮探测进程，不启停共享模型。探测进程作为产品后代参与PSS归属；使用当前绑定解释器，无新增依赖或部署开关。
直接单步sample_once保留进程内诊断路径；正式start始终使用受管进程，启动失败明示unavailable，不静默回退。


### 2026-10-24 错误分析发布与验证

运行详情展开“错误分析”；历史结果点击生成规则报告。新测评和重判退出后自动排队，分析失败不改run结果。active-work包括分析任务；取消后等工作退出再更新服务。历史报告、来源指纹和过期标记保留，重评分后重新生成。AI默认关闭，需选择1–20题并确认provider；不将secret写入任务记录。

固定版本执行完整`make ci`，以干净提交打包。四机同一证据目录用各自绑定Python运行`PYTHONPATH=<release>/apps/photobench/src <python> <release>/scripts/replay_photobench_error_analysis.py --root <evidence> --output <replay.json>`，核对fact_signature、raw_unchanged及AGX格式异常/153恢复断言；回放不调用模型。随后仅在资源允许的设备激活8291/8872，真实QA验证新调用diagnostics、工具内部模型事实、Judge事实及自动分析。NX资源不足须保留待验，不抢共享模型。HTML离线导出、页面筛选和原轨迹定位分别验收。

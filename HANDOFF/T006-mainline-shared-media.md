# T006 主线集成与跨相册媒体共享

## 2026-11-20 10:45｜修复Sentrix前端31项测试失败

用户侧聊明确授权修复既有31项失败，并提交、push及更新原main PR #43。起点1a56385工作树干净；本机复现158通过/31失败。逐项根因与当前源码依据保存在产品项目audit/20261120-product-web-tests/failure-inventory.json。缺认证/上传Provider与matchMedia的测试已补齐运行上下文；导航与路由按现行六入口/七路由检查，旧相册切换/命令/QA入口保持隐藏；过程卡片、媒体标签和预览按当前界面核验。

时间线测试先读日历索引，展开月份/日期才取当天事件，检查重复展开缓存、服务端地点筛选和事件深链滚动高亮；BrowserRouter与window共享真实地址，关闭详情只删event、保留date和其他参数。补索引/单日读取失败重试。历史对话核消息和交付照片替换、缩略图点击preview，未展开不读轨迹；语音普通模式传false，新增推理开关传true验证。保留原功能断言的业务目标，没有skip或删除测试文件。

补上下文后发现首页在缺Fullscreen API时调用未定义的exitFullscreen，运行代码仅将该方法按可选能力调用；空/有数据首页冒烟均覆盖该环境。最终Sentrix前端191项/44文件通过、0失败/0跳过，较基线新增2项；TypeScript和生产构建通过，lint 0错误/45警告，diff检查通过。中途夹具类型缺字段、缩略图断言仍找文件名的失败均已修正，日志保留；没有把它们列为最终通过证据。

本轮main仍42a1c58，无待合入提交；只修改本机源码及Git交付，不访问100、不部署/重启、不调用模型或写测评数据。100候选仍为旧99306be，不包含本次修复或main合并；原空闲监控及实验监控不动。提交/push/PR最终回执以该审计delivery.json和pr-final.json为准；继续原PR，不合并PR。另一主开发树四份未提交改动未触碰。

## 2026-11-20｜合入最新main并处理四处冲突

用户要求把main新提交重新合入当前PR。拉取确认main为42a1c58，新增#41导入三阶段与#42关键帧时间命中/QA代表帧。以merge保留双方历史；冲突涉及run_execution/service字段透传、QaItemCard及测试、ImportsPage。保留邮件字段/导入入口、统一图视频邮件GT去重、节点轻读与耗时；代表帧接入现有UnifiedMedia，按标注时间定位视频且保留未交付标记。关键帧面板沿main移至运行详情，测试对应验证独立且只显示一份。修正上游新代码的变量类型冲突、标注时间类型及格式；没有改评分口径或运行预算。

合并后PhotoBench后端1727项通过；后续关键帧45项通过。前端855项/71文件通过；Sentrix导入5项通过，两端TypeScript/生产构建通过。Sentrix全前端158通过/31失败，31个失败名称与合并前59a8784归档完全一致。PhotoBench mypy仍10个既有错误，新增关键帧类型错误已清；改动Python ruff/格式、分层11契约、模块地图、check-docs通过。大chunk与原lint警告保留，未声称全仓CI全绿。

本轮只做源码合并、验证与推送，不重启100服务或启动模型。原候选仍为99306be，未包含本次main改动；原两个空闲wake/监控保留，回调需先核当前代码/候选版本及真正空闲再决定局部上线。主开发树四份原有改动需SHA保持。记录与回执：产品项目audit/20261120-main-sync。


## 2026-11-20｜聊天历史、节点耗时与格式3分析修复

本轮修复历史聊天选择后被型号发现回调重置、会话读取反复扫描相册和整段轨迹的问题。HTTP会话轻读先返回消息及已存答案，回合详情按需生成独立格式3缓存；按引用ID补媒体，旧长回答完整返回。历史续接保留session与已存模型配置，测评占用时仍可查看历史。活动回合轨迹提供手动刷新，关闭轨迹不持续重取。

节点耗时改为按需读取同轮模型metrics及唯一关联工具，保留已有工具检索分量与内部模型观测。未存独立loop墙钟时显示调用累计，提示并行时长可能重叠；歧义绑定和工具耗时缺失不造合计。未增加产品侧loop墙钟埋点。

追加修复格式3详情200但analysis404、其他测评运行导致历史规则分析排队。格式3固定修订提取规则事实，跳过输入输出正文，原题与GT指纹保持；证据/显式AI只读选中题有界片段。规则可与另一测评并行，排队AI不阻塞后续规则；已运行且暂停的AI仍占唯一worker。分析状态失败显示错误及重试。原答卷、评分及失败史不写回。

本机最终后端1725项、前端852项通过；此前复查一项1秒并发读取断言超时，单独及全量重跑均通过，失败日志保留。mypy10错、ruff2错和可达性1+3项已用起点HEAD归档复现；改动文件ruff、架构11契约、模块地图与check-docs通过。不能写全仓CI全绿。上游9170e6a只改README标题，交付时合入保留。

100只读核8872/8874精确worker均running；对应PID1405124/351297，原监控不变，可用内存约66GiB、/home3.4GiB。代码e7682d8及合上游README的99306be已提交；100独立候选chat-trace-analysis-99306be2共167文件SHA一致，未激活。真实Qwen0bbacb的1013题规则投影/聚合10.738秒完成，派生输入157029129字节临时流式处理后删除，原manifest保持；节点耗时读取通过。已注册本线程空闲上线wake：8872为8175bf15-afeb-4e26-9905-a0ceb871f50c，8874为1942be94-c26a-4ca5-9f3f-ac26da4f8f1a；只读watch_idle已启动（PID16371/16372），首轮精确探测均成功且两测评仍活动；连续3次真正空闲才唤醒，超时/异常清零，3次失败或进程/任务漂移也唤醒审查。回调必须再次核真正空闲，仅切对应测评的后端和静态文件，不重启产品。不能把候选验收称线上生效。审计入口产品项目audit/20261120-chat-trace-analysis；只读候选验收脚本deploy/photobench/20261120-chat-trace-analysis/inspect_candidate.py。本轮已push并更新原main PR #43，仍OPEN且未合并；主开发树快进后四份原改动SHA保持。不得重放旧发布/恢复脚本，不改8772/8191/153或运行预算。


## 2026-11-20 09:06｜README补齐推理模式与psh配置

用户要求README清楚说明推理模式，以及不提交Git的psh部署配置，按lieflat-less-ai-tone规范核稿。保留原有README，新增配置入口、local/cloud区别、Qwen采样预设与角色JSON字段；说明环境文件需显式加载、PhotoBench连接优先级、独立配置目录所需模板及生效核验。推理部分写明100模型/600工具（搜索100、其他500）、重试/收尾共享计数、3600秒墙钟与3660秒等待，区分Agent推理与原生thinking，标明普通及推理上下文来源。实际本机配置和密钥保持忽略。

文档门禁发现既有request_budget漏模块登记和5条依赖边、PhotoBench懒加载及DELETE接口漏OpenAPI导出，已补齐两份文档；未改运行代码。当前源码路径显式加入PYTHONPATH后，完整check-docs通过，避免本机共享虚拟环境读取其他旧工作树。README新增链接/命令语法、模板采样/四项上限、cloud角色JSON及独立PhotoBench目录离线加载、Git忽略规则均通过；初次离线夹具缺少两份必需模板的失败已纠正，未涉及运行服务。审计readme-config-validation.txt、readme-docs-check.txt与validate_readme.py，原README正文逐字保持。本次继续原PR #43，不部署或重启，主开发树四份无关改动继续按SHA保护。

## 2026-11-20 08:48｜集成推送并向main提交PR

用户授权核工作区并push、提交main PR。[PR #43](https://github.com/ysh12304124/sentrix-home-web-2.0/pull/43)由feature/photobench-evaluation指向main，未合并。创建时远端HEAD dfe0a88，相对main有202提交/465文件，范围包括邮件与混合来源测评、有限推理预算100/600/100/500、格式3增量检查点与节点/正文懒加载、轨迹与邮件UI、交互重测、对话删除、媒体共享及处理回执恢复。PR说明保留历史慢写/OOM尚未证明消除及实验未完成的边界。

08:44精确只读探针：Q4 3556c7 running、852/1013；豆包d52ecf running、155/1013，worker与型号匹配，豆包进程/库/预算匹配。可用内存约68GiB、/home约4.1GiB；原watcher6536/79813保持。未重启或部署服务、重跑题目或迁移数据。本轮后端专项192通过，测评前端33通过，生产构建通过并有大chunk警告；非.patch文件diff检查通过，旧部署patch上下文空行保持。本轮未跑全仓回归。

当前树原仅有未跟踪audit/20261119-trace-viewer-ui，3.2MiB旧验证产物已整体移入产品项目audit/20261120-main-pr/retained-workspace-audit保留，未提交原始产物。主开发树原两份修改及两份uv.lock SHA保持，禁止加入本PR。审计含两精确probe、测试/构建日志、PR正文和回执。后续继续原监控与终态验收，不因PR切换活动后端。

## 2026-11-20 02:08｜豆包历史媒体副本验收并启动独立1013题

准备171603于01:53:46正常完成，bb58891d终态已处理。285bb73新只读验收确认原库14表指纹完全一致；6330媒体资产/5519观察/18254视觉向量保持；6742邮件原文、词法及semantic/episodic向量各6742全覆盖。新版地址补6330资产，标准后处理无失败，图146027节点/333897边。原媒体VLM不重跑，旧视频抽帧覆盖保留，报告标“历史豆包媒体记忆复用+新版邮件增量”。

8294/8874真实空闲后单次切换，实际PID351254/351297，数据根doubao-reuse-49f347c/product-data，主模型18107实际doubao-seed-2.1-lite、普通与推理上下文16384。进程目录保持conversation-delete-9363d4b-v2/product与wait-chat-950b2be/evaluation；运行连接、systemd最终环境文件与静态Environment同步，预算100/600/100/500、QA4/3660、冻结ATM v5/Judge和reasoning/graph保持。8保护服务PID及Qwen0bbacb manifest指纹不变。首次验收错把SQLite短连接必须留在/proc/fd作条件，已停止在切换后；686ed9d仅补只读accept-activation，用在线6742新邮件及实际环境验库，没有重复切换或重启。

新wake79d22271-9818-4c64-8b62-42ae61b28032先注册，02:06:24独立reuse run `20261120-020624-2026.11.17-atm-sources-addresses-current-d52ecf`启动；继承答卷0，scope仍为独立库中的PhotoBench-20261112-174516-atm-bench-media-current。精确watcher79813已核qa_eval、4个实际产品turn、型号/预算/16K与数据库一致。监控终态、精确worker、进程/库/预算漂移、磁盘<1.5GiB、可用内存<4GiB、单服务RSS>24GiB、连续3次探测失败及两小时无QA/turn进度；只唤醒审查，不自动重启。Q4 3556c7最新590/1013、worker/型号正常，fd46ad08/watcher6536保持；原18隔离及活动后端不动。

新脚本accept_prepared/activate_run/probe_run/watch_run已先本机语法/差异检查并提交285bb73/033b5de/686ed9d/5231242，再传100核SHA、执行实际验收。300秒等待已生效，但未证明原211秒慢写或OOM根因消除。/home约5.2GiB，终态导出须流式避免重复大文件。入口产品项目audit/20261120-doubao-media-reuse/{README.md,delivery.json,watchers.json,doubao-latest.json}及主实验state/checkpoint-watchers。activate/accept-activation/formal launch均已消费，禁止重放；终态核1013去向、冻结ATM分与真实失败，再验在线trace/SFT两完整流式导出，最后三模型比较。不启动Hard/16题包，不改8772/8191/153或其他服务器。


## 2026-11-20 01:30｜Qwen终态已验收，8874两修复上线，豆包准备复用旧媒体

Qwen0bbacb 1013唯一QA、1012答卷/有效ATM分、1真实995f8ba1模型失败。四份现成答卷仅Judge补评4/4完成、0失败，原1008回执及所有答卷指纹保持；有效均值65.884562%，失败未补0，total_score为空。17新attempt、996原答卷、1010seed答卷/1005回执及源manifest均保持。固定版本完整在线trace44,902,276字节/SFT1,009,589,070字节SHA验收通过，未落大正文。fe870c77与score_four.py accept已消费。

950b2be已局部上线8874/PID71956，实际app-dir为wait-chat-950b2be/evaluation/apps/photobench/src；有效300秒、线上静态文件及真实浏览器静默恢复/主动打开通过，只重启8874，保护PID/结果manifest保持。本机ChatView7项、长运行专项及构建通过。延长等待没有消除原211秒慢写或OOM的证明。

新授权取代旧豆包full从零方案：历史3759图/533视频的像素/方向和音画载荷全部匹配，拍摄时间/GPS也匹配；旧WeMM2048配置可复用。6742邮件与新包原文全匹配。使用独立SQLite副本保留原库基线，同历史scope补地址/词法索引和邮件/文本嵌入，标准后处理后再正式1013题reuse新run；保留旧视频抽帧，报告写“历史豆包媒体记忆复用+新版邮件增量”。准备脚本49f347c及watcher已本机验证提交、同步100校SHA/只读preflight通过；01:30:56先wake bb58891d-dbfe-43d3-aa33-8838659f7ce4再单次launch，worker171603与watcher69971存活，当前阶段snapshot。副本创建后/home约6.5GiB；正式Agent尚未启动。回调先验副本与6742邮件/索引/后处理、原基线，再空闲切换并另挂正式QA wake。不重放本轮launch或旧脚本。Q4最新572题原fd46ad08/6536继续，不切活动后端。当前入口产品项目audit/20261120-doubao-media-reuse及主实验state/checkpoint-watchers。

## 2026-11-20｜侧聊修复检查点等待和聊天自动弹窗，豆包改为复用媒体记忆

用户明确要求先改代码提交，并将以下授权交主线程：检查点写入等待由180秒改300秒；进入“聊天对话”页不能自动打开“聊天对话与执行轨迹”；两项上线验收后再进行豆包测评。侧聊仅本地修改、验证与提交，未重启服务或启动测评。

`run_execution/state.py`默认值已改300，保留`PHOTOBENCH_PERSIST_TIMEOUT_SECONDS`覆盖；等待仍只确认成功提交的修订，不吞写入错误或虚报成功。启动新进程才读取默认值，主线程部署时需检查环境是否仍显式覆盖180。此前线上copy0.392秒/write211.568秒证明有写入超过180秒；本次调等待值，未将慢写或OOM根因宣称解决。

聊天页原先自动恢复localStorage记住的会话时调用`select`，连带打开弹窗。新增静默选择参数，仅初始化恢复传false；主动点会话、打开按钮和发送行为保持。回归覆盖ready/running会话的首次进入与重新挂载不弹窗、主动点击打开及删除行为。ChatView 7项通过；长运行后端专项通过；默认300及显式覆盖42独立进程核验通过；TypeScript/Vite构建通过。

豆包的新授权取代先前“每模型从零full新记忆”的后续要求：复用旧scope `PhotoBench-20261112-174516-atm-bench-media-current`中3759图/533视频已处理结果，补6742封邮件后完成本轮1013题答题、冻结ATM评分及trace/SFT两导出。历史run `20261113-035053-atm-bench-media-current-e13149`；核验依据产品项目`audit/20261113-cloud-text-index-repair/README.md`。执行前确认旧库仍在、媒体哈希和嵌入配置一致；独立副本/目标scope保留原基线，复用相关资产及记忆关联，补地址元数据和必要索引，刷新记忆图及相关后处理。保留旧视频抽帧覆盖范围并在实验报告标记“历史豆包媒体记忆复用+新版邮件增量”，不标成新版从零构建。

主线程继续掌握发布和运行安排，避免与现役Q4、Qwen补评分冲突；确认两项修复线上生效后才启动真实豆包。侧聊已先发两次需求提醒，提交完成后补发提交号与完整接手信息。

## 2026-11-20 01:00｜Qwen 17题终态已核，仅补四份现成答卷评分

0bbacb终态1013题：1012执行完成、1个新995f8ba1 model_error，1008有效ATM分，四份现成答卷缺Judge。17个授权attempt均有独立产品回合：5正常交卷/7有缺口/4步骤上限/1模型错误；状态不代替正确性。996原继承答卷、1010seed答卷和1005评分回执逐项保持，源0e3686原1013项/1011有效分及170bf7 manifest SHA不变。保留新模型失败，不自动重跑或补0。

先新wake fe870c77-445f-47b9-8245-55f820457144，再仅Judge四题同冻结批次，辅助scoring-5432bc9e56bf4fa0bc19ad0d3d8a0f4a，watcher60461已精确核running/worker_alive。原1008分/全部答卷指纹已记；下一步score_four.py accept单次验四分与保持性，再验完整在线trace/SFT两导出，通过后才先wake启8874真实豆包full新scope。当前豆包未启动。

本轮未重启服务，8874实际conversation-delete-9363d4b-v2；Q4 3556c7已563题、worker/型号正常，原fd46ad08/6536保持。预算100/600/100/500、QA4/3660及18隔离保持。/home约11.2GiB，导出应流式验收。脚本先本机ruff/语法检查与commit后同步SHA，终态检查和精确补评启动已过。现行入口产品项目audit/20261120-qwen-terminal及主实验state/checkpoint-watchers，旧回调6f788501已处理；本轮inspect/launch不可重放。没有证明原211秒持久化或未来OOM已彻底消除。


## 2026-11-20｜对话删除已上线8294/8874

9363d4b沿产品已有DELETE加事务内活动回合检查，测评新增DELETE并联动删除产品/本地session；两端列表直接显示删除和二次确认，活动会话409。仅删对应消息/轨迹，资产及测评run/答卷/分保留。本机后端137+1、前端16+3和两端构建通过；100候选产品48/测评89通过。8294/8874空闲后单次切换，PID4104799/4104804，实际目录conversation-delete-9363d4b-v2/{product/apps/api/src,evaluation/apps/photobench/src}。8保护单元PID及原结果manifest保持，29文件/两端在线静态SHA一致；独立空会话DELETE204/GET404、不存在DELETE404，旧会话/消息/回合不变，模型新增0。8874页面删除入口可见。

第一stage因4份更早测试文件基线差异在intent前停止，生产源码全匹配；审查后新v2目录发布。首个验收GET误写由d0e0a49改显式POST，仅操作新空测试会话，正式功能未再变。审计产品项目audit/20261120-conversation-delete，所有已消费stage/activate/verify不可重放。8872/Q4活动期未切新删除功能，后续对应产品/测评真实空闲再发布；Q4原fd46ad08监控保持。Qwen0bbacb已终态，17题比较/补评分/两导出验收仍待原实验后续，不启动豆包。

## 2026-11-20｜对话删除实现与发布准备

用户要求Sentrix与测评聊天均可删除。沿用产品DELETE接口，补产品事务内活动回合检查与创建回合时会话状态检查；测评新增DELETE /api/v1/chat/sessions/{id}，先删对应产品会话再删本地session。产品404可清理本地，409或其他失败保留本地；两端列表提供直接删除、二次确认与进行中状态。仅删目标会话消息/轨迹，不改资产、测评run、已存答卷/分数。

本机后端137项与turn executor 1项通过，前端两端专项及构建核验；本地浏览器两端删除确认与列表移除已核，仅使用mock会话。全仓既有Panel测试/mypy问题另列审计，不扩大范围。发布脚本deploy/photobench/20261120-conversation-delete/release.py限定8294/8874，两者当前空闲，Qwen0bbacb已终态；8872/Q4仍活动，保持其PID/目录/静态根。提交后同步100候选校SHA并测试，再单次启用，线上仅创建无模型调用的空会话验证后删除。发布回执见产品项目audit/20261120-conversation-delete；原恢复脚本不重放，Qwen完整终态验收与豆包后续另续。

## 2026-11-20 00:00｜Qwen写入中断恢复，8874新后端生效

旧170bf7在1007项因180秒持久化等待失败；日志copy0.392秒/write211.568秒，原211秒内部耗时尚未唯一复现。候选4df9302独立真实1007项cProfile写91.36秒，源manifest/分数保持。8874测评及产品完全空闲后切至trace-viewer-ui-4df9302实际目录，PID3854102/四源码SHA确认，6保护PID保持；8872活动中仍旧后端。

3份已完成产品回合以GET找回，答案SHA/终止原因一致，无Agent重跑。新seed1010项写15.52秒，新run0bbacb仅执行3题；wake6f788501-6e9a-4869-8e82-847de43c447c先登记、watcher41342精确监控。实际核1010继承答卷/1005既有回执不变，源0e3686原1013项/1011有效分与170bf7历史保持；新995f8ba1 model_error仍失败，不自动重试或补0。三新回合真实预算100/600/100/500，QA4/3660、冻结ATM/Judge不变。

8874在线核心31.9ms且无process_diagnostics，首题25模型/16工具及1024字符节点正文可读；新run首快照write11.247s。00:00测评RSS约739MiB，系统可用约64.5GiB，swap满，/home约12GiB。Q4 3556c7最近532题、worker/型号正常，fd46ad08/6536保持。该实测不保证消除全部OOM/超时。

接手入口改为产品项目audit/20261119-qwen-anomaly17-persist/{README.md,delivery.json,watchers.json}及主实验state/checkpoint-watchers。终态比对17个新旧attempt、1013去向与四份补Judge后最终评分，验完整在线trace/SFT两导出后才先新wake接8874豆包full新scope。本轮activate/recover/resume已消费，禁止重放；Q4原18隔离保持，不改8772/8191/153/其他服务器。

## 2026-11-19｜测评查看界面已静态发布，后端候选待空闲

按最新要求恢复原左右节点/四详情页签并保持分页与正文分段，邮件压为单行按需原文，删会话标识块，预算改小字。普通单题移除process_diagnostics显示与默认后端读取；独立分析和导出保留。格式3空投影不再显示假0，缺耗时不画伪“其他”占比。真实源0e3686第1题25模型/16工具、83.0s/178.3s已核。

真实浏览器又定位继承节点的ObjectRef二次编码问题：写端保留引用类型，旧目录按fields识别内部引用，前端沿同版本source_path读取原文；原始数据和manifest不改。本机160后端通过，全前端840通过/1失败在HEAD190925d复现；构建/lint/ruff通过。详见docs/refactor/2026-11-19-trace-viewer-lazy-ui.md。

4df9302已先本机提交，再同步100核源码SHA、160项通过、真实1013存档只读验收，最后仅静态发布两端；7保护PID保持，3556c7/170bf7继续，原fd46ad08/ddd6b98e两个watcher存活。真实411节点按需仅读摘要及三段正文，峰值RSS69888KiB，原manifest未变。8874输入/输出/指标、390px窄屏及8872节点四页签已核。后端候选trace-viewer-ui-4df9302/evaluation尚未激活，普通单题不读process_diagnostics/旧引用修复等须空闲后切换。主实验state/checkpoint-watchers及Qwen审计已加候选接手入口。

线上历史0e3686列表一次18.97秒超前端10秒；独立候选和旧代码均0.77秒，活动run查看正常，并发现场原因未唯一定位，未宣称修复。脚本deploy/photobench/20261119-trace-viewer-ui的stage及publish-static已消费，禁止重放；新后端激活须先核真实systemd启动覆盖及对应测评/产品空闲，核cmdline/SHA与在线轻详情，再原监控验收。本轮未新增Agent/Judge、不改scope/分/失败史。审计在产品项目audit/20261119-trace-viewer-ui。

## 2026-11-19 22:18｜六补评分验收，17题独立新attempt已运行

六题Judge 6/6完成、失败0；accept_scoring.py实际核1011有效分、1005原有效回执SHA逐项保持，两原执行失败状态保持。补评分wait75fbfd92已处理。源0e3686原1013项/1011有效分及17异常答卷完整保留。

3739235单次seed/resume已消费：新run170bf7（完整ID见主实验state）继承996题，只执行授权17异常；新wake ddd6b98e-7279-4dc8-9aac-46fcd953f948先登记、watcher11012精确监控。c1f2ed9只读验收脚本本机提交后同步100核SHA/语法，实际核996继承答卷及996评分、源manifest保持；已保存1新attempt，共997项，running/qa_eval、worker/型号正常。4个真实新回合均100/600/100/500，QA4/3660保持。Q4最新459题、worker/型号正常，fd46ad08/6536保持，未重启或切新优化。

下一步等17题终态wake，按精确QA ID比较新旧attempt/结束原因/分数，核1013去向、真实失败和两完整流式导出后再豆包full新scope。不得重放plan/seed/resume/补Judge/accept_scoring及任何旧发布恢复脚本；不强改失败成功或补0。完整懒加载、Q4原18隔离、原scope/资产/分与历史保持。当前审计audit/20261119-qwen-persist-recovery、主实验state/checkpoint-watchers已更新，推送SHA回执见delivery.json。

## 2026-11-19 22:08｜Qwen持久化恢复、实际版本修正及17题新尝试授权

当前入口改为产品项目audit/20261119-qwen-persist-recovery/{README.md,delivery.json,watchers.json}及主实验state/checkpoint-watchers。Q4原3556c7最新455/1013、worker/型号正常，fd46ad08与watcher6536保持；本轮未重启Q4。以下原21:00“仅恢复七题/43及671不可重跑”的限制已由用户最新明确授权取代，原结果及失败历史仍完整保留。

Qwen4c5f90持久化180秒等待失败，保存1007题。找回3个已完成产品回合，原答卷一致；独立seed继承1010后仅运行3个未执行QA。0e3686现completed_with_errors，1013题齐、1011 execution completed/2执行失败、1005有效ATM分，原1002有效回执保持。六现成答卷同冻结batch仅补Judge，辅助活动scoring-0d4fa66aef1e4a8c8e3c457977712156，wait75fbfd92-410c-4170-bac1-5f4408802304/watcher5955已精确核存活。旧bd347d8c终态事件已处理。

3d32090将活动列表统计移到状态锁外，b4cb0ae读取已提交统计并叠加实时进度。两早期98/99启动项被zzzzzzzz-full-lazy.conf覆盖；先前只看PID产生误判，0e3686三题仍在旧目录跑，不能归因新补丁。dfb513d修真正生效项后，8874 PID3279790的/proc/cmdline及四文件SHA确认qwen-live-list-8b6da36实际加载；保护PID保持。最新1010题独立并发验收保存14.93/11.59秒、408列表读、RSS572040KiB、源manifest及评分保持。本机/100最新147项均通过；原远端5模型适配失败在baseline复现。241秒原综合write的唯一根因尚未复现，无本轮新增OOM证据。

用户明确授权最新版定点处理17个Agent异常（11步数、2planner、2guard、1timeout、1model_error），保留原run/答卷/分/失败，保存新attempt，优先于豆包。3739235单次retry_anomalies.py本机提交后同步100验SHA/语法；17题精确ID/原指纹与分布已核。尚未seed或resume；六补评分核1005旧有效回执及六新分后，单次seed继承其余996题、原1013保持；每次resume先新wake再单次POST并保存映射。预算100/600/100/500、QA4/3660、reasoning/graph、冻结ATM/Judge及原scope保持。两完整流式导出验收前不启豆包；完整懒加载已上线两端，Q4原18隔离保持，不启动Hard/16题包、不改8772/8191/153或其他服务器。提交推送回执见审计delivery.json。

## 2026-11-19 21:00｜Qwen全1013终态分类后仅恢复七个服务故障

Qwen f41436 completed_with_errors：1013均执行、1002有效ATM分，QS有效均值0.6586843988790096但total_score=null。严格筛出7个未交卷服务超时/502，c9179c1先本机验证commit、同步100校验SHA，再先wake单次resume为4c5f90；继承1006题、原scope/失败史保持，无服务重启。1002评分回执及答案指纹逐项一致，另4未评分答卷的答案/状态/媒体一致。真实新回合预算100/600/100/500、QA4/3660及冻结ATM/Judge保持。新wait560dfaf0-1bae-49e6-8d60-b9305b7c8e16/watcher86994；Q4原fd46ad08/6536保持，已401题、worker/GPU正常。

两正常答卷仍缺评分（QA71582c85、bfe15c10，完整ID见审计），resume不自动补profile评分；恢复空闲后精确同batch补Judge，不重跑Agent。43/671原失败已有答卷，禁止自动重试/改成功/补0；预算耗尽等也保留。1013每题去向、评分与两完整流式导出尚未验收，豆包尚未启动。UI行号随暂缺7题压缩，后续以qa_id定位。测评RSS/HWM约1.36/1.88GiB，系统可用约64GiB，/home约13.5GiB。旧一次脚本已消费禁止重放。主实验state/checkpoint-watchers及audit/20261119-qwen1013-acceptance/{README.md,delivery.json,watchers.json}为现行入口。

## 2026-11-19 15:59｜Q4正式采样生效，恢复GPU0后保留196题续评

psh a5daf519与main 28ba9e6已是本分支祖先。只读1538772最新实际任务，产品连接8195，以local模式启用qwen35_nonthinking；磁盘q4km.json未被加载，8230属于实验副本，均不复制。正式采样temperature0.7/top_p0.8/top_k20/min_p0/presence_penalty1.5/seed42/enable_thinking=false；Agent reasoning、100/600/100/500及失败工具过滤保持。f75c105新增环境模板，并在显式产品升级续评时冻结采样开关；2e99113修产品版本配置持久化。两者均已验证并发布100Q4，Qwen产品配置不动。

进一步发现Q4模型PID1583在启动时CUDA初始化失败、长期CPU推理。d0fc404的一次性脚本已提交后同步SHA验证，先取消精确b5c51a并核测评/产品空闲，再仅重启原模型unit。新PID1465949已在原GPU0，显存6038MiB、4槽各16K、型号/原启动脚本SHA正确；受保护产品/测评/WeMM/8772/8191进程保持。实际测评生成已恢复，日志随并发负载变化，不能把单点45token/s当稳定吞吐。

新wake先于单次resume；Q4当前3556c7继承196题、18隔离，逐题答案/元数据/执行状态及154份完成评分全匹配。原scope/失败历史保留，retry_qa_ids=[]。Qwen f41436保持。Q4 wait fd46ad08-b7ee-4371-b882-3cf8fd304b0b/watcher6536；Qwen ce0022c2-ca23-48b8-8c0e-27f73c166e69/watcher91971。主实验state/checkpoint-watchers及正式配置watchers为现行映射；旧936ee9c3已cancel、2572已停止。正式发布stage/stop/activate/resume及GPU恢复stop/restore/resume均已消费，禁止重放。

完整懒加载已上线，两端实际页面/节点/分块及完整两导出验收见下节；本轮不回退。QA4/3660、1013、reasoning/graph、ATM v5/豆包Judge保持，整轮终态评分及两导出待wake验收；Qwen全部验收后才接8874豆包独立full。不启动Hard、不发布16题包、不改153或1008772/8191。证据：产品项目audit/20261119-psh-formal-config/{README.md,delivery.json,watchers.json,gpu-restore/}。

## 2026-11-19 15:30｜完整懒加载已上线并通过长轨迹与在线验收

18146f7/41546d8/c9fe1eb/c12f396均已提交并push。1008872/8874已启用格式3增量存储、指标/单题核心轻读、节点分页/正文分块、按需评分及固定版本流式两导出。真实浏览器已展开节点与内容分块；修复EnvironmentFile覆盖导致旧前端继续生效的问题，实际HTTP和进程环境均已核。

真实101题含全部18条超长轨迹，48读/4写共942读/4保存，RSS峰值1.67GB，最长保存150.54秒，答案/评分/元数据SHA保持；整组读取最大15.92秒。两端在线API/两完整导出通过。后端1692项及前端832项通过；1项旧前端、5项候选模型管理失败基线复现，完整结果见审计。Qwen旧索引数值类型已修，729份评分回执全匹配，原压缩存档不变。

最终Q4 run86771b继承178题、Qwen runf41436继承740题；15:30附近快照181/749题，worker正常，各4产品回合，RSS各约0.97GB。原scope/分数/失败史/18隔离保持，100/600/100/500与QA4/3660、reasoning/graph、冻结ATM/Judge保持。8872/8874 PID1213513/1213800；保护产品/模型/8772/8191 PID保持。

现行wake：Q4 54732136-a7e3-4016-9784-da122b283b3d/PID91966，Qwen ce0022c2-ca23-48b8-8c0e-27f73c166e69/PID91971；已精确核验，旧wait取消，禁止重复登记或重放发布/resume。完整1013及终态评分/两导出仍待wake验收，Qwen验收后才接豆包独立full。当前状态取代下方历史中的“完整懒加载未完成/未部署”。证据：产品项目audit/20261119-full-lazy-results/{README.md,delivery.json,final-live.json,watchers.json,web-activation-fix/}及主实验state.json。

psh正式代码已在本分支祖先中，无新提交需要重复合并。新增配置对齐以用户指定1538772为线索，只读核其实际产品与参数；保留四项推理上限、失败工具过滤及本轮懒加载，不将实验/消融配置直接覆盖当前1013协议。

## 2026-11-19 13:34｜18条历史轨迹定点隔离已上线，两路已续评

- 测评修复 `fcb32bd` 已部署1008872/8874（PID614203/614225），包含47523cd活动读取内存修复；本机100项、100候选100项通过。种子安装辅助脚本 `73c45eb` 已验SHA并push，feature分支远端一致。产品、模型及其他实例保持原版本。
- 针对Q4原run `62cc28` / generation `6d296ab38f414887a55fc0aade1672b0` 的18条轨迹，在完整解析前选择轻状态；保留101条答卷、执行状态、分数、失败历史和1013分母。原不可变检查点及18条归档保持；隔离题不自动重跑Agent或补Judge。
- 原563MiB压缩检查点生成73MiB轻状态。独立流式比较全部非隔离轨迹字段，双方规范流SHA均为 `9ae2f3a559b00091a41e60d86134c239dc540a0355999ae424f5416523b9d9ca`；安装验SHA和原指针保持通过。离线生成最大RSS2.87GiB，独立校验最大RSS3.77GiB。
- Q4新run `20261119-133056-2026.11.17-atm-sources-addresses-current-resume-bfe3ee` 继承101/剩912/18隔离；Qwen新run `20261119-132135-2026.11.17-atm-sources-addresses-current-resume-2f8ee5` 继承679/剩334。验收时两路qa_eval/running、精确worker=true、各4产品回合、型号匹配，Qwen已保存691条。100/600/100/500、QA4/3660、reasoning/graph和冻结ATM/豆包Judge保持。
- 8872真实并发读取：隔离题1.40秒/20KB且三类重轨迹长度0、分页3.20秒、总览0.52秒、active-work0.15秒。读取前后Q4 RSS约1.70→1.71GiB，HWM3.42GiB保持；Qwen RSS2.26GiB/HWM3.92GiB，系统可用约65.9GiB。本轮现场结果不能替代全程验证。
- 两路wake均先注册后恢复：Q4 `eac4aef7-a65f-4e13-a817-0c2f5117df86` / watcher60310；Qwen `98e5080b-f49f-4023-b295-5876c0fb4112` / watcher57594。listener healthy/queue/dry_run=false；终态、worker、型号、磁盘、无进展及连续探针失败均触发续接。
- 完整分块存储及节点懒加载仍是本机未提交草稿，未部署。本次完成定点旧轨迹隔离。两轮完整1013答卷、评分、两导出验收仍待后续wake；Qwen验收后才接豆包独立full。stage/activate/seed安装/两次resume均已消费，禁止重放。

证据：`audit/20261119-oversized-trace-quarantine/{delivery.json,real101-preservation.json,install-seed-response.json,live-acceptance-summary.json,q4km-concurrent-read-acceptance.json,watchers.json}`；活动映射以主实验`state.json/checkpoint-watchers.json`及100`active-runs.json`为准。


## 2026-11-19 02:22｜Q4 OOM修复并从95题续评，两路异常监控已验证

01:51内核OOM杀死8872测评PID976050，匿名RSS62616860KiB；自动重启后62b2fc标interrupted，保存95题及81有效ATM评分。47523cd已本机提交并完成100候选验收：活动读取复用一致快照及不可变完成轨迹，同run历史完整读取合并在途解析。仅8872切至独立oom-read-47523cd/PID1164820，其余六个保护进程PID保持。既有云模型/清理覆盖层已取证保留。本机131项、远端129项通过；真实并发读取RSS仅增加约122MiB、峰值保持，但列表14.15秒/单题14.96秒，性能尚未完全解决。

先注册新wake，再单次恢复为Q4 `20261119-021603-2026.11.17-atm-sources-addresses-current-resume-62cc28`，继承95/剩918，原scope、已完成答卷和有效分保持。Qwen `20261119-013722-2026.11.17-atm-sources-addresses-current-resume-842598`保持运行，02:22已保存276题，两精确worker及型号匹配。真实新回合限额100模型/600工具（搜索100/其他500）已核，QA4/3660、reasoning/graph与冻结ATM/豆包Judge保持。

旧Q4监控因83,769字节事件超过64KiB而发送失败；现白名单事件约650字节，完整证据落盘，再经本机wakecodex emit登记并交queue。新Q4 wait `1ac4b69e-0f4e-4a1e-a2a8-0818c21ab968`/watcher9111，Qwen wait `1ab1c834-d97f-4459-b140-a332e4d2ea3b`/watcher8274，均已核精确快照，listener healthy/queue/dry_run=false。旧两wait已对账cancel，旧Qwen进程在新监控通过后停止。监控覆盖终态/worker/型号/磁盘/无进展/探针异常；本轮未新增独立内存阈值告警。

完整分块存储和节点懒加载仍是未提交、未部署草稿；首次读取巨型旧检查点仍会完整解析。stage/activate/resume均已消费，禁止重放。当前活动映射和推送回执以产品项目 `audit/20261119-q4-oom-recovery/{README.md,delivery.json,watchers.json}` 及主实验 `state.json/checkpoint-watchers.json` 为准。Qwen1013答卷/评分/两导出验收后才接豆包独立full；不启动Hard、不发布16题包、不改8772/8191或其他服务器。后续本机验证commit→同步100验收→push。

## 2026-11-19｜有限推理预算已激活，两路原scope续评

1008291/8294已独立发布ffec2be，每题真实生成请求100、工具600（搜索100/其他500，搜索计入总额，两个分项互不借额）；收尾在上限内，HTTP重试和工具内VLM共用模型额度，连续失败工具移除。普通非推理行为保持。产品243项、测评71项远端候选通过；恢复配置快照daa1f15本地及100各86项通过，显式升级只替换四个调用上限，原配置来源完整保留。

Q4新run `20261119-013809-2026.11.17-atm-sources-addresses-current-resume-62b2fc`继承92、剩921；Qwen新run `20261119-013722-2026.11.17-atm-sources-addresses-current-resume-842598`继承249、剩764。原scope、历史有效分/失败与取消记录保留，仅未完成题进入新预算；模型/题集/Judge冻结、QA4/3660秒、reasoning/graph保持。两路已出现真实新回合的reasoning-bounded-v1计数，Qwen已完成首批两题。原run快照提交指针SHA经切换与恢复核保持。

Q4按此前授权移至GPU0/PID888621，模型/8100/4槽16K及权重路径保持；切换前GPU0空余13.4GiB。两产品PID891823/892400，测评PID976050/976123；8772/8191及其原PID11560/2371432保持。独立发布未覆盖共享旧根；源码本地commit后同步，局部适配保留、SHA和AST核只增resume_run调用上限快照。

先新wake再各单次resume：Q4 wait bfeae5a2-a58f-418f-88a0-6a537b1fec4b/watcher107，Qwen wait d2a979ad-0e47-4d76-af4a-1ab1ffc9f3ab/watcher99457。精确映射以主实验state.json/checkpoint-watchers.json及本轮resume-watchers.json为准。stage/activate/模型切换/resume-snapshot/两次resume已消费，禁止重放。完整轨迹分离与节点懒加载仍为本机未提交草稿，本轮未发布；既有读侧/冻结轨迹复用已在两测评启用，不能据空闲API毫秒响应断言活动长尾读取完全解决。

证据：产品项目 `audit/20261118-reasoning-bounds-release/README.md`、activation-response.json、resume-snapshot-response.json、actual-turn-budgets.json及两份resume intent/response。后续按新精确run验1013答卷、ATM评分和两导出；Qwen全部验收后才先wake再在8874接实际豆包2.1 Lite独立full，Q4并行。不启动Hard、不发布16题包、不改8772/8191或其他服务器。后续仍本地验证commit→同步100验收→push。

## 2026-11-18｜懒加载与推理有限预算规划完成，未实施

23:16核验：Q4旧run7955c3因180秒快照持久化等待失败，64份答卷保留；活动续跑3ece8e于23:16为qa_eval/running，70/1013，Agent77、Judge63，worker已确认。Qwen211c0d为qa_eval/running，220/1013、Agent221、Judge218；产品有4个running turn，但active-work接口超时，worker状态未核实。两精确watcher55106/4162仍运行，未重启/恢复/补跑。精确快照见产品项目audit/20261118-atm1013-three-model-full/live-followup-2315.json。

用户要求先规划：推理每题模型100次、工具600次（搜索100/其他500，搜索计入工具总量且两类不互借）。方案补齐规划/评审/上下文恢复/HTTP重试/工具VLM的共用模型计数，建议交卷计入其他工具，收尾各预留2次但包含在上界内；逐项检查批量工具，恢复不重置预算。

懒加载已核：普通概览已有metadata轻读，但共享大写锁；题目分页/单题详情仍读整轮、单题带全轨迹。方案拆轻量索引/单题核心/节点内容，增量保存重轨迹、短锁发布版本，完整两导出显式流式生成。性能数字均为验收目标，尚未实施或压测。当前1013三模型沿用冻结协议，本轮未改源码/配置/服务器或重跑；原监控和续跑约束保持。

主方案：`/Users/jzousz/.codex/worktrees/telemetry-latest-main/sentrix-local1171-resume-20261031/docs/refactor/2026-11-18-lazy-read-and-reasoning-limits-plan.md`；审计入口：`audit/20261118-lazy-read-reasoning-plan/README.md`。接手按方案A–D实施，后续本地验证commit→同步100验收→push，对应产品/测评真实空闲后才激活。以下时间段是既有操作历史。

## 2026-11-18 22:11后｜Q4复用冻结轨迹，保留64题续3ece8e

7955c3于21:10因快照等待180秒再次failed，64答卷/52有效ATM分及4评分失败保留。4d3d477冻结完成轨迹并复用其复制/编码压缩片段；本地1657、100候选129及真实64题两导出解压SHA通过。首轮冻结68.711s+写20.154s，重复copy0.030s/write6.660s；现场复制约0.04–0.10s、写42–61s，仍重写完整压缩文件。只1008872核产品/测评真实空闲后切q4-trace-reuse-release/PID3833360，含原2cbc6cf读优化；其余五保护服务PID保持。空闲API六次0.4–7.1ms，运行中有一次5s超时，后续普通精确worker探测通过，不能称延迟已完全消除。

先新wake ad9b42d5，再单次resume，活动run20261118-215717-2026.11.17-atm-sources-addresses-current-resume-3ece8e，继承64/余949，原scope不变。新前64行JSONL与原SHA完全相同（02da892d…955ffc）；原失败与有效分保持，未重试已完成Agent。最新Q4 69/1013、Qwen211c0d 165/1013，两者qa_eval；Q4新精确watcher55106，Qwen4162保持。QA4/3660、reasoning/graph、1013与冻结ATM豆包Judge不变，未改Agent预算。

证据与接手见audit/20261118-q4-trace-reuse/{README.md,delivery.json}及主实验state/checkpoint-watchers。旧wait b92d8b16已处理；本轮stage/benchmark/activate/resume均已消费禁重放。继续保留原scope/资产/答卷/有效分与失败史，只续未完阶段，每次新恢复先新wake。Qwen全1013答卷/评分/两导出验收后才接豆包独立full；不启动Hard、不发布16题包、不改8772/8191或其他服务器。以下带时间的旧状态保留作历史。

## 2026-11-18 19:01｜状态API恢复通知须连续三次确认

Q4 2d4f47dc事件又是一次短暂响应，随后5秒探针再超时；保留原52题，日志已记录下一条53题快照完成（copy19.562s/write32.829s，实际检查点再核）。三个旧回合在18:58到时结束，最后一回合仍推理；Qwen18:59已34/1013答卷与评分。仅本机watch.py将已知故障的恢复通知改为连续三次精确worker探测成功；超时或探针异常清零计数，终态/worker缺失/型号/磁盘检查仍优先。7项模拟通过，最初模拟的停止异常被业务捕获，修正测试停止信号后重验；业务实现未因此更改。监控仅本机运行，无需同步或重启服务器。新wait/PID及最新进度见产品项目state.json；读侧候选2cbc6cf仍待对应实际空闲后启用。


## 2026-11-18｜Q4状态接口超时，读侧优化待空闲激活

7955c3仍QA52/1013，原scope四个产品回合有实际推理，8872同步HTTP线程池排队，状态接口连续超30秒；openapi异步接口可响应。本机修复重复详情在事件循环共享等待，避免占满HTTP线程；运行态跳过缓存体积遍历，终态达到64MiB后停止遍历。48并发重复详情下状态接口及异常恢复回归通过，完整1653测评测试通过。先commit后同步100独立候选组合验证；业务正运行，本轮不重启。新监控明确记录status API超时、worker存活未核，转读精确检查点及真实产品事件，API恢复/终态/无进展/资源故障唤醒。Qwen稍后已完成构建收尾，18:33进入qa_eval并保存9/1013题；网关实际模型请求持续成功。活动映射与最终回执见产品项目audit/20261118-atm1013-three-model-full/state.json及audit/20261118-q4-read-pressure/。

## 2026-11-18 18:04｜Q4大快照修复并保留51题接续

Q4原90f21f因180秒快照等待失败；51题的run.json为2.17GB，最大单题轨迹334MB。de88f01先本机验证commit，再发布1008872独立q4-checkpoint-release：进程内快速复制、逐题一次编码、可选gzip无损存储及读取/恢复/分析兼容。远端state排空适配已回收本机，冻结协议/预算保持。本地1649项、93定向、Ruff/8文件Mypy通过；100实际93项、真实51题解压逐字节SHA通过，孤立copy15.247s/write9.814s、单份压至334MB；运行中首条慢快照约23.7+28.9秒。启动时仍记录过列表15秒超时，尚不能认定列表延迟完全消除。

仅8872空闲切换，PID2527943；8874/两产品/8772/8191保持。新wake先于单次resume POST，7955c3继承51题/剩余962，父90f21f及原scope保持；原51行JSONL SHA=d6de5be6440ef4fb31f7d84acd7ced33845e88ff35e715bd12428f69830248c0，与新前51行完全一致。现qa_eval/52题、实际4槽工作；不是仅队列受理。原评分有效分、缺项/失败历史保持，终态须精确补齐并验1013答卷/ATM评分/两导出。

活动Q4完整ID：20261118-175624-2026.11.17-atm-sources-addresses-current-resume-7955c3，新wait f0e83446-d246-40b5-83f6-e3cf1e219516/PID4158。Qwen211c0d仍11034/11034材料processed、finalizing、QA0，原wait0bda9c0b保持，watcher20927替换为4162。27aaa87监控绑定精确ID，poll_count不计业务进展，失败保留stderr。旧Q4wait7f3c8516已消费；启动检查中两次失败均在业务操作前退出，修正commit后同步，未重复POST。Qwen完整验收后才接豆包；不启动Hard、不发布16题包、不改8772/8191或其他服务器。

详细状态及接手入口为产品项目audit/20261118-atm1013-three-model-full/{README.md,state.json,checkpoint-watchers.json}，验收/限制/推送回执见audit/20261118-q4-checkpoint-recovery/。本轮stage/activate/resume已消费禁重放，后续仍本地验证commit→同步100验收→push。

## 2026-11-18｜列表接口避开大快照锁等待

用户反馈`/runs?page=1&page_size=20&group_tasks=true`超时。根因是活动列表生成摘要需获取RunState锁，后台大快照复制会长时间持有此锁；`/active-work`还会在持任务注册锁期间等待该锁，间接阻塞列表。此前日志复制约47秒/写入约57秒，前端10秒超时。

本机cea61d3、928845b维护脱离运行状态的列表缓存：状态锁可立即取得时生成并复制瘦列表摘要，锁被占用时返回最近缓存；active-work同样非阻塞读取且保留真实worker存活状态。active_run_ids先释放注册锁再查询状态。完整答卷、轨迹、持久化及评分协议保持。

本地最终1643项PhotoBench测试全部通过，145项定向测试、Ruff/格式/Mypy及diff检查通过。一次全量中的1秒并发读测试曾计时失败，定向及最终完整重跑通过，未更改该测试。100现有两份部署适配通过精确patch合并并核AST只涉及本次10个方法；适配后的候选、源SHA和单次安装脚本见仓库deploy/photobench/20261118-runs-nonblocking。

100两套实际运行组合的锁占用验证均通过：1013题完整摘要约12ms，20次占锁读取最大0.4ms；resume组合84/84测试通过，shared组合80通过、4项旧构建恢复测试失败在原服务器基线逐项复现（该实例未合f8a75a5，本轮保持）。验证脚本最初inspect.py遮蔽标准库，已本机59f7d99更名preflight.py后重验，原失败记录保留。

8份发布资产SHA一致，17:19已同步三份suites.py；云端型号、清理与原scope续跑适配保持。只空闲重启8772，PID1497→2371432，93条索引扫描ready，LAN列表200/0.023秒。8872/8874源码已同步，进程194991/194992及三产品PID保持；后端修复尚未在两旧进程激活。8874仍有Qwen任务；8872活动注册中有失败残留且产品有历史pending/receipt，后续核实际任务与检查点后再单独激活，禁止直接重放install.py。完整回执与push状态见产品项目audit/20261118-runs-list-nonblocking/{README.md,delivery.json}。

同期只读发现Q4原续跑90f21f于16:48:20 failed/qa_eval：落盘等待180秒超时（target1064/written1063），51份答卷保存。Qwen211c0d仍running/finalizing，11034/11034材料、失败0、QA0。Q4旧wake7f3c8516于16:46因连续三次探测失败已排队，尚无完整故障恢复；Qwen20927监控继续。下一次接续先读本次Q4失败metadata与原probe错误，检查大快照耗时、内存/磁盘及实际产品任务，修复后保留51答卷续原scope；新wake先于resume，禁止重放launch。不能以列表优化已发布当作Q4落盘故障已解决。


## 2026-11-18｜Marvell迁移候选文件已备，尚未导入

用户授权先准备配置，之后决定是否切换。5430262先本机验证commit，再同步100的/home/realmagic/xy/diagnostics/marvell-migration-20261118，三文件SHA及系统libnm解析/verify通过。候选绑定enp12s0及MAC，保持192.168.0.100/24、网关/DNS，autoconnect=false；只存用户目录，尚未导入或激活，因系统写入需sudo认证。管理员可sudo python3该目录prepare.py --install，仅导入不启用。原两接口地址、默认路由与活动连接逐项核保持。

正式切换前仍须指定Marvell验证当前网段/网关、安排断网窗口及调整固定Intel网口的watchdog；本轮未实施。证据、导入命令与推送回执：产品项目audit/20261118-marvell-migration-prep/{README.md,delivery.json}。温度采集继续，主线程业务/测评未操作。

## 2026-11-18 11:56｜100独立温度与网卡取证已启用

侧聊按用户要求新增30秒采集器，代码a2a41b9先本机验证commit，再同步100逐SHA验收。用户单元sentrix-nic-thermal-recorder.service已enabled/active，PID459313、NRestarts0、Linger=yes；连续样本间隔30.05秒通过。日志/home/realmagic/xy/diagnostics/nic-thermal/logs/samples.jsonl，32MiB×4轮转约128MiB，记录hwmon/GPU温度、两物理网卡累计收发/错误和PCIe/AER状态。Intel enp11s0无温度接口，明确null，另一网口PHY/MAC约56℃不能代替。没有切网口或重启业务；主线程测评与监控继续自行管理。

同主板/型号/固件上游报告指向PCIe异常状态变化，尚无该报告最终确认修复；单独pcie_aspm=off已被报告者更正无效。可先用现有Marvell10G口作对照，尚未实施。完整证据、来源、停止命令及推送回执：产品项目audit/20261118-nic-thermal-recording/{README.md,delivery.json}。

## 2026-11-18｜题集评分默认值与运行配置回显修复

用户截图显示开放式勾选，但8872/8874两活动续跑90f21f/211c0d实际均冻结atm_qs_v1，未改错评分。根因是启动表单按相册ID的atm-前缀选默认，日期命名相册误判；表单也未绑定运行快照。改为按所选qa_set自动选择，ATM系列默认QS，其他PhotoBench题集默认开放式；保留人工单选/双选。运行中独立读取精确run的冻结配置及GT开关，标明run/题集；查看历史记录时保持活动任务配置。结果与补评初始标准也优先读取冻结值，轮询保留人工选择。未读取到冻结配置时显示读取状态或错误。

67项相关前端测试、生产构建及lint通过（保留现有警告）。全前端832项中831通过，ChatView旧夹具缺模型接口响应的1失败在未修改def8737基线独立复现。变更仅前端；部署脚本publish.py先校验源文件SHA及两run冻结配置，更新静态资源与对应源码，不重启服务。业务1b2c04f已先本机commit后发布1008772/8872/8874；三份源码副本15文件与两份实际静态目录4文件共19项SHA通过，旧资源保留。六服务PID与两run冻结评分SHA保持，无重启。三端HTTP6资源与Edge页面验收通过：两活动任务ATM勾选、8874查看历史仍绑定当前运行配置，8772切PhotoBench后开放式勾选。推送回执见审计delivery.json。审计：产品项目audit/20261118-scoring-selection-ui/；两run监控及三模型顺序保持。

## 2026-11-18 11:15｜GPU恢复，ATM1013两轮从原scope续跑

11:06核149内核/595.99.02两张3090可用，WeMM及8291/8294健康。原Q4误CPU装载已按原unit恢复GPU1/PID191317，Q4_K_M、4×16K和Q4_OK验证通过；十条旧pending及其他任务保持。

发现突然重启的状态恢复未写scope_build，原resume接口会拒绝完成未完构建。本机f8a75a5依据中断full/build原阶段判定未完构建，并保留显式finish_incomplete_build和完整导入条件；192项本地、83项远端发布组合及Ruff/格式通过。远端现有型号/清理适配保留，AST核仅resume_run变化。独立复制测评应用至/home/realmagic/xy/experiments/atm1013-20261118/resume-release，仅8872/8874切专属unit，PID194991/194992；原共享源码SHA及8772/8191 PID11560/1497保持。

两次resume各一次成功，原scope/11034导入及阶段成果保持，原中断历史和快照指针SHA不变。活动Q4为20261118-111519-2026.11.17-atm-sources-addresses-current-resume-90f21f，父e53f3c；Qwen为20261118-111529-2026.11.17-atm-sources-addresses-current-resume-211c0d，父7fcbea。均running/pipeline_processing且worker_alive，QA0；Q4材料11034/11034、finalizing，Qwen从3778/11034继续。执行器mode=reuse，属于此次full原scope的检查点续跑；1013题、reasoning/graph、QA4、3660秒及冻结ATM/Judge配置保持，未重新导入。

GPU wait4482eaa1已消费；新Q4 wait7f3c8516-d9f0-48b8-8230-e27245052c75/PID20926，Qwen wait0bda9c0b-9a26-4661-90a8-ed6dd6a08932/PID20927，注册先于恢复，每60秒精确探测活动映射。原launch-response保留，active-runs.json和resume响应明确父子ID。四份脚本逐SHA验收；本地commit→同步验收完成，推送回执见audit/20261118-atm1013-three-model-full/resume-delivery.json。侧聊16题包未发布100。

接手读同目录state.json、README.md、resume-active-runs.json和本次事件，禁止重放安装/模型恢复/resume或原launch脚本。异常保留完成数据继续原scope；Qwen1013答卷/评分/两导出验收后才接豆包独立full新scope，并先注册wake。三轮验收、比较及监控收尾仍待完成。

## 2026-11-18 10:55｜100连接恢复，GPU驱动阻塞原两轮

SSH于10:50恢复，现场内核6.8.0-149-generic没有NVIDIA模块；两张3090在PCI可见，nvidia-smi失败。现有595-open模块覆盖到138，当前595.84；仓库提供149模块，安装模拟涉及16升级/2新增/1旧模块移除。realmagic无sudo免密权限，需管理员在100本机完成驱动恢复，命令及影响见audit/20261118-atm1013-three-model-full/ADMIN-RECOVERY.md。未安装系统包、改启动内核或重启主机。

两原run均interrupted/QA0；Q4 e53f3c原11034份输入全部processed、停finalizing，Qwen 7fcbea为3778/11034、失败0。两端active-work空、recovery-plan均can_resume/剩余1013；同scope原资产、导入、记忆与历史保持。DB图片数包含派生帧，pipeline_receipts running是重启前记录；当前WeMM无CUDA、8291/8294启动失败循环。Q4模型曾CPU装载，恢复时须核空闲/GPU1容量后按原unit恢复CUDA，不能直接开始QA。

旧Q4/Qwen/连接恢复wait全部消费。新GPU恢复wait 4482eaa1-4e6e-4cc3-a353-31e2fd564bef、本机PID15388，每60秒只读检测，两卡恢复/探针连续三失败/24小时通知原线程。ab3ba89先本机语法检查commit，再仅新增100实验目录inspect_reboot.py，远端业务Python编译、真实检查点/DB/GPU路径与SHA验收通过；业务/配置及侧聊16题包未发布。推送状态以gpu-recovery-delivery.json为准。

GPU恢复后先核两卡/磁盘/WeMM/产品/原pipeline与turn，再新注册两wake、一次性resume原run并finish_incomplete_build=true，继续原full的检查点。保留原launch响应及历史，新run_id须写显式活动映射并更新probe；原3660秒/QA4、reasoning/graph和冻结ATM/Judge保持。当前尚未resume。Qwen1013答卷/评分/两导出验收后才接豆包，Hard子集不启动。详细证据见reboot-inspection.json、reboot-recovery-plans.json和state.json。

## 2026-11-18｜本地ATM小测扩为16题

按用户要求保留原10题，在新版本加入六道涉邮件题，数值/列表/开放各新增2道，合计5/5/6。覆盖单封事实、日期、多邮件金额汇总、证据不足拒答、邮件ID交付和Hard邮件关联照片。新版候选2026.11.18-atm-smoke-16q/atm-bench-smoke16，默认atm-smoke-16q，另有email6及旧10/main6/hard4入口；16图/3视频/9邮件独立物化，官方原行、评分指纹、来源ID/时间/摘要/原对象完整。

原固定10题包及主集1013未改。现行加载与五入口预检0错误0提示，9邮件解析/元数据、28材料SHA、来源/媒体评分和临时重建45文件一致通过。生成器scripts/extend_atm_smoke.py；审计产品项目audit/20261118-atm-smoke-email-expansion/README.md。仅本地维护，未同步100或运行模型；主对话活动run和连接恢复监控保持。

## 2026-11-18 06:06｜100局域网不可达，已挂连接恢复监控

06:03两条精确run监控同时因连续三次SSH失败触发，通知不代表测评终态。现场核100 SSH报No route to host/超时，HTTP8874超时，ARP未解析；本机192.168.0.52可连路由器及153，100实际主机/服务状态暂不可读。

最后可信快照为06:00：两轮11034份材料均导入成功、失败0；Q4媒体处理1655/11034，Qwen996/11034，处理失败0；均仍pipeline_processing、尚无答卷。当前不能确认仍在运行或已停止，豆包继续等待Qwen完整验收。

两条同因事件已在本轮合并处理，旧Q4 wait 5342e16f与Qwen wait b8a0d160均消费；若旧Q4通知随后到达，先读state.json，勿重复登记或重启。新连接恢复wait为0ee206e8-a68c-4528-a46b-7c2368d88d7d，本机watch_connection.py/PID60866每60秒只做SSH连通检查，SSH恢复或24小时仍不可达时通知同一主线程。旧精确run watcher已正常发事件退出。

本轮无远端写入/重启/恢复请求；本机新增连接监控7aff59d已语法检查、实际不可达路径验证并commit。100不可达，本轮后续push暂待恢复后核验；已发布上游仍0f61b14。该监控仅本机运行，不向服务器传凭据。证据与精确run见audit/20261118-atm1013-three-model-full/{connectivity-incident.json,state.json,connection-watcher.json}。

连接恢复后先核两run、产品pipeline/Agent、GPU、磁盘；仍运行则注册新精确wait并恢复run watcher；若失败按既定授权保留阶段成果修复续跑，禁止重放launch。Qwen完整1013答卷/评分/两导出验收后才接豆包，三轮比较与最终收尾仍待完成。

## 2026-11-18｜ATM1013两轮全链路已启动并接入精确监控

按用户授权，100的8872 Qwen3.5-4B Q4_K_M与8874 qwen3.7-plus并行运行。均为atm-bench1013题、full新scope/新记忆、reasoning=true、graph_enabled=true、QA并发4、单题等待3660秒；统一冻结ATM native_v2/v5及豆包2.1 Lite Judge，GT图片/元数据关闭。Qwen正常结束并验收1013答卷、评分及两导出后，8874接doubao-seed-2.1-lite；豆包尚未启动。

|阶段|精确run ID|现阶段|
|---|---|---|
|Q4_K_M / 8872|20261118-043111-2026.11.17-atm-sources-addresses-current-e53f3c|running / photo_import|
|Qwen3.7 Plus / 8874|20261118-043128-2026.11.17-atm-sources-addresses-current-7fcbea|running / photo_import|

两端实际型号发现、冻结1013/SHA、共同评分提示词一致、全11034份材料路径预检通过。新scope各含run时间，model_deploy均done，原答卷未复用。导入最新processed分别7663/1970，失败0（仅启动快照，后续读精确run）。Q4权重SHA及Q4量化、4×16K、Q4_OK短生成通过；仅恢复既定GPU1服务并刷新8291容量探测。云端推理声明128K；系统reasoning开关与原生模型thinking分别记录，实际QA轨迹待进入答题后复核。

资源准备：六份离线部署DB冷备压缩并解压SHA一致，释放10.55GiB；原始冷备路径各附.db.archive.json可恢复映射。首次系统Python缺file_digest在首文件读阶段失败，无写入；改业务Python3.12完成。8874历史results迁至/home/realmagic/xy/experiments/atm1013-20261118/cloud-results，159文件/2117301466字节SHA逐份一致，原路径保留软链接，空闲重启8874；历史数据保持。未变更8772/8191。

本机ops先语法检查与commit（27d250c/da5c55b/73d2c3c/516894c/c1346a6），再传100；五份远端脚本SHA与已提交本机一致，实际运行验收通过。业务源码仍d44e834+100-reasoning-context，本轮仅实验运行准备与专属模型unit覆盖；单次prepare/launch禁止重放。代码推送状态以审计delivery.json为准。

wakecodex本地listener健康；Q4 wait 5342e16f-9ed6-47c5-a3a7-03c415cade8a、Qwen wait b8a0d160-bad2-4c64-b6bc-71cbf90aa009，均在任务前注册，回到主对话01a1e782-4348-7b70-8dc5-aec2d8e20c82。本机watcher PID43440/43441每60秒读取精确提交快照与worker，终态/worker异常/型号变化/磁盘低于1.5GiB/两小时无进度/连续三次探测失败写事件并唤醒。token仅存本机私有目录。

接手先读audit/20261118-atm1013-three-model-full/README.md、state.json及对应事件/latest。异常按用户授权定位修复并保留已完成答卷和分数续跑；修复续跑前注册新wait。Qwen验收后先注册豆包新wait，再建独立full新scope；三轮完整验收、比较及监控收尾仍待执行，不重复POST。当前按事件监控等待，未完成测评不报最终成绩。

## 2026-11-18｜1013题全集与三模型全链路测评交接

默认题集atm-bench已替换为完整1013题，博物馆题首答案ID日期0904→0905，其他1012题保持；主集相册2026.11.17-atm-sources-addresses及名称不变。先本机数据提交6476d88后发布100共同题包，8772/8872/8874真实API均为1013题、预检0错误，六图逐端18次访问通过。发布资产见deploy/photobench/20261118-atm-main-default，工作区完整证据audit/20261118-atm-museum-qa-correction/replacement/。旧发布脚本已消费，不重放。

用户已授权主对话执行以下三轮，侧聊只交接、尚未启动新run：

|端口|模型|顺序|
|---|---|---|
|8872|本地Qwen3.5-4B Q4_K_M；须读取现有模型路径和配置确认身份|与8874首轮并行|
|8874|qwen3.7-plus，以接口发现的实际型号为准|与8872并行|
|8874|doubao-seed-2.1-lite，以接口发现的实际型号为准|Qwen正常完成并验收后启动，可与尚未结束的8872并行|

三轮统一：原名atm-bench主集全部1013题、ATM-Bench评分、全链路（导入→处理→记忆构建→答题→评分）、推理模式开启且放开普通模式限制、图记忆检索开启；不复用旧答卷冒充新全链路。Hard31独立，不擅自扩大本轮1013题口径。保留来源引用、候选/交付媒体、全部Agent/工具轨迹、运行配置/型号/窗口/耗时与评分批次快照。正式启动前检查产品与测评真实空闲、三个材料类型的导入/处理和GT可解析；全链路用新相册/新scope隔离旧结果，按现有流程执行。8872此前8100离线，应先恢复所需Q4_K_M实际模型服务并检查GPU容量；不要误用旧BF16。云端推理窗口已支持131072；本地按模型服务实际容量和探测结果配置，记录有效值。

主对话先读T004/T006最新现场和当前OpenAPI再生成请求，冻结三轮相同评分配置。两个首轮确认运行正常后用wakecodex对精确run注册终态/异常通知，回到主对话01a1e782-4348-7b70-8dc5-aec2d8e20c82；注册须在启动相关任务或watcher前完成。用户授权异常时及时定位修复、保留已完成答卷并从失败/未完成阶段续跑；源码仍先本机验证commit→同步服务器验收→push。每轮正常结束核对1013题答卷及评分完整性、导出和引用轨迹，Qwen完成后按上述队列接豆包，三轮全部结束后汇总对比并收尾对应监控。wakecodex的queued只表示通知已受理，验收以实际run证据为准。

## 2026-11-18｜推理128K已发布100并完成长输入验收

业务d44e834先本机验证commit，再同步100三产品8191/8291/8294各五份源码；8191保留已核运行适配。云端两产品以独立环境声明推理131072，普通配置分别8192/16384保持；8291本地保留16384及实际容量探测。输出上限和原生thinking设置保持，95%压缩阈值按回合窗口计算；普通/推理客户端相互独立。

85项专项与8191运行组合12项通过；既有health/live满线程池失败在19a0c57原基线复现。真实qwen3.7-plus接收18052输入token、返回CTX128_OK/5输出token，应用估算73554、窗口131072；同输入普通16K预算拒发。长输入实际发出两次，首次回包后验证脚本取错统计字段，第二次正确保存回执；未新建正式QA/Judge或产品会话。

首次安装在8291启动等待失败：WeMM8102此前02:23已退出。按原user unit/GPU0恢复PID1303192后，六接口及独立verify.py验收通过，没有重放install.py。产品PID1294308/1304176/1304183，测评1298797/1298799/1298801；测评源码/静态保持，当前三份runtime_connection仅更新产品版本。原历史文件清单、聊天SHA、三库核心数量及8291十条pending保持。五份源码逐文件SHA和未覆盖源码对照通过。云端8772/8874型号发现均通过；8872原8100离线保持。

部署根/home/realmagic/xy/deploy/20261118-reasoning-context128-d44e834；产品版本d44e834+100-reasoning-context。原install已消费禁重放，后续只读验收可用verify.py；验收脚本提交a1db4af/50a1415。审计audit/20261118-reasoning-context-128k/README.md，推送回执见delivery.json。

## 2026-11-18｜当前部署模型显示实际型号已发布100

业务85fb2d8先本机验证commit，再发布1008772/8872/8874；仅两份测评后端、共用ModelSelect及静态产物局部覆盖。新增只读/current-deployed-model，从产品diagnostics的vlm_base_url读取实际模型列表，复用交互执行端点解析；默认选择显示具体型号，地址变化清除旧型号，未发送生成请求。

31后端/18前端专项、生产构建/lint、OpenAPI与模块/能力登记通过。空闲重启三测评PID1045894/1045896/1045898；配置/聊天/历史与三产品PID/源码/静态及数据库保持。8772接口实际doubao-seed-2.1-lite，8874实际qwen3.7-plus；8872原8100无监听，接口明确连接失败。Edge8874聊天与重测均显示“当前部署模型 · qwen3.7-plus”，三端首页及资源SHA一致。安装脚本已消费，禁止重放；审计audit/20261118-deployed-model-label/README.md，推送回执接delivery.json。

## 2026-11-18｜时间与历史轨迹、模型发现修复已发布100

业务89e12d4先本机验证commit，再仅前端同步1008772/8872/8874；三测评PID806459/806461/806463保持，无需重启。比较时间到秒、日志时分秒，原值悬停可查；重复查看当前重测记录强制重读并滚动到答案，加载和错误明确显示。重测/聊天复用/current-model，单型号自动选择、多型号下拉，取消手填模型名；旧聊天保留endpoint_model避免发现后重置。

全量824前端及最终90项专项、构建/lint通过；Edge已完成记录b33a4a7c连续两次显示原$252.07回答与74事件，取消记录35事件也通过，重测/聊天均自动选择qwen3.7-plus，相册日期规范。8源码+3产物逐文件及三端HTTP SHA通过，三产品PID/源码/静态、原配置/聊天/历史文件与数据库计数保持，新增Agent/Judge0。审计audit/20261118-interactive-history-models/README.md及final-verification.json；安装脚本已消费禁重放，推送回执接delivery.json。

## 2026-11-18｜统一媒体与单题/聊天弹窗已发布100

本机业务d2a4fb5与关闭后状态刷新fc7f1a8已提交，发布100三测评8772/8872/8874，PID806459/806461/806463。左侧统一候选及交付的图/视频/邮件，右侧GT去重与正确答案，重测入口和JUDGE输入在展开底部并排。重测/聊天共用固定弹窗与轨迹；背景/Esc/完成均保持，×取消未完成任务并保存日志，关闭后轮询取消终态，避免聊天按钮灰置。

最终818前端/构建及本机156交互执行取消、85运行聊天版本等专项通过；100现役组合151/156通过，5失败与未加补丁基线一致，新增0。Edge邮件左右布局及真实重测/聊天日志动态增长、背景/Esc保持、×取消和重新查看轨迹通过；完整IO在产品终态trace返回后展示。原910/148/128历史文件及配置/旧聊天SHA保持，三产品PID与源码/静态保持。局部覆盖SHA和两阶段发布回执见audit/20261118-media-retest-dialog/README.md；install.py均已消费，禁止重放。推送回执见同目录delivery.json。

## 2026-11-18｜ATM邮件、Hard及官方地址已发布100并完成smoke

本机先提交ed74c7b，再同步100三产品8191/8291/8294及三测评8772/8872/8874；后续GT预览20cfede、来源导出eddab4d和独立产品构建40365bb均先本机commit再发布。共同基版为ed74c7b+100-atm-sources，局部覆盖SHA单列，原运行适配保留。最终产品PID339845/354041/359960，测评PID447003/447073/447085，六服务健康且测评空闲。

完整题包已同步并注册三个catalog：3759图/533视频/6742邮件；相册“ATM-Bench 主集 / Hard · 图视频与邮件”，atm-bench-hard为31题，atm-bench-hard-email为涉邮件8题，atm-bench-email为381题。Hard使用完整资料库。官方main1013保留一处答案/证据ID冲突，另列atm-bench-validated的1012题，不猜改原题。11084文件SHA及4292官方媒体原件对照通过，原图视频独立副本嵌地址，导入优先读取。

三产品均完成6742邮件预检、隔离scope导入4封及幂等复用、text_ann非空、官方地址读取。8874真实5题执行/评分及两种导出验收通过：邮件2题QS50%，Hard3题QS54.1667%；仅功能小样，不是官方全量成绩。Cairo漏算吉萨住宿、酒店题未识别“第二家”，Fancett照片交付全中但未检索邮件，原回答与失分保留。页面已核邮件原文、三阶段来源指标、Hard三图GT及产品导入入口。首轮导出漏字段已修，继承原2题答卷继续，未重跑。

三库备份后0034迁移；原903/148/83份历史结果大小/mtime、19份保护配置/聊天中的16份SHA及8291十条旧pending保持；三份runtime_connection仅更新产品版本，smoke后未再改配置。部署前WeMM8102已停，按原unit/模型/GPU0恢复PID353178，未切换主模型或重启BGE；8191 tokenizer沿用原侧车目录。独立产品前端保留VITE_LEGACY_LOCAL_AUTH=1，认证入口已实开通过。

本机543后端专项、41来源契约、108评分专项、806测评前端及新增组件、导出44项、GT预览5项和两端构建通过。mypy74处/30文件与原基线错误集合相同、新增0；产品31前端及11助手工具等既有失败保持，完整CI未全绿。发布与smoke单次脚本已消费，禁止重放；没有启动正式全量。推送回执及全部证据见audit/20261117-atm-sources-implementation/README.md、100-release/final-verification.json及delivery.json。

## 2026-11-17｜推理模式及启动栏开关已同步100

用户要求提交并同步100。本机前端66a0883及发布适配0608d54提交后，已更新三产品8191/8291/8294及三测评8772/8872/8874，并在空闲时重启。共同发布版本0608d54+100-reasoning；产品PID3352746/3352864/3352946，测评PID3353343/3353356/3353649。

前端开关与删除相册并排、默认开启；取消恢复普通模式工具/执行限制，产品持久执行器保留所选reasoning。保留8191的16K上下文合同、8291十条历史pending隔离、三测评原模型切换/鉴权适配及聊天跨版本修复。仅更新产品各8个、测评10个Python文件及两份共用静态目录；源码与37份发布文件逐项SHA通过。

377项本机发布组合、8291/8294各8项执行器回归通过；本轮前端806项/构建通过。六服务真实健康/版本与三个在线OpenAPI默认值通过；三个真实页面开关可见，8872默认开启及切换通过。903/148/83份历史结果大小/mtime保持，19份配置/聊天中仅三份当前runtime_connection更新产品版本，其余SHA保持；三库五张核心表数量、旧pending完整行SHA保持。无新增真实Agent/Judge请求。

服务器原目录继续使用，模型和数据路径保持；未启动或切换模型。审计audit/20261117-reasoning-100-release/README.md及acceptance.json/live-api-verification.json。install.py已经执行，禁止重放。验收后按既定顺序push，最终上游回执接delivery.json。

## 2026-11-17｜推理模式100配套发布准备

用户明确要求提交并同步100。前端66a0883已提交；核100三测评空闲、三产品无新任务，8291保留十条隔离旧pending。现役源码与b2030e1及已提交运行补丁、a730335聊天修复逐文件一致。
已在本机重建最新源码加8191/8291/8772既有运行适配：377项组合专项、8291/8294各8项执行器回归全部通过；前端806项与类型/构建沿用本轮已验版本。
发布资产在deploy/photobench/20261117-reasoning-100，先提交后同步；仅更新产品各8个、测评10个Python文件及测评静态构建。版本更新当前配置，旧run/聊天快照保持；99zz最后追加测评版本覆盖。实际发布结果接后续回执，单次install.py不得重放。

## 2026-11-17｜推理模式开关移到启动栏

按用户截图，将「开启推理模式」移至启动栏右侧，与「完成后删除相册」并排，采用同尺寸边框复选框，开启为蓝色。默认开启，取消时通过已有 reasoning=false 请求恢复普通系统执行限制；运行期间锁定。full 显示两个开关，reuse 显示推理开关，build 隐藏推理开关。窄屏将开关组放到启动按钮下方，保留换行。

测评前端67文件/806项通过，类型与生产构建通过；lint成功、17条既有警告。Edge真实页面已核默认/切换、桌面及390px窄屏布局。此次为本机前端修改与提交；未同步服务器或push。100尚需将此前133434f的产品/测评后端配套发布，才能提供实际可用的新模式开关；后续按本地commit→服务器验收→push执行。

审计：`audit/20261117-reasoning-toggle-placement/README.md`。本次无真实Agent/Judge调用。

## 2026-11-17｜聊天跨产品升级修复已发布三个测评

已确认8874旧会话冻结f864868、8772两条旧会话冻结1c7ecde+100-local，产品已b2030e1；
8872无已有聊天。新提问按当前产品实际版本准备并逐回合保存快照，原创建快照和历史回合保持；
正式测评/续跑及其余冻结配置继续校验，版本错误显示expected/actual。

本地源码a730335，215项本地及24项发布组合回归通过，ruff/格式/OpenAPI/模块与能力检查通过。
只同步两个源码文件到b2030e1加原运行适配，三测评空闲重启为8772/PID3171616、8872/PID3171627、8874/PID3171932；
版本标记a730335+b2030e1-chat-hotfix。19份配置/聊天SHA、历史结果大小/mtime及三个产品PID保持；
重启运行锁按预期变化，8291十条旧pending未执行。三个端口真实诊断+隔离会话副本模拟回合通过，新增真实生成/Judge0。

用户可刷新后主动发送上一条问题，原失败请求没有重放。133434f推理模式仍待配套发布。
审计：audit/20261117-chat-revision-fix/README.md；发布资产：deploy/photobench/20261117-chat-revision/。
首轮版本覆盖顺序及锁检查已修正，只读最终验收通过；两份单次部署脚本禁止重放。
本轮验收后已快进push至upstream/feature/photobench-evaluation，最终上游核对见审计delivery.json。


## 2026-11-17｜聊天跨产品升级版本校验修复

100三组当前产品/测评均b2030e1。8874旧会话要求f864868，主动追问在模型准备时被拦；
8772两条旧会话仍要求1c7ecde+100-local，同样受影响；8872无聊天记录但共用校验实现。
聊天每次主动提问从该会话产品地址读取实际版本，按本次快照校验并逐轮保存。
创建快照及历史回合保持；重启恢复只对账已发送请求，正式测评/续跑继续严格冻结。
版本错误补充expected/actual；其余产品设置和嵌入配置继续校验。

215项本地交互/执行器/任务回归、发布组合24项通过；ruff与OpenAPI/能力登记检查通过。
服务器已发布的8772运行适配也被8872/8874使用，SHA与已提交补丁重建完全一致。
局部发布基于b2030e1加该适配及本轮补丁，仅两个源码文件，推理模式133434f仍待配套发布。
发布资产 `deploy/photobench/20261117-chat-revision/`；先本机commit，再同步三个测评并验收。
远端实际结果见工作区 `audit/20261117-chat-revision-fix/README.md`，本条发布状态待回执补齐。


## 2026-11-17｜测评默认推理开关已在本机完成

用户要求测评接口增加默认开启的推理模式，并放开测评限制。整套 full/reuse、单题重测及聊天
接入 `reasoning` 布尔值和前端开关；开启后全工具、五类调用次数不限，执行墙钟至少3600秒，
测评默认等待3660秒。关闭采用普通模式；原生模型thinking设置、召回宽度、交付和上下文配置沿用现状。

修复下文psh合并记录中的后台执行器丢失模式问题：模式存入trace并从数据库恢复，
启动失败也保留；相同幂等键换模式返回409。云端模型的角色/历史/评审/压缩设置保留。
续跑、内存画像重放继承原模式，历史缺字段按普通模式；新重测默认开启，聊天可逐轮切换并保存。

最终377项专项、测评前端805项、两端类型与构建通过；ruff、11条分层契约及OpenAPI/地图通过。
全仓一轮4192通过/20失败、产品前端155通过/31失败，失败集合与既有基线一致；mypy74处错误内容一致。
既有格式/两项端口可达性仍失败，完整CI未通过；本轮覆盖率89.37%，分母与旧日志不同，不作增量比较。
完整CI后补的启动失败和重放模式断言均在最终377项中复验。

当前仅本机修改验证并提交，100仍为上次发布b2030e1；未SSH、部署、push或发起真实QA/Judge。
后续发布须配套更新产品与测评后端。实现见 `docs/refactor/2026-11-17-evaluation-reasoning.md`，
证据见工作区 `audit/20261117-evaluation-reasoning/README.md`。

## 2026-11-17｜psh集成版100发布完成

本机64d7348后，运行适配先验证并提交b2030e1，再发布100三产品8191/8291/8294及三测评8772/8872/8874。
完整源码释放于/home/realmagic/xy/releases/psh-b2030e1-100-20261117及8191/8291/8772后缀目录；
所有测评使用已验证8772公共运行适配，产品8191保留上下文/诊断、8291隔离十条历史pending。
活动测评配置为/home/realmagic/xy/deploy/20261117-psh-release/runtime-config各端口，更新产品版本标识；原配置及数据/结果路径保持。

三真实数据库副本迁移幂等、正式0033升级、远端邮件4专项、六服务1474源码/静态文件SHA与页面资源、共享语音真实样例识别通过。
原五树4360文件、285配置/核心结果及五张核心表原列行SHA保持，十条旧pending未执行；无新建真实QA/Judge，健康诊断包含模型探测。
本地组合8772的161项通过，产品88项通过/3项既有嵌入连接失败，独立上下文/历史隔离认领通过。
共享SenseVoice8107/PID2642591新启；原WeMM8102恢复既有unit/GPU0/PID2617162。新环境仅加sherpa-onnx1.12.40，引用旧环境，原venv必须保留。

产品PID8191=2632234、8291=2621118、8294=2618666；测评PID8772=2632672、8872=2621453、8874=2619074。
8291/8872转用户systemd管理并启用。8874模型健康，8772原18106上游429、8872原8100无监听；模型选择保持。
三个测评均空闲；本轮未push。完整ATM邮件GT/评分与上游reasoning持久执行器问题继续待办。
数据库备份在部署根backup各产品端口，约7GiB，/home剩9.4GiB。所有单次发布脚本禁止重放。
审计：/Users/jzousz/Documents/ChatGPT/产品项目/audit/20261117-psh-100-release/README.md。

## 2026-11-17｜本机测评分支合入 psh

用户在侧聊授权本地合并。合并前为 `feature/photobench-evaluation@fcbef3b`，工作树干净；
本次合入 `upstream/psh@a5daf5192fecf9059a6e922f41d5e279241eec53`，包含
`upstream/main@28ba9e6d5b58a9068224e0f52d4e175ef7f28bb0`，main 到 psh 差异为 0/1。
邮件原文导入及回读、共享语音服务、云端模型角色配置和推理开关代码均已集成，
原有单题重测、聊天、跨相册硬链接共享与完整轨迹保留。本轮仅本地集成，服务器发布和 push 待后续安排。

双方占用 0032：保留主线 `0032_text_sources.sql`，流水线回执移至 `0033_pipeline_receipts.sql`。
按实际表结构兼容已发布的旧 0032 回执记录，原版本记录和时间保持；新旧两条真实 Git 快照所建数据库
升级、重复执行及原表记录核验通过，另有 7 种历史基线回归。
新增邮件契约测试改查 `text_vector_repository`，对应此前已修复的文本/视觉分表；
补核同正文不同邮件 ID、不同相册分别建记录，硬链接共用且原文可下载。

专项 119 项通过，涵盖迁移、共享媒体、邮件导入/回读、模型配置、单题重测和聊天；
PhotoBench 前端 803 项与类型/lint/构建通过，两端前端构建通过。
完整 CI 首轮为4168通过/22失败；其中1项是测试子进程未指定当前源码路径，1项是上述
新增邮件测试查错表，均已在最终专项复验通过。余20项在fcbef3b逐项复现。
产品前端155通过/31失败，与psh完全同集合；原分支157通过/29失败，新增2项来自共享ASR变更。
全量mypy为74处（原分支71、psh111），按路径和错误内容比对未新增两边都没有的错误。
覆盖率78.70%低于85%；既有格式及两项端口可达性仍失败，完整CI未通过。
详细日志及两个分支对照见工作区审计 `audit/20261117-psh-merge/README.md`。
模块地图、能力登记、两份 OpenAPI 和 11 条分层契约通过；推理字段 OpenAPI 已同步。

已知上游问题：推理开关只传入请求阶段的 RunTurn 构造，持久执行器重新构造默认 RunTurn，
未保存或恢复 reasoning，因此当前实际执行仍使用默认模式；本次保留上游行为，未扩展修复范围。
直接构造推理模式还会覆盖 cloud profile 的预算；全工具说明仍偏照片/中文，邮件核验只取最近两段原文。
完整 ATM 邮件题包接入、邮件引用评分与界面展示仍待实施，不等于原文导入通路已覆盖全套测评。

## 2026-11-17｜单题重测与聊天已发布100三实例

新增单题重测与原题模型比较、相册/模型选择的聊天页签；题库搜索、标准答案/两类GT对照、候选/交付媒体及完整轨迹接入。多轮题完整执行，GT不进入Agent请求，历史答卷和总分保留。会话独立持久化，取消/重启按产品真实回合对账；模型准备与测评共享占用。

本机实现a41a312，版本独立标记54c5401，题库定向校验330d209，旧ATM请求适配142490b，最终选题加载修正92a82c0。所有修改本地验证并commit后同步。1008772/8872/8874均已更新并空闲重启，最终测评版本92a82c0，PID847057/848012/848367；原产品PID与配置保持。保留各实例已有模型管理/相册匹配/持久化/报表/轨迹覆盖层，31个源码文件及前端构建逐文件SHA核验通过。测评版本通过PHOTOBENCH_BUILD_REVISION独立记录。

本地PhotoBench后端全套、前端67文件/803项、14项交互后端及2项HTTP通过；三组合实际独立导入，各14后端/5页面测试及类型构建通过。make -k ci仍有同062ebab基线的71处mypy、两处端口可达性、旧格式/taxonomy问题；coverage缺sherpa_onnx，产品前端29项既有失败保持。模块地图、OpenAPI、分层契约、本轮ruff及新增关键文件mypy通过。

8772真实题库问答6候选/1交付、两类GT/完整轨迹和图片加载通过；自由聊天连续两问同conversation、刷新及重启恢复通过。8874真实自由聊天及显式Qwen3.7 Plus单题run20261117-125242-retest-d219e575已completed，1/1评分成功，原题比较/查看轨迹/返回原任务通过。8872模型8100离线，仅验聊天/模型/选题入口，需选择可用模型端点；8291的10条旧pending保持。

本轮验收已结束，三个测评active-work为空、产品无running；原242/22/18份配置和核心历史结果SHA保持。产品代码与共享媒体发布仍另算。审计及最终上游回执：`/Users/jzousz/Documents/ChatGPT/产品项目/audit/20261117-interactive-evaluation/README.md`、`upstream-verification.json`。组合可重建patch在deploy/photobench/20261117-interactive-100。四轮一次性部署脚本禁重放。

- 状态：本机实现完成；1008772/8872/8874已发布单题重测与聊天
- 创建：2026-11-16
- 最近更新：2026-11-17
- 相关任务：产品项目 T004、T005、T006

## 当前定义

在本机 `feature/photobench-evaluation` 集成最新主线及缓存分支功能，保留已有测评修复；
实现跨相册原始媒体共享，减少反复导入同一题集的磁盘占用。相册资产、权限和记忆独立。
轨迹展示改为调用节点与分源详情，保留已保存原文、逐步耗时及最新后端调用类型。
本机完成代码集成与验证；1008772/8872/8874现已发布测评交互功能，产品代码和历史测评数据保持。

## 当前状态

本机已合入psh/a5daf519（含main/28ba9e6），100三产品及三测评已发布0608d54+100-reasoning；推理模式持久执行、前端开关、聊天跨版本修复及原运行适配已上线。邮件通路与共享语音保持，完整ATM邮件测评仍待办。

主线合并提交 `b62b085`，第二父提交为 `2838a75`，已包含缓存分支 `f41237a`。
合并保留恢复请求与流水线超时、操作回执、图谱开关，以及 Katna 的线程安全处理。
迁移编号冲突按已发布表结构识别；11/16曾将流水线回执移至0032，本次合入邮件后改为0033，旧版本号记录保持。
共享存储及历史文件合并脚本已实现并验证，f8a830d 已推送上游同名分支。
轨迹节点、完整原文查看及 final_answer 实测计时已完成本机验证。edb271a 为功能提交，
7b81cdc 为样式提交；100 两端轨迹前端已局部发布。产品 final_answer 新计时代码未部署；轨迹、样式和部署记录现已推送上游同名分支。

## 需求与决策

- 同一 `media_dir` 中按 SHA256 组织共享内容，每个资产保留独立硬链接。
- 外部源文件独立；不复用跨相册的推理、记忆、图谱结果。派生视频文件仍独立。
- 通过操作系统链接数回收最后一份内容；文件锁协调多进程导入和删除。
- 旧数据按已登记哈希择机纳入共享；批量历史整理脚本默认只读。
- 不在远端直接开发，不干预正在运行的测评。
- 轨迹读取每次已保存输入及工具，debug/metrics/事件分源，未知字段和空值原样可查；
  相同上下文只折叠展示。后端只补终止动作计时和坏参数失败事件，不改模型请求与任务决策。

## 产物与验证

实现集中在 `apps/api/src/sentrix/infrastructure/media/storage.py`；导入通过仓储按哈希定位
已有原件，相册内去重规则保持。上传配额按设备号/inode 计算实际文件引用。
维护入口及边界见 `docs/runbooks/shared-media-storage.md`。
审计目录：`/Users/jzousz/Documents/ChatGPT/产品项目/audit/20261116-shared-media-integration/`。
主线合并的 342 项专项测试通过。最终共享存储、迁移、导入与 SSE 兼容专项 104 项通过，
其中新增共享存储 17 项覆盖多进程、HTTP 上传/重试/配额/删除和真实 SQLite。
磁盘实测：8 MiB 相同原件导入 10 个独立相册，唯一原件仍为 8 MiB，节省 72 MiB；外部源独立。
PhotoBench 前端 787 项通过；产品前端 157 通过 / 29 失败，失败集合与合并基线相同；两者构建通过。
后端全量首轮 4124 通过 / 21 失败；随后复核修正一项新测试对批量执行器的调用假设，
剩余 20 失败全部在合并基线复现。该基线只覆盖同样的离线 conftest，生产代码保持 b62b085。
ruff、11 条分层契约、模块地图、能力登记、OpenAPI 和新增文件格式检查通过；新增六源文件
mypy 通过。全量 mypy 为 71 错误（基线 73），无新增错误；12 个既有格式问题保持。
模块级可达性通过，两个包各有一项既有端口方法可达性问题。完整 CI 尚未全绿；详见审计验证。

轨迹变更契约见 `docs/refactor/2026-11-16-trace-node-view.md`。
本轮 PhotoBench 前端 798 项、RunTurn 与事件投影 110 项通过；构建及 lint 通过，
改动后端 ruff/mypy、模块与能力登记检查通过。lint 有既有警告；后端格式检查保留
两文件共 4 处既有格式差异，本次新增代码符合格式。真实浏览器 12 项检查通过，
含历史 7 节点/10 事件、整题字段值一致、最新后端 125ms 交付计时及手机无横向溢出。
审计目录：`/Users/jzousz/Documents/ChatGPT/产品项目/audit/20261116-trace-node-implementation/`。

## 坑点与已知问题

- 同事的缓存分支优化模型工具和槽位，原先没有实现跨相册磁盘复用。
- 缓存分支默认允许两次搜索，集成会影响未来测评条件；历史成绩与原部署保持。
- 先前核查发现缓存分支的超额并发槽位租约边界问题，本次尚未修改该机制。
- 全量 mypy 和产品前端有既有失败，必须记录本轮与合并基线的对照，不宣称全量通过。
- 真实语音模型与 GPU 模型运行未包含在本轮离线回归中。

## TODO 与接手说明

100三产品与三测评已发布b2030e1，跨相册共享从新导入起生效。后续完整ATM邮件测评与上游推理开关修复另行实施；部署先核对目标实例根、运行状态及
未提交改动，不得重放旧部署/恢复脚本。真实历史数据整理须先运行只读报告，再在维护窗口应用。
20 项后端既有失败涉及工具精简后的旧断言、启动探测隔离、健康探针、投影与可达性等；
产品前端 29 项和全量类型/格式旧问题列在 audit/verification.json，不混称本次功能验证通过。

## 工作日志

### 2026-11-17｜按用户要求推送上游分支

已将 edb271a 轨迹功能、7b81cdc 样式及 a56086b 部署记录正常快进推送至
`upstream/feature/photobench-evaluation`。仓库为 `ysh12304124/sentrix-home-web-2.0`；
未改 main，服务器发布范围沿上次记录保持。

### 2026-11-17｜100两端轨迹界面已发布并重启

本机先提交7b81cdc，后同步11源文件及3构建文件；沿100原前端快照构建，保留远端覆盖层。
8872 PID3734160→1207798；8874 PID3715338→1207818。187/176份配置、后端和核心结果SHA、
环境哈希及20个其他监听进程保持，历史run数量6/4保持；两端空闲、systemd无异常重启。
组合版778测试、构建与两端真实历史题浏览器验证通过，节点、各步耗时和新页签样式生效。
正式功能仅轨迹前端；产品计时/共享存储未发布。审计见20261116-trace-controls-deploy/README.md。


### 2026-11-16｜轨迹页签样式与100界面发布准备

轨迹视图和详情页签改为圆角分段按钮，选中态、悬停和键盘焦点统一；尊重减少动画偏好。
截图中的历史/脚本切换仅用于验收，预览复用正式样式，不将测试数据入口加入产品。
本机完整构建与 lint 通过（保留既有警告），真实浏览器 13 项核验通过。
100 的 8872/8874 当前均空闲，源码树一致，整体仍 a7bb484 加既有界面覆盖层。
在该源码快照合入 11 个正式轨迹文件，本机 778 项测试和构建通过。
发布计划仅合入已提交的轨迹界面源码和本机构建产物，保留远端基线其余前后端、配置和历史。
产品 run_turn.py、跨相册存储及主线其他功能不在此次部署范围。
审计：`/Users/jzousz/Documents/ChatGPT/产品项目/audit/20261116-trace-controls-deploy/`。

### 2026-11-16｜轨迹节点实现与每步计时

QA、错误分析与对比页共用分源详情。按最新 main 调用字段展示，保留重复输入、每次
失败重试与原始事件；模型/工具自身时长、TTFT 和已有拆分保持。final_answer 交付动作
补实测时长，坏参数原串和失败原因落事件；旧记录不补零。798 前端与 110 后端专项、
构建、ruff/mypy、浏览器 12 项核验通过。未访问服务器、触发真实模型或改历史数据。

### 2026-11-16｜授权集成主线并实现磁盘共享

最新主线经 Git 拉取至本机，已完成合并冲突和数据库升级验证。跨相册硬链接共享、
上传计费及最后引用回收实现完成。最终新增 17 项 / 相关 104 项通过，磁盘复用实测通过，
既有失败已与合并基线逐项对照；未新增模型推理调用、远端操作或真实历史数据迁移。


### 2026-11-19｜有限预算发布与续评授权

用户明确要求停止长尾并重启使修改生效后续评，取代11/18方案的现役预算冻结约束。23:50后核Q4 3ece8e/92题、Qwen211c0d/246题均worker运行，各4turn；已单次取消两精确run并取消对应旧wake/watch。现两测评active-work为空、产品目标scope无活动turn，导入后处理空闲；10个11/10旧pending均已cancel_requested，不改历史。两产品受影响源码SHA与本机HEAD基线相同。

本机正验证reasoning-bounded-v1：真实模型发送含底层重试与工具VLM共用100，工具600中搜索100、其他500，收尾额度在上限内；失败/回放/无效工具均消耗准入，推理分支移除连续失败工具；同turn事件账本恢复计数。普通模式行为保持。需本地验证commit→同步100验收→push。仅1008291/8294及8872/8874，保持8772/8191与模型进程；新resume先新wake，保存intent/response及映射；保留原scope/答卷/有效分/失败史、QA4/3660、reasoning/graph及冻结Judge；预算变更分段记录。完整轨迹存储拆分/节点懒加载待后续，不冒充本次已实现。证据在产品项目audit/20261118-reasoning-bounds-release/。

### 2026-11-19｜完整懒加载实现与发布验证

本轮隔离树codex/q4-trace-quarantine-20261119已完成格式3内容引用/分块存储、增量保存与已提交轨迹释放，概览/题页/单题轻读，节点50条分页、内容256KiB内分块，Judge请求按需读取，同版本trace/SFT流式导出及显式旧存档迁移。后端1692项通过；前端832项通过，1项ChatView失败已在e24f8fb基线复现，专项40项和构建通过。真实含18条异常轨迹的101题迁移峰值RSS1.23GB，48读/4写及导出压测进行中。审计在产品项目audit/20261119-full-lazy-results；新发布脚本deploy/photobench/20261119-full-lazy，不执行旧脚本。当前线上仍fcb32bd，尚未声明新版本已上线。待压测验收后按已授权流程停两精确run，空闲后迁移和切8872/8874，先新wake再原scope续评。

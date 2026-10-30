# T006 取消、调用事实与采样优化（2026-10-23）

基线 e8a8c656，开发分支 codex/photobench-cancellation-and-trace。修复在本地完成并通过门禁后，固定SHA统一制品更新四机。发布前重查占用；不打断其他人的任务，不重启模型/侧车/旧栈。真实回归的状态和本地测试分别报告。

## 分层和契约

遵循 module-interfaces.md：domain声明OperationControl、OperationCancelled、CancellableOperations和可选OwnedTelemetryWork；application负责安全边界、取消广播、有限提交、登记及清理；infrastructure读/proc、HTTP及有界线程探测；bootstrap构造产品进程内登记器。API只映射协议和HTTP状态。导入仍同步POST，不建立持久队列。TelemetryProvider的原四方法签名不变。

产品新增POST /api/v1/imports/{operation_id}/cancel和/assistant/turns/{turn_id}/cancel：202响应operation_id（外部原ID）、cancel_requested、cleanup_status（pending/stopped），未知/重启已丢归属为404。重复取消幂等；202仅表示收到信号；stopped必须原worker退出确认。登记有active/history上限128，重复409、容量429。取消导入返回409、进度cancelled，已落库部分保留，禁止自动重放导入。该接口是通用产品能力，不输出测评规则。

PhotoBench保留POST /cancel-active的cancelled/skipped/total兼容字段，新增可选auxiliary/errors/cleanup_status；它停止全部测评、重判和内存画像，不停止HTTP服务或共享模型。新增GET /active-work只读登记、POST /active-work/cleanup复查已请求取消的清理。未知或不支持的远端清理保留stop_pending并阻止新任务，不能以线程daemon或Future.cancel返回当作已退出。写盘失败不会阻止广播其他对象，诊断仍可读。前端轻量查询该工作登记，单独重判/画像也可停止；无活跃run时取消入口仍可调用，重复点击有锁。

## 确认修复

- QA按实际并发窗口补交，慢Judge对提交施加有界背压；取消先广播事件，不再新增后续会话、turn和Judge。RunCancelledError不再吞成failed。未开始QA保留qa_id和scheduled事实，可resume；不补答案或分数。Judge取消不记作评分失败。
- 重判、内存画像登记所有本地worker；取消时旧评分历史不删除，未完成重判单列。产品规划重试、工具前、模型前和批末步骤检查同一取消信号；运行中的内核/HTTP调用协作退出，不强杀线程。
- Agent已存call_type、step_id、status、finish_reason、工具请求可确定性投影call_observation，注明saved_call_facts_v1；原始历史文件不改写。新run直接记录通用调用事实。内部L2模型调用也走真实计时记录，不与云端Judge混淆。没有TTFT/流式事实时保持未采集，模型请求墙钟不能标作纯生成耗时。2.0 agent墙钟不再重复扣Judge。
- 合并runtime_turns与题面conversation以显示真实状态、回答和轮次；scope来自run执行事实。轨迹UI按事件种类显示紧凑卡片，展开后才渲染JSON；状态徽标class不作用于整行。移除结果页人工审核填写和保存入口，历史review与兼容API保留；QA/GT审核不变。
- Jetson每轮只做一次全拓扑归属和相关PID的smaps采集，PID+start_ticks/socket仍验证，监听缓存不缓存内存。smaps最多4个在飞任务、无等待队列，核心等待预算0.6秒；未读完保持null，未完成任务下轮继续观察其原始时间，不重打时间戳。同一预算内分批补交，轮转防止后排PID饿死。
- 可选llama /metrics最多1个请求在飞，零等待核心采样；读取失败撤销值，保留原观测时刻，超过三个刷新周期失效。系统内存不依赖这些来源。进程探测耗时和pending数量入原始观测；不扩TTL、不平滑补点、不放宽3秒覆盖门槛。长时间内核阻塞仍如实报告pending。

## 验证与发布要求

本地：make ci；取消前/中/后、异常不阻断广播、未知产品归属、队列取消、重判与画像、真实线程阻塞/释放、监听身份/TTL/空值、2.0字段、懒渲染与历史兼容测试。真实数据展示另核验。日常CI不要求真实模型/权重，不删除真实专项断言。

共用执行与采样语义已变，四机后续需要固定新SHA的10QA及短取消专项。分别报告测评程序/指标、任务完成与模型质量、评分覆盖；受控失败可保留，外部Judge缺分不能补0。设备忙时只stage独立release，保留其正在运行版本和历史证据，不能写为已激活。24小时压测、驱动和ORT GPU本轮暂缓。

工作线程启动失败会记录真实失败终态并释放操作登记；导入取消注册竞态最多重试6次，不自动重放POST。历史scope仅依据保存的run.scope_id投影，并标saved_run_scope_v1。

内存画像轮询断连/超时同样请求产品取消；不以turn终态替代worker退出确认，清理pending时中止后续重放并保留失败原因。

## T006 2026-10-24 运行终态汇总规则

沿用八种运行总状态。新版在全部Agent/Judge收尾后，将最终执行/评分失败及应有评分缺失汇总为qa_eval partial、run completed_with_errors；合法0分、重试恢复及未配置Judge不误报。结果ID缺失/重复/不匹配或缺少Agent终态为failed，取消优先。规则在domain集中，application调用；不改接口、指标公式或历史原始记录；QA收尾前清除旧run_valid成功值，失败时保持false，不将该字段用作独立验收结论。

完整边界、failed_count口径及验证要求见[终态汇总规则](../architecture/photobench-metrics-contract.md#t006-2026-10-24-运行终态汇总规则)。本地门禁、同步部署与新版真实回归分别记录，忙机仅暂存。

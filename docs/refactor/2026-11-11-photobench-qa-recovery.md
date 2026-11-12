# PhotoBench QA 未执行与恢复修复

运行：20261111-013136-album3-max-video10-current-b95543。

## 原因

1. 独立 API 的问答接口只创建 pending 回合，build_turn_executor 却因
   experience_mode=false 返回 None。排查时17道题各等待900秒超时；数据库18个
   pending 回合全部 attempt_no=0，其中17个已经请求取消。
2. 恢复执行器后，旧回合的取消检查访问重启前的内存 OperationControl，产生 KeyError；
   持久取消标志本来已经在 SQLite 中，需要读取它而不是依赖旧内存登记。
3. 问答恢复后独立暴露 Judge 401：请求未带 API Key。配置目录无 .env.local，相关环境
   变量未设置。已经请求用户通过 PhotoBench 配置界面补充密钥，不在日志打印密钥。
   运行冻结的 Judge 配置不自动热更新，历史成功回答可在配置完成后重评分。

## 修改与实际运行

- 独立 API 同样装配 TurnExecutor；不改模型、问题、答案、超时预算和评分规则。
- 持久尝试取消读取 repository.cancel_requested；旧路径保持 OperationControl。
- 集成测试经过真实 HTTP 创建会话/回合、SQLite、真实执行器和确定性模型假件，
  验证取消回合不执行、清空进程内取消登记后仍能恢复执行。
- 仅重启产品 API，PhotoBench 和模型不重启。第二次激活等待回合执行空隙，
  更新本机托管 PID 记录并保留旧记录于 logs/qa-worker-recovery-20261111。
- 运行中途修复有边界：切换 API 期间部分请求收到502，原 run 如实保留超时和失败。
  未把失败涂成成功，也未伪造评分；整轮不能视作无故障的质量基线。
- 已观察到回合从pending变为running并生成complete/partial答案，仍需继续监控整轮。

## 验证

针对装配/恢复的test_container共7项通过。扩大运行test_run_turn发现8个旧测试失败，
用HEAD原始run_turn模块重跑复现同样8个失败（主要为旧事件形状断言），没有新增失败。
assistant repositories另有2个、容器端口声明另有1个既有失败，未扩大修复范围。
具体日志在 /private/tmp/sentrix-qa-*.log；不将本轮针对性通过称为全量CI通过。

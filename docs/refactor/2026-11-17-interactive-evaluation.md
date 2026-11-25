# 单题重测与聊天对话

2026-11-17，在 PhotoBench 顶部增加聊天对话页签，并在每道结果题目上提供重测与模型比较。

单题重测复用原相册和冻结题目，多轮题完整重跑；创建独立 run 和评分，原任务总分保留。默认当前实际部署模型，支持 Manager 模型、配置目标和自定义专用端点。比较区显示模型、分数、交付召回与耗时，详情可查看实际版本和完整调用轨迹。源评分批次配置保留，规则版本同时记录来源与本次值。

聊天支持自由提问、连续追问和搜索题库选题；更换模型或相册创建新会话。结果区分别展示召回候选、最终交付媒体和模型/工具轨迹；题库来源显示标准答案、检索 GT 和答案证据 GT。GT 不进入 Agent 请求。会话保存在 results 同级的独立 chat 目录，刷新和服务重启可恢复。取消会等待产品回合终态再释放占用。

聊天在每次主动提问时读取产品实际构建版本，并按该版本准备本次请求。会话创建快照保持，
新回合分别保存 `runtime_snapshot` 与测评 `build_revision`；旧回合沿用其已存记录。
重启恢复只对账已发送回合；正式测评、续跑及其余冻结配置继续严格校验。
版本不符错误同时显示期望版本和实际版本。跨版本聊天回归见 `test_chat_revision.py`。

验证入口：`apps/photobench/tests/test_interactive.py`、`apps/photobench/web/src/features/interactive/Interactive.test.tsx`。HTTP 契约见 `packages/contracts/openapi/photobench.yaml`；新端口见 module-interfaces，模块依赖见 photobench-module-map。

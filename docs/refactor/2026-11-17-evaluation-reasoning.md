# 测评推理模式

## 需求与行为

2026-11-17 用户要求测评接口提供推理模式开关，默认开启，并放开测评限制。
整套 full/reuse 测评、单题重测及聊天创建接口接受布尔字段 `reasoning`，默认 `true`。
测评启动栏右侧提供「开启推理模式」开关，与「完成后删除相册」并排；重测弹窗和聊天控制区也提供对应开关。取消后恢复普通模式的系统执行限制，运行期间锁定。

开启后使用完整工具集，模型步骤、工具往返、搜索、翻页和精读次数均不设上限。
执行墙钟至少 3600 秒，当前模型配置更长时保留更长值；测评默认等待 3660 秒，关闭时为 900 秒。
整套测评允许用 `turn_timeout` 显式覆盖等待时间；交互功能沿用部署默认且至少达到对应模式默认值。
单次召回宽度、交付上限、模型上下文和输出配置保持当前设置，取消及重复失败终止机制继续生效。

该字段控制现有 Agent 的工具和执行预算；模型原生 thinking 参数继续按模型适配器配置执行。
普通产品接口仍默认 `false`。推理模式局部覆盖当前模型执行额度，保留模型角色、历史、评审和压缩配置。

## 持久化与恢复

- 产品请求将模式存入回合 `trace_json`，持久执行器从记录构造对应的 RunTurn；不新增数据库迁移。
- 完成轨迹保存实际模式、profile 和各项执行预算。相同幂等键更改模式返回 409。
- 测评 run、执行快照和逐轮记录保存模式。续跑及内存画像重放继承原任务；历史缺字段按原普通模式执行。
- 新重测默认开启，独立于来源旧 run 的模式。
- 聊天会话保存模式，后续发送可显式切换；省略或 `null` 沿用会话，历史会话缺字段沿用普通模式。
- 已保存的逐轮模式保留，不因后续切换回写历史。

## 契约与发布

`POST /api/v1/runs`、`POST /api/v1/runs/{run_id}/retest` 及
`POST /api/v1/chat/sessions` 接受 `{"reasoning": true}`；
`POST /api/v1/chat/sessions/{session_id}/turns` 也接受该字段控制下一轮。
两份 OpenAPI 及测评前端类型同步更新。

产品和测评后端须配套发布，确保后台执行器落实所选模式。此次先在本地修改、验证并提交，后续同步服务器验收后再 push。

## 验证入口

- `apps/api/tests/integration/test_turn_dispatch.py`：HTTP 请求、排队落库、租约恢复、幂等冲突、实际预算与云端配置保留。
- `apps/api/tests/unit/test_assistant_policy.py`：超过普通模式计数后仍可继续取证。
- `apps/photobench/tests/test_suites.py`、`test_run_execution.py`：默认值、关闭、预算冻结、续跑及产品请求传递。
- `apps/photobench/tests/test_interactive.py`、`test_interactive_http.py`、`test_sentrix_client.py`：重测、聊天切换/继承、严格布尔和 HTTP 载荷。
- 测评前端 ConfigSection 与 Interactive 测试：开关默认开启、切换与运行锁定。

测试结果及完整 CI 基线见工作区 `audit/20261117-evaluation-reasoning/README.md`。

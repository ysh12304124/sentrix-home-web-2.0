# 体验服务交付说明

## 1. 基线与分支

- 源码基线：`d3485537b02e30e0ee3e7cc6dc4b450c2f38f7db`（GitHub `main` 与 153 工作树一致）。
- 开发分支：`codex/experience-service`（从基线新建）。
- 交付 SHA：以 `git rev-parse HEAD` 为准；控制面启动日志/诊断里的 `build_sha` 由发行脚本注入。
- 逐任务提交（从旧到新）：基线盘点 → 控制服务骨架 → 登录/转发 → 模型准入 →
  持久上传 → 导入执行器 → 问答恢复 → 回忆接口 → 六入口界面 → 反馈 → 监测 →
  部署材料。

## 2. 数据库迁移

用户库（`apps/api/src/sentrix/infrastructure/persistence/migrations`）：

| 版本 | 内容 |
| --- | --- |
| 0015 | `import_jobs`、`import_job_stages`、`upload_reservations` |
| 0016 | `scope_postprocess` 代际 |
| 0017 | 回合幂等键/租约/围栏、助手消息唯一索引、`agent_turn_events_v2` |
| 0018 | 回忆序列、游标与已看记录 |
| 0019 | 导入取消请求 |
| 0020 | 结果集按回合尝试归属 |
| 0021 | 相册后处理失败退避（`next_attempt_at`） |

控制库（`apps/api/src/sentrix/control/migrations`）：

| 版本 | 内容 |
| --- | --- |
| 0001 | 账号、会话、反馈、附件、审计、事件、采样 |
| 0002 | 登录限速 |
| 0003 | 反馈补充信息与内容签名 |
| 0004 | 重启操作幂等记录 |

全部为扩展式；旧表不删除。回退前提：上一版能读新 schema，且不把新作业写坏。

## 3. 路由访问审计（按组）

体验网关对普通账号放行 `/api/v1/*`，但下列组在控制面与用户 API 两处都拒绝：

| 路由组 | 普通用户 | 管理员 | 内部服务 |
| --- | --- | --- | --- |
| `/media/uploads`、`/import-jobs*` | 允许 | 不路由 | 允许 |
| `/assets*`（file/preview/poster/详情） | 允许（仅本人数据根） | 不路由 | 允许 |
| `/timeline*`、`/events*`、`/videos/{id}` | 允许 | 不路由 | 允许 |
| `/memories/rotation*` | 允许 | 不路由 | — |
| `/assistant/*`（回合、SSE、结果集照片） | 允许 | 不路由 | 允许 |
| `/people*`、`/face-clusters*`、`/family-graph`（读） | 允许 | 不路由 | 允许 |
| `/diagnostics`、`/memory-graph/*`、`/search`、`/knowledge`、`/entity-groups`、`/entities` | 403 | 403（网关不路由） | 允许 |
| `/imports*`、`/pipeline/process`、`/maintenance/*`、`/facts/*`、`/family-graph/runs`、`/family-graph/scopes/merge`、`/videos/*/reprocess` | 403 | 403 | 允许 |
| `/internal/status` | — | — | 仅体验模式、需内部令牌 |
| `/control/v1/auth/*` | 匿名登录/本人会话 | 同左 | — |
| `/control/v1/feedback*` | 仅本人 | 仅本人 | — |
| `/control/v1/admin/*` | 403 | 允许 | — |
| `/internal/model/v1/*` | — | — | 需模型令牌，按令牌固定工作类型 |

逐条 77 条审计表见 [`experience-route-audit.md`](experience-route-audit.md)。

完整枚举命令（用户执行）：

```bash
cd /opt/sentrix/current && .venv/bin/python - <<'PY'
from sentrix.main import create_app
for route in create_app().routes:
    methods = ",".join(sorted(getattr(route, "methods", []) or []))
    print(methods, route.path)
PY
```

## 4. 状态机与幂等语义

导入作业：`accepted → queued → running → completed|partial|cancelled`，
暂时可进入 `waiting_dependency|retry_scheduled`，无法自动修复进入 `needs_attention`。
`completed` 只在全部必需阶段完成时设置；不适用的阶段记 `skipped` 与原因。

问答回合：业务状态仍是 `pending/running/complete/partial/failed/blocked/cancelled`；
`execution_state` 表达 `recovering/waiting_dependency/retry_scheduled`。
认领写 `attempt_no` 与 `fence`，终态与最终消息同事务、条件写；SSE `event_seq` 回合内单调。

模型准入：问答 > 导入 > 评测；槽位取实际 `--parallel`；同级用户轮转；
导入等待满 120 秒后每放行 4 个问答放行 1 个最老导入；流结束/取消/异常都释放槽位。

## 5. 静态检查记录（实施代理执行）

在 153 的 `sentrix-home-web2.0-codex` 工作树执行（2026-11-03，后端 SHA `ea83c4c`，前端构建 `034708c`）：

- `git diff --check`：通过。
- `.venv/bin/python -m compileall -q apps/api/src/sentrix`：通过。
- `PYTHONPATH=apps/api/src .venv/bin/python -c "import sentrix.control.admin, ..."`：通过。
- `cd apps/web && npx tsc -b --force`：通过。
- `cd apps/web && VITE_BUILD_SHA=034708c npx vite build`：通过。
- 批量导入回归：344 个文件全部接收并完成，期间修复了带时区/无时区事件时间比较的 `TypeError`，
  并补上进程重启后清理 `processing_generation` 的后处理恢复；记忆图的时间边比较也统一按 UTC。
- `.venv/bin/lint-imports`：11 条契约全部 KEPT。
- 针对性浏览器验证：8220 管理台加载构建 SHA、按用户运行、趋势、反馈筛选与重启按钮，
  并确认旁路横幅已移除、模型槽位显示 4；8210 问答点击“停止回答”后服务端回合为 `cancelled`；
  回忆页文字位于图片下方且底部只有一个开始/暂停按钮；导入页显示 n/m 总进度与文件/文件夹选择区。

未执行：完整 A1–A9 验收、故障注入、容量压测、TLS/专有身份/备份恢复。上述仍需用户按
`experience-fault-drills.md` 与验收矩阵执行。

## 6. 已知缺口（开放用户前仍需处理）

已补齐：家族图批末推断、结果集按尝试清理、运行中导入的取消检查点、管理员作业重排、
管理员反馈收件箱 UI、阶梯 attempts 计数、逐条路由审计（见 `experience-route-audit.md`，共 77 条）、
独立管理入口（8220，独立 cookie）、固定端口 systemd 自恢复（见 `experience-ports.md`）、
管理台按用户运行视图与在飞阶段、60 分钟趋势、排队告警、构建 SHA、反馈截图与多维筛选、
固定单元重启（幂等键）与共享模型影响提示、导入后处理 `partial` 状态。

仍缺：

1. **取消仍不中断已在执行的模型调用**：运行中的导入会在下一次安全检查点停止
   （准备完成、提交前），回合取消依赖协作式检查，不做强制中断。
2. **PhotoBench 演示未启用准入**：代码支持同机共享模型经控制入口并按评测级排队，
   部署时需设置 `deploy/experience/photobench.env.example` 的三个变量并跑并发验收。
3. **TLS 与生产 systemd 未执行**：演示是 HTTP、Secure cookie 关闭、以 asus 身份运行；
   正式部署必须使用专用身份、受信任证书与 systemd 单元。
4. **依赖托管仍不唯一**：BGE 8101 是 1.0 `sentrix-api.service` 的子进程，没有独立单元，
   子进程退出不会由 systemd 单独拉起；8100 与 8113 已有 demo 单元，但未换成生产模板。
5. **容量、故障注入、备份恢复未执行**：1→3→5 用户、断 SSE、杀进程、停依赖、
   单用户恢复与版本回滚仍需用户按 `experience-fault-drills.md` 执行。
6. **8100 旁路未接入**：8194、8291、8091、8772 等消费者仍直连共享模型。按产品要求，
   管理台不再显示旁路横幅；这些请求仍不受体验入口的槽位与优先级约束，
   因此优先级保障不能算全局生效。

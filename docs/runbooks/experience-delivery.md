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

控制库（`apps/api/src/sentrix/control/migrations`）：

| 版本 | 内容 |
| --- | --- |
| 0001 | 账号、会话、反馈、附件、审计、事件、采样 |
| 0002 | 登录限速 |
| 0003 | 反馈补充信息与内容签名 |

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

在 153 的 `sentrix-home-web2.0-codex` 工作树执行：

- `git diff --check`：通过。
- `.venv/bin/python -m compileall -q apps/api/src/sentrix`：通过。
- `PYTHONPATH=apps/api/src .venv/bin/python -c "import sentrix.main, ..."`：通过。
- `cd apps/web && npx tsc -b --force`：通过。
- `cd apps/web && npx vite build`：通过。
- `.venv/bin/lint-imports`：11 条契约全部 KEPT。

未执行：功能测试、真实模型调用、故障注入、容量压测。上述每一项仍需用户按
`experience-fault-drills.md` 与验收矩阵执行。

## 6. 已知缺口（必须在开放用户前处理）

1. **相册后处理的家族图未接入执行器**：参与者、记忆图、事件总结与人物画像已接入；
   `family_graph` 仍只在原同步导入路径触发。
2. **结果集按尝试隔离未完成**：回合终态与事件已按 `fence` 隔离；`agent_result_sets`
   仍按每次检索新建，失败尝试的临时结果集不会被主动清理。
3. **取消是协作式的**：持久取消标志会让恢复扫描跳过；正在执行的模型调用只在安全
   检查点停止，不做强制中断。
4. **管理员“重新排队作业”端点未实现**：当前只有监测、事件与固定服务重启。
5. **管理员反馈收件箱只有 API**：前端管理页目前展示运行概览，未做反馈列表/状态操作 UI。
6. **PhotoBench 接入依赖部署环境变量**：代码支持同机共享模型经控制入口并按评测级
   排队，但必须按 `control.env.example` 与部署手册设置三个 `SENTRIX_BENCHMARK_*` 变量。
7. **CLIP 侧车单元已提供但未启动**：部署时必须先 `systemctl enable --now sentrix-legacy-clip.service`。
8. **第 3 节是路由组审计**，不是逐条 69 路由枚举；开放前用第 3 节的命令生成逐条清单
   并补齐媒体下载、结果集照片、目录导入、维护、评测五类的逐条标注。

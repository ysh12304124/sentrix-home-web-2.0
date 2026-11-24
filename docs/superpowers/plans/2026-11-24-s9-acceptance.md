# S9 验收报告与 Agent 交接（代码/契约层）

日期：2026-11-24
分支：`feat/image-memory-v3`
基线：`8c209ebc`；迁移：`0037`–`0042`

## 1. 本轮已交付的工程证据

| 证物 | 路径 |
|---|---|
| 基线与环境 | `docs/refactor/image-memory-v3-baseline.md`、`docs/refactor/image_profile_matrix.json` |
| 迁移 | `0037_memory_build_foundation.sql` … `0042_memory_export_snapshots.sql` |
| 切片交付记录 | `docs/superpowers/plans/2026-11-24-s{0,1b,2,3a,4,5,6,7,8}-delivery.md` |
| OpenAPI | `packages/contracts/openapi/sentrix-v1.yaml`（`export_openapi.py --check` 通过） |
| 模块/能力地图 | `docs/architecture/module-map.md`、capability map（`check-docs` 通过） |
| 测试 | 各切片 unit/integration/contract 用例；本报告只列已执行项，未执行项另列 |
| Agent 交接 | `docs/refactor/image-memory-v3-agent-handoff.md` + `docs/refactor/agent-handoff/*.json` |

工程门禁已运行：`ruff check`（API/PhotoBench/scripts）、改动文件 `mypy`、
`make arch`、`make check-docs`、OpenAPI/PhotoBench OpenAPI 一致性。均通过。
全仓 mypy 仍有基线既有错误（例如 `merge_people` 的历史类型收窄、`container.py`
既有未注解函数），未混入本轮变更范围。

## 2. 22 项缺口覆盖矩阵

状态说明：**代码/契约证据**＝本轮已实现并有目标测试或门禁；**用户验收待执行**＝需要
真实相册/模型/前端按 §15 执行；**未启用**＝按计划保持关闭。

| # | 原缺口 | 本轮落点 | 状态与证据 |
|---|---|---|---|
| 1 | 准确率只有度量 | 质量隔离、可信档、单次纠错；`auto_trusted` 未校准 | 代码/契约证据；真实误绑/覆盖/基础误配用户验收待执行；**姓名自动放行未启用** |
| 2 | 历史描述无改善路径 | H0 盘点、H1 确定性回填、H2 按范围重建任务 | 代码/契约证据（`memory-builds` 端点、任务表/重试/取消）；真实前后对照待执行 |
| 3 | 展示混时地 | 统一事件文档时地投影；场景/真实地点分开；date_filter_mode | 代码/契约证据（`test_event_documents`、timeline 用例）；前端页面验收待执行 |
| 4 | 来源未进入问答 | 字段证据、事件/出现来源、`/time-place` | 代码/契约证据；问答侧按计划**排除本轮** |
| 5 | 描述无检索落点 | 出现 FTS、描述定位、`/search?description` | 代码/契约证据（跨 scope、local_only、匹配复核）；Recall@10 金标待执行 |
| 6 | 外部映射/tags | 三文件导出包、manifest/provenance、PhotoBench 适配器 | 代码/契约证据（导出/适配器用例）；真实目标消费器待执行 |
| 7 | 单图 entities | observations entities/tags + 导出 name/type + 来源 | 代码/契约证据 |
| 8 | OCR 区域缺值 | 核心不输出；provenance `not_provided/regions=null` | 契约用例 |
| 9 | 端点/尺寸矩阵 | profile matrix；云端主模型配置；1280 输入 | 配置与代码证据；真实调用/延迟待用户验收 |
| 10 | 描述修正撤回 | `PATCH /face-instances/{id}/description`，CAS、审计、撤回不回落 | 代码/契约证据（appearance/evidence 两路） |
| 11 | pending 重复 | 低质量不建簇、相似簇人工建议/拒绝抑制 | 代码/契约证据；真实人数统计待执行 |
| 12 | 外观/瞬间权威 | appearance 优先、face evidence 暂存、统一出现选择 | 代码/契约证据；多来源冲突真实 fixture 待执行 |
| 13 | 人物关系 | pending 两端可维护；合并/拆分清理路径 | 关系契约用例；跨 scope 用户验收待执行 |
| 14 | 跨 scope | 出现/事件/导出/搜索 scope 校验；全相册排除评测空间 | 代码/契约证据 |
| 15 | 前端空缺 | 后端契约与 OpenAPI 已交付 | **前端页面逐项验收未执行**（本轮未改前端） |
| 16 | token/延迟/存储 | 现有成本观测与 profile matrix | **真实 p95/成本报告待用户执行** |
| 17 | 隐私/保留 | 删除清理覆盖出现 FTS/事件文档/向量；provenance 不含原始正文 | 代码/删除回归；备份恢复期限验收待执行 |
| 18 | 样本与功效 | 固定对照/金标设计要求在 v3 §15 | **800 图/2000 出现金标未采集**，不宣称统计结论 |
| 19 | legacy 读取 | legacy/partial/complete、事件文档缺失回退旧时间线 | 代码/契约证据；新旧混读真实相册待执行 |
| 20 | 表依赖 | M1–M5 迁移、active 文档/证据 scope 校验 | 迁移执行与 FK 检查由用户在数据库副本验证 |
| 21 | 视频关键帧 | 原生图片来源判定；关键帧排除导出、保留旧路径 | 导出用例；关键帧重放真实 token 待执行 |
| 22 | partial/failed | 事件文档缺索引仍可读；H2 任务重试/取消/显式执行 | 代码/契约证据；故障注入与页面状态待执行 |

## 3. 未执行/不能宣称

- 未执行完整 `make test` / `test-contract` / `test-integration` / `web-check`；
  只执行了各切片相关用例与门禁。
- 未执行真实云端模型金标、真实相册导入、前端页面操作和 PhotoBench 真实评测。
- `auto_trusted` 未开启；没有达到 v3 §15.2 的独立测试门槛。
- H2 执行由显式 `/memory-builds/run` 领取，不新增常驻后台调度线程；任务 lease/重试/
  取消语义已具备。
- 删除后剩余事件当前撤回旧 active 文档，确定性重算由 H1/H2 触发。
- 本轮不宣称旧照片已全部重新理解、所有人脸误配已解决、摘要语义已自动核实、
  Agent 自然语言回答已变准确。

## 4. 启用建议

- 默认仅开启读取/字段/检索/导出；`auto_trusted` 与任何“系统判断同人”保持关闭。
- 用户按 v3 §15.3 从工作树根执行工程门禁与真实验收；未通过的 profile 不启用。
- Agent 下一里程碑以 `docs/refactor/image-memory-v3-agent-handoff.md` 的 schema 与
  样例为输入，重新验收问答后再宣称问答改善。

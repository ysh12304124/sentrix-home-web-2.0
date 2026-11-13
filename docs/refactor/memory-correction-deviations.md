# 用户记忆修正与派生同步

新增主前端用户修正能力，不改变 PhotoBench 或回答提示词。
`MemoryCorrectionRepository` 由 bootstrap 装配，SQLite 实现负责版本检查、修改前后记录和待同步状态。
`PATCH /api/v1/memories/{observation|event|entity}/{id}` 接收 revision、changes、reason；
冲突返回统一 409 错误。人物改名继续走原人物接口，保留别名及身份审计。
实体属性与身份/家庭关系既有写入口保留响应，写入前登记持久任务，成功后按 scope 同步；
该兼容路径不是把原来多个仓储事务强行变成一个事务，进程中断后任务仍可重试并读取最新值。

`GET /assets/{id}/memories` 包含源视频及派生关键帧观察。
`GET /memories/{kind}/{id}` 提供修正表单的权威当前版本。
`GET /memories/corrections?scope_id=...` 返回审计和状态；
`POST /memories/corrections/retry?scope_id=...` 重试该范围，不传 scope 时重试最多 100 个待同步范围。
同步在显式保存或重试时执行，不在普通读取或问答时执行。

图构建读取用户确认的家庭关系、当前实体属性与确认身份提及，撤回的事实不再形成可遍历事实边。
身份边以 entity_id 关联，观察姓名快照按上游确认身份刷新；确认身份投影提供媒体直达人物的边，
使“媒体→人物→家庭关系→人物→媒体”落在既有四跳预算内，保持遍历上限不变。
用户覆盖/撤回的人物对不会由旧通用关系恢复；关系派生 semantic_claims 同步撤回或更新，
保留历史行并使用稳定目标实体 ID。确认身份参考边只从节点保存的上游 ID 字段重建。
画像与属性保存于节点属性，
不由这些字段推断额外因果边。图仍是 scope 原子替换的派生投影；不更改图检索开关。
词法/文本向量仅刷新受修正影响的观察和事件，视觉向量不因文字编辑重算。
各同步步骤独立执行，任一步失败记录 warning 和 pending，不把已提交的用户修改回滚。
新 schema 迁移 0022；回滚代码时可保留新增审计表与 entities.revision，不删除用户修正历史。

验证结果见 [记忆修正验证记录](memory-correction-validation.md)。本次专项测试运行通过后，
按用户要求移除新增的后端和前端专项文件；原删除、身份、关系与图集成测试保留。

## 独立验收场景

| 功能 | 验证内容 | 自动用例 |
|---|---|---|
| 图片观察修正 | 权威描述/版本、图节点、词法和文本向量均更新，记录前后内容 | test_observation_correction_updates_graph_lexical_and_vector_and_audit |
| 并发与非法字段 | 同一版本同时保存只允许一个成功；不允许修改 scope | test_concurrent_edits_allow_exactly_one_revision / test_stale_revision_and_unknown_fields_do_not_write |
| 文本模型故障 | 保存不丢失、pending 可见，模型恢复后重试成功 | test_failed_embedding_keeps_authoritative_edit_and_retry_recovers |
| 图故障隔离 | 图失败不会跳过词法和文本向量更新 | test_graph_failure_does_not_skip_index_update |
| 家庭关系修正/撤回 | 图边与派生关系声明一起更新/撤回 | test_family_relationship_edit_and_retract_change_graph |
| 真实图遍历 | 确认关系后四跳内找到另一人的媒体，撤回后路径消失 | test_confirmed_family_path_is_retrievable_and_disappears_after_retraction |
| 人物改名 | 观察姓名快照、人物图节点、新姓名检索更新，旧词法投影消失 | test_renaming_person_refreshes_confirmed_observation_and_graph |
| 实体属性 | 属性落库、审计记录及图节点属性同步 | test_entity_property_is_recorded_and_projected |
| 事件修正 | 摘要/图节点/事件向量更新；结束时间早于开始时间时原子拒绝 | test_event_correction_updates_summary_vector_and_rejects_reversed_time |
| 视频关键帧修正 | 视频入口可读源观察和关键帧观察；修改帧记忆保留源视频 | test_video_memory_list_includes_derived_keyframe_and_edit_keeps_video |
| 前端 | 打开时读取版本、只发送改过字段、409 不冒称成功、视频入口读取帧记忆 | apps/web/src/shared/components/MemoryCorrection.test.tsx |
| 视频删除（原有） | 级联帧/独占记忆，保留共享事实，外键检查、文件/向量清理与重试 | apps/api/tests/integration/test_media_deletion.py |

表中专项用例名称用于保留已执行的场景记录；专项文件已按用户要求移除。
所有数据库测试使用临时 SQLite 和合成记录；未删除本机真实媒体或影响正在运行的评测。

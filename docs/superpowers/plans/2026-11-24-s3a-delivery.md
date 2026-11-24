# S3a 交付记录（人物出现描述检索与观察语义组合）

日期：2026-11-24
分支：`feat/image-memory-v3`
范围：出现 FTS、描述/稳定 ID 定位、跨 scope、`/search` 人物 ID/描述适配、观察语义文本组合、pending 关系复核。

## 迁移

- `0040_person_occurrence_search.sql`：
  - `person_occurrence_search_terms`（scope/face/asset/observation/person/field/value/evidence/revision）；
  - `person_occurrence_search_fts`（FTS5 预分词投影，保留 term_id 与来源字段）；
  - `person_merge_suggestion_decisions`（相似簇建议的负向/接受决策，S3b 使用）。
- 原 Master 的 M4/M5 顺延为 `0041_event_documents`、`0042_memory_export`；本切片不改事件表。

## 写入

- `refresh_occurrence_terms`：
  - 有效描述优先 `person_appearance_evidence`，其次 `memory_evidence(face_instance, face.description)`；
  - 索引稳定标签（验证姓名 / `人物…` / `本图…`）、完整 face ID；
  - 验证姓名只在该次出现为 `user_verified`/`auto_trusted` 且实体 confirmed 时进入投影；
  - 在新增实例、确认/驳回、描述绑定的同一事务内刷新；新增 `SqlitePersonOccurrenceIndex.rebuild`
    供 S8 历史回填/维护端点使用（已登记 PENDING_METHODS）。
- `ImportAssets` 绑定描述后刷新所有本次检测项；低质量 `local_only` 的描述同样可查。
- 观察语义文本：`compose_text` 追加 `detail_caption`、`tags` 与同图有效脸级描述（去重），
  仍是一张图一次文本嵌入，不新增 per-face 向量；旧顺序不改，避免影响幂等判定。
  source_text 分块路径不额外追加，避免改变既有文档分块语义。

## 读取

- `GET /api/v1/people/occurrences` 新增 `description_query`、`identity_state`、`offset`；
  返回 `matched_fields`、`evidence_refs`、`match_score`。
- 描述查询先查出现 FTS，再按**当前**描述/标签/姓名复核；投影晚清理或描述被改时旧词不会返回。
- `scope_id` 为空时排除 `include_in_people = 0` 的空间，和按人召回同一口径。
- `GET /api/v1/search`：
  - `q` 不再必填；新增 `person_id`（可重复）与 `description`；
  - 描述是独立召回通道，只有词法落到 face_instance 才输出 `matched_occurrences`；
  - 图片语义分数不分摊成人物识别分；
  - 只有人物条件时按当前归属列举图片；空条件仍返回 422。
- pending 人物可作为关系两端；补契约用例，无需改关系写侧。

## 验证

已执行：
- 新增/更新 unit、integration、contract 用例全部通过（描述定位、local_only、跨 scope、
  身份状态过滤、稳定 ID、投影失效复核、驳回移除、验证姓名、搜索通道、pending 关系）。
- `make arch`、`make check-docs` 通过；改动文件 ruff check/format、mypy 0 错误。
- 全仓 mypy 仍有基线既有错误；未运行完整 test 套件。

## S3b 相似簇建议

- `person_merge_suggestion_decisions`（0040 已建）存负向/接受决策与特征版本；唯一键
  `(scope, left_cluster, right_cluster)`，重复操作是 UPSERT。
- `SqlitePersonMergeSuggestionRepository`：按 scope 取未拒绝簇的代表向量/原型向量、
  头像、成员数；候选上限 500 簇，不做全库两两比较。
- `ReadMergeSuggestions`：复用现有匹配余弦阈值（0.30），比较前各自归一化；同人簇、
  跨模型版本不比较；每簇最多 5 条，默认一页 20 条；`kind=manual_review`，没有自动合并。
- 拒绝后同一特征版本不再提示；簇 revision 或模型版本变化后重新提示。
- `GET /api/v1/people/merge-suggestions`、`POST /api/v1/people/merge-suggestions/reject`；
  静态路径注册在 `/people/{person_id}` 之前。

验证补充：`test_merge_suggestions.py`（同 scope、版本失效、每簇上限）与
`test_merge_suggestion_routes.py`（读取、拒绝、路由不被动态路径截获）通过；
`make arch`、`make check-docs`、openapi 契约一致；改动文件 ruff/mypy 0 错误。

未完成：
- 未校准时只提供人工建议，不发布“系统判断同人”；校准金标仍需用户决定。
- S1b 时地详情仍未做，已登记在第 11 项任务。

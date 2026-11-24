# 图片记忆 v3 · Agent 交接

日期：2026-11-24
状态：后端 schema/端点/样例已交付；Agent 接入与问答重验是下一里程碑。

## 1. 可消费端点

| 用途 | 端点 | 说明 |
|---|---|---|
| 单图时地/来源 | `GET /api/v1/assets/{asset_id}/time-place` | 时间角色/精度/时区状态、真实地点、scene、冲突与证据引用 |
| 人物出现 | `GET /api/v1/people/occurrences?scope_id=&description_query=&identity_state=` | 每张脸稳定 ID、描述、框、可信状态；姓名只在验证时出现 |
| 单次纠错 | `POST /face-instances/{id}/confirm-assignment` / `reject-assignment` | 只改本次出现 |
| 描述修正/撤回 | `PATCH /face-instances/{id}/description` | `expected_revision` CAS；撤回不回落旧模型描述 |
| 图片/人物搜索 | `GET /api/v1/search?q=&description=&person_id=&scope_id=` | 词法落到 face 才返回 matched_occurrences；图片语义分不分摊成人物分 |
| 时间线/事件 | `GET /api/v1/timeline`、`/events/{id}` | active 事件文档：capture range、occurrence_time 状态、participants、content_items、coverage |
| 事件搜索 | `GET /api/v1/events/search?q=&scope_id=` | 事件命中展开全部成员并标出实际命中成员 |
| 相似簇建议 | `GET /api/v1/people/merge-suggestions` + `POST .../reject` | 人工比较，无自动合并 |
| 历史/回填 | `GET /memory-builds`、`POST /backfill`、`POST /rebuild`、`POST /run`、`POST /cancel`、`GET /tasks` | H0 盘点、H1 0 模型回填、H2 按范围阶段任务 |
| 导出 | `POST /memory-exports` + `GET /memory-exports/{id}/files/{name}` | 三文件只读快照 |

OpenAPI 权威：`packages/contracts/openapi/sentrix-v1.yaml`。

## 2. 使用纪律

- 事件正文只读 **active** `event_documents`；`summary_source`/`summary_state` 必看：
  `legacy`/`model_generated_unverified` 不等于逐项有依据。
- 人物姓名只在出现 `identity_state in {user_verified, auto_trusted}` 且实体姓名确认时
  可信；`candidate`/`local_only` 只能使用稳定标签和描述。
- 时间：`time_basis=capture`，`precision`/`timezone_state` 必看；`occurrence_time_state=unknown`
  时不得把 `capture_range` 改写成活动起止。
- 地点：`place.scene` 是画面场景，`physical_locations/display_place` 才是真实地点；
  `place_conflict=true` 时不得输出唯一精确地址。
- 证据引用格式：`observation:*` / `asset:*` / `build:*` / `face:*` / `asset_place:*` /
  `document:*`；引用只证明可定位，不等于语义一定受支持。
- 导出 `location_name` 不为 scene 兜底；OCR 区域 `not_provided` 不是 `[]`。

## 3. 样例

- `docs/refactor/agent-handoff/event_document.sample.json`
- `docs/refactor/agent-handoff/person_occurrence.sample.json`
- `docs/refactor/agent-handoff/source_evidence.sample.json`

样例是契约形状，不含真实家庭数据；`id`/时间/坐标均为构造值。

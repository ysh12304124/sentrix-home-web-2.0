# 人物页去掉「待确认的人物」、新增手动合并；问答找不到已改名人物

## 问答找不到「王王」

用户在全部相册里问「查找王王的照片」，四次都只给出一张无关照片。根因不在问答链：
人物详情的「保存姓名」走 `PATCH /people/{id}/name`，只改 `canonical_name` 与
`name_state`，实体仍是 `pending`。当时库里「王王」的簇有 36 张脸，提及 0、
事件参与者 0、`people_json` 无命中；问答的人物档案只认已确认的人，按人找照片依赖提及
与参与者，所以查不到。`home-default` 的 54 个人物全是 pending，另一个改过名的是「明明」。

修复分两部分：

1. **入口**：名录同时列出待确认与已确认的人，`GET /family-graph` 的 `people[]` 增加
   `status` 字段（只增不删，OpenAPI 与前端类型同步）。详情对 pending 的人给「确认身份」
   （名字 + 可选家庭角色，走 `POST /people/batch-confirm` 单件），对已确认的人保留改名。
   `PATCH /people/{id}/name` 的语义不变。
2. **数据**：经用户同意，对「王王」「明明」调用确认接口。两件都在最后的重分段一步
   返回 `FOREIGN KEY constraint failed`，此前的提及、外观、参与者已经写入。

### 重分段的外键缺陷

`SqliteEventRepository.merge` 删除源事件前只搬了 `event_observations`；参与者与
`event_entities` 由调用方 `rehome` 先搬，但 `person_moments.event_id` 无人搬。人物介绍
跑过之后真实库里有这些行，`DELETE FROM events` 撞外键。以前没人被确认过，重分段从未
真正合并过事件，所以没暴露。修复为删除前把 `person_moments` 改挂到目标事件
（其唯一键不含 event_id，不会冲突）。回归用例
`test_the_source_event_person_moments_follow_the_merge` 在未修复代码上复现同一
`IntegrityError`。

数据侧：先备份 `data/backups/sentrix-before-resegment-20261111.db`，在副本上用修复后的
代码复现并验证，再对真实库只补跑失败的那一步（参与者刷新幂等 + 重分段）：「王王」合并
回 4 对被拆开的事件，「明明」无可合并。记忆图通过一次同名改名（无写入）触发路由里的
`refresh_memory_graph`。

### 尚未解决：小模型把人名截短（暂缓）

数据修好后复测，回答仍只有一张照片。带调试开关重跑确认：规划模型（Mac legacy 的
Qwen3.5-4B Q4）原始输出就是 `"scope": {"person": "王"}`，执行模型随后
`search_memories(query="王")`，实体通道没有拿到人名，召回里没有「王王」的照片
（他名下 32 张）。代码没有截断名字。按硬规则 7/8 不在出口清洗、不硬编码人名。
规划接地的修复（已确认人物名单进规划提示词、规划人名作为检索人物条件）已经做完并验证，
但按项目组 2026-11-11 的决定问答查询暂缓，放在单独的分支 `wip/assistant-person-grounding`，
不进本分支。

## 去掉「待确认的人物」tab

名录本来就列出 pending 人物，那个 tab 与之重复。它独有的能力处理如下：

| 能力 | 去向 |
|---|---|
| 确认身份（含家庭角色） | 人物详情「确认身份」 |
| 拒绝（不是人物） | 人物详情「这不是一个人物」（二次确认，取它的有效簇调现有 reject） |
| 簇级合并 | 名录「合并人物」/ 详情「与其他人合并…」（人物级，见下） |
| 批量确认、单张脸拆分 | 不迁移（用户确认）；后端接口保留 |
| 已确认人物列表 + 改名 | 与名录/详情重复，删除 |

删除 `PeopleView` / `FaceClusterList` / `FaceClusterCard` / `BatchConfirmPanel` /
`PersonList` 及其测试、样式；客户端里只剩测试在引用的 `listFaceClusters` /
`confirmFaceCluster` / `mergeFaceClusters` / `splitFaceCluster` 一并删除。后端
`/face-clusters/*` 全部保留（PhotoBench 与外部契约在用）。原 tab 上的
`people.confirm_submit` / `people.confirm_result` 埋点挪到详情的确认动作，口径不变；
没有新增事件名（控制面校验事件名白名单）。

## 人物级手动合并

现有 `POST /face-clusters/merge` 是簇级的：只挪人脸实例，拒绝合并两个已确认的人，
来源实体的提及、关系、归属、画像都不处理。名录里同一个人因年龄、光线被拆开的典型情况
恰恰是两个都已命名的人，所以新增 `POST /people/merge`：

- **规则**（`domain/identity/person_merge.py`，纯函数）：保留用户指定的目标；名字取
  显式给的 → 目标的 → 第一个有名字的来源；任一已确认则合并后已确认；所有旧名与旧别名
  并成别名。合并后的名字撞上另一个已确认的人 → 409，让用户把他一起选进来。
- **写**（`SqlitePersonMergeRepository`，一个事务）：人脸簇与实例并进目标主簇；
  提及、观察关联、事件参与者/实体、外观证据、人物瞬间改挂（冲突即重复，删除）；
  两套关系里两人之间的边作废，同一第三人的边按「锁定优先、目标优先」只留一组；
  家庭归属/画像同理；来源人物画像修订与来源实体置 `superseded`，双边写审计
  （`merged_into` / `absorbed_person`）；重写 `observations.people_json`。
- **重算**（事务外，幂等）：别名、目标簇原型、新确认时补提及、外观取样、事件参与者与
  重分段，路由最后刷新记忆图。
- **幂等**：已并进同一目标的来源跳过，重放返回同一个人；并进了别人的再合并 → 409。
- **不做**：不建冻结的 `entity_merge_candidates`、不自动推荐、第一版不支持撤销。
  `semantic_profiles` 与来源自己的 `semantic_claims` 不搬（对来源这个人的派生总结，
  由目标下一次画像/洞察运行重新得出）；以名字为键的 `facts` 不改写。

前端两种入口共用一个确认框（只问「保留谁」，按后端同一规则预览名字与别名）：
名录工具栏「合并人物」→ 勾选 ≥2 张卡片（跨相册时禁用）→「合并所选」；
详情「与其他人合并…」→ 搜索同空间人物 → 同一确认框。成功后重拉并打开保留的人。

## 验证

- 后端：`test_merge_people.py` 16 项、`test_person_merge_rules.py` 12 项、身份/家庭
  路由契约（含新增 `status` 与 `/people/merge` 错误码）、`test_event_participants.py`
  （含新回归）通过；改动的 Python 文件 ruff 检查与格式检查通过。
- 前端：`tsc -b --force`、oxlint、`vite build` 通过；人物相关 45 项测试通过。全套
  vitest 29 项失败，均在 AppShell / CommandPalette / 冒烟 / 时间线等未改动文件；
  同一 HEAD 未修改时为 34 项失败（多出的 5 项就是已删除的 `PeopleView.test.tsx`）。
- 门禁里的既有失败（HEAD 上同样失败，未扩大修复范围）：`import_executor` 反向依赖、
  `Container.model_health` 端口声明、3 个对话仓储方法不可达、改名 pending 仍显示
  占位名的契约用例、module-map 其他 142 处未登记、`container.py` 的格式检查。
- 真实库：复测问答时人物数据已正确（「王王」已确认、36 提及、25 事件参与），回答仍受上面
  的小模型截名影响。新接口与名录状态字段需重启 8191 后生效。

# 素材删除第三阶段 3A：影响清单与证据依赖分析

本阶段基于第二阶段的未提交工作继续实现。只新增分析计划、永久拒写身份和必要的隔离基础设施；不物理删除资产、观察、证据、文件或索引，不修改人物/簇/事件/事实/关系/画像，不重建记忆，不调用 cleanup_derivatives。

## 1. 数据库结构

新增迁移 `0029_media_deletion_plans.sql`，在第二阶段 `0028` 后应用。

### media_tombstones

| 字段 | 含义 |
|---|---|
| kind + object_id | 联合主键，身份类型与原始ID，不依赖ID前缀 |
| scope_id | 原始数据所在scope |
| deletion_id | 删除来源任务ID；没有指向可删除业务行的外键 |
| generation | 隔离代次 |
| created_at | 记录时间 |

类型支持 asset、observation、face_instance、event。当前自动登记的只有目标资产、其观察和人脸实例；不会把共享人物、人脸簇或共享事件写进墓碑。event 类型留给后续显式确认的事件移除。

- 迁移从已有 media_deletion_members 回填墓碑，不修改原业务行。
- 新隔离请求由成员表的 AFTER INSERT 触发器在原事务内登记墓碑。
- 墓碑不可 UPDATE/DELETE，没有过期或解除接口；身份不能复用。
- 新增SQL写屏障按身份检查，源行不存在时也拒绝重建资产、重新挂父、观察/人脸写回、JSON证据、向量和任务恢复。
- 文件发布的 allow_unregistered 分支也先查墓碑，不能把“原行已删”误认为“首次导入新ID”。
- 过滤视图及scope投影屏障改为使用墓碑；原有第二阶段触发器仍保留。Qdrant旧点不删除，交付仍走权威状态过滤。

### media_deletion_plans

| 字段 | 含义 |
|---|---|
| plan_id | 持久计划ID |
| deletion_id | 绑定隔离任务 |
| version | 同一任务的计划版本，从1递增 |
| fingerprint | 来源快照、分析器版本、位置配置和外部盘点的摘要 |
| created_at | 版本生成时间 |
| source_versions_json | 各表数量/指纹、成员隔离代次、schema版本、文件/Qdrant指纹、分析版本等 |
| payload_json | 分类对象、证据引用、依赖边、文件/向量/全文定位、覆盖状态和不确定性 |

`UNIQUE(deletion_id,fingerprint)` 保证相同快照幂等，`UNIQUE(deletion_id,version)` 保证版本顺序。计划是不可变快照，触发器禁止更新或删除；发现变化时创建新版本，保留旧版本。

迁移中 DROP/CREATE 的仅是隔离读取视图，没有删除业务表或业务记录，也没有修改第二阶段任务的状态语义。

## 2. 数据结构与分类

### 对象项 items[]

每项包括：

- `table/key/locator`：真实SQLite表和主键；无显式主键的FTS条目保存rowid。
- `kind/scope_id/cross_scope`：对象身份及跨scope标记。
- `category`：exclusive / shared / derived / unknown。
- `derived`：派生属性单独保留；例如某个人物瞬间既是素材专属行，也属于派生数据。
- `evidence[]`：按实际字段契约解析的 `{kind,id,field}`，以及证据/上下文角色、解析到的资产、目标资产、仍有效资产、其他已隔离资产、未解析标记。
- `target_evidence_count/remaining_valid_evidence_count/remaining_valid_asset_count`：证据与素材分别计数。证据按 `(kind,id)` 去重；不是将观察、人脸、素材混为一种计数。
- `evidence_count_complete`：为false时，已知有效数量只是可确认下限，不能把0解读为“确定没有其他证据”。
- `source_evidence_fields`：保留来源JSON的解析结果；无效JSON还保存原始文本，避免丢掉无法解释的依赖。
- `versions/row_fingerprint/indirect/uncertainties`：版本、间接依赖与人工复核信息。
- `automatic_deletion_allowed:false`：所有项目均如此；本阶段没有删除授权或删除执行器。

结构性专属行包括目标资产、观察、人脸实例、地点行及观察桥等。人物和人脸簇始终归共享身份，不能依据“本次剩余证据为0”批量删除。事实、关系等共享业务对象缺少明确生成来源时，不会因只剩目标证据就被判为素材专属。

人物ID/簇ID是身份上下文，不等于某条事实的来源。事件参与者的所属事件也是上下文，其个人证据从自身证据字段计算。混合了目标与剩余素材的事件引用标为需要依赖重建，不作为一条已完整有效的事件证据计数。

### 精简示例

以下为结构示例，省略完整版本清单和其他对象；实际响应会保存全部字段：

```json
{
  "plan_id": "plan_...",
  "deletion_id": "deletion_...",
  "version": 1,
  "fingerprint": "sha256...",
  "stale": false,
  "source_versions": {
    "analysis_version": "media-deletion-3a-v1",
    "deletion_generation": 1,
    "tables": {
      "facts": {"row_count": 1, "fingerprint": "sha256..."}
    }
  },
  "impact": {
    "root_asset_id": "a",
    "asset_ids": ["a"],
    "analysis_only": true,
    "automatic_deletion_allowed": false,
    "items": [{
      "table": "facts",
      "key": {"id": "fact"},
      "category": "shared",
      "cross_scope": false,
      "target_evidence_count": 1,
      "remaining_valid_evidence_count": 1,
      "remaining_valid_asset_count": 1,
      "evidence_count_complete": true,
      "evidence": [
        {"kind": "observation", "id": "oa", "field": "evidence_ids_json[0]", "role": "evidence", "target_asset_ids": ["a"], "remaining_valid_asset_ids": []},
        {"kind": "observation", "id": "ob", "field": "evidence_ids_json[1]", "role": "evidence", "target_asset_ids": [], "remaining_valid_asset_ids": ["b"]}
      ],
      "automatic_deletion_allowed": false
    }],
    "dependency_edges": [{
      "from": {"table": "person_portrait_revisions", "primary_key": {"id": "portrait1"}},
      "to": {"kind": "person_moment", "id": "moment"},
      "field": "evidence_refs_json[0]",
      "role": "evidence",
      "unresolved": false
    }]
  }
}
```

身份类型严格来自字段契约或显式kind。`oa`即使恰好等于另一个person_moment的ID，也不能在两个类型之间猜测转换。未知kind、缺失引用、非法JSON进入unknown/不完整计数，而不是用SQL LIKE或文本子串建立归属。

## 3. 收集范围

分析直接读取底层表，不使用第二阶段 readable_* 视图，否则会遗漏已隔离证据。

| 范围 | 收集方式 |
|---|---|
| 根资产/多级派生物 | media_deletions + media_deletion_members；校验所有成员仍为对应代次的isolated；不擅自扩大隔离范围 |
| 全部观察与人脸 | 按asset_id及observation_id反查，不只取最新观察 |
| 人脸簇、人物、其他实体 | 实例、entity_mentions、entity_observations及簇entity_id；统计其他有效素材证据 |
| 事件/参与者/实体桥 | event_observations、source_asset_id及事件相关行；保留每条记录的真实证据角色 |
| 事实/关系/属性/主张 | 精确解析证据数组；证据与身份端点区分；多证据行记录剩余支持 |
| 外观/瞬间/画像 | 专属FK、所有历史画像修订、kind/id间接引用；记录画像→瞬间→素材依赖 |
| 家庭对象 | 家庭关系、成员、画像、推断运行、人工关系屏蔽和共享scope组；跨scope匹配记录标记复核 |
| 记忆图 | 节点属性、边两端、构建版本；受影响scope的图投影保守列入，包括明确跨scope依赖的构建记录，不声称逐边因果可撤除 |
| 回忆/历史结果 | 事件序列、已看状态、结果集资产列表、turn/结构化轨迹和消息候选；自由文本不做子串归属判断 |
| SQLite向量 | 两张表及完整space/source_type/source_id/model_name/dimension/scope/行ID |
| Qdrant | 只读scroll实际点，保留collection、原始point ID类型、payload及维度；与SQLite推算的expected locator分开；其他namespace的点标记非当前namespace所有 |
| 全文 | terms真实主键和FTS逻辑rowid，连同资产/观察证据；不操作FTS内部影子表 |
| 文件 | 原片/派生资产路径、browser_preview_path、各尺寸预览、视频/场景分析目录、任务speech/scene_windows及output_artifact、导入原件路径 |

Qdrant不可用时保存明确warning和unavailable覆盖状态，SQLite推算的位置只标为unverified，不伪称已找到全部点。孤儿点、无来源点列入复核候选。

文件清单保存绝对路径、解析路径、来源字段、已知owner及scope、设备/inode/尺寸/mtime、可用的源content_sha256和管理目录标记。检查原片、预览元数据、导入和任务路径的共享引用；硬链接/符号链接/管理目录外路径不能直接视为可删。`ownership_complete:false`明确说明项目尚无统一文件引用注册表。

## 4. 并发、版本与恢复

- 生成使用BEGIN IMMEDIATE，读取和保存计划处于同一SQLite事务。其他SQLite写入要么在之前提交并纳入快照，要么在计划提交后继续，不允许混合前后两个数据库版本。
- 不调用模型；采用实例级保守表指纹，包含跨scope关联，避免遗漏未索引JSON反向引用。代价是无关记录修改也可能令计划过期，且大库全量盘点会持锁较久。
- 指纹包含分析器版本、schema版本、隔离成员/代次、各表快照、data_dir/Qdrant前缀、外部盘点结果；分析语义变更需要升级ANALYSIS_VERSION。
- 重复生成相同快照返回同一plan_id/version/created_at；并发生成同样幂等。
- GET可指定version，动态计算stale，不覆写历史计划。数据变化后POST生成新版本。
- 进程中断时事务回滚，不保留半份清单；已提交计划重启后直接读取。对未来源行缺失的旧计划，要求有匹配墓碑才允许历史读取，不能据缺失源行重新声称生成了完整新计划。
- 文件与Qdrant不能与SQLite形成跨存储事务。受管Qdrant写入已被第二阶段SQLite屏障串行；直接外部客户端、SVD临时文件等仍可能并发变化。文件stat指纹也不是内容完整性证明。第3B必须重新验证外部位置、来源、所有权和内容版本。

API：

```http
POST /api/v1/media-deletions/{deletion_id}/plan
{"scope_id":"s"}

GET /api/v1/media-deletions/{deletion_id}/plan?scope_id=s
GET /api/v1/media-deletions/{deletion_id}/plan?scope_id=s&version=1
```

均只分析/读取计划。scope校验沿用第二阶段的用户独立数据库边界，跨scope引用仅报告，不据此取得修改权限。

## 5. Tombstone检查与仍需解决的问题

### 已补齐

原先“asset行不存在→当成未注册新资产”的发布路径已修正。资产、观察、人脸等拒写身份没有指向源行的FK，旧任务不能依赖源行消失绕过屏障。scope投影暂停也由墓碑驱动，不会在最后一条isolated asset移除后自动解锁。测试用从未存在源行的墓碑模拟删后状态，不通过实际删除业务数据制造场景。

### 第3B前必须处理

1. 第二阶段 media_deletions.root_asset_id 和 media_deletion_members.asset_id 仍有assets外键；它们当前会阻止实际删行。不能关闭foreign_keys绕过。3B需先设计保留任务/计划/墓碑历史的非破坏性元数据迁移；本阶段没有重建这些表。
2. 永久墓碑不能清除。3B要按新数据代次恢复后处理，需将scope级暂停改造为可确认完成的重建代次机制，同时永久保留资产身份拒写。
3. events.summary、entities.summary、画像句子、自由文本消息没有逐句血缘；未知来源及缺失证据只能整体标记待复核/重建，不能精确撤销一句话。
4. 事实的人名字符串、人工确认与模型推断混合、累计MAX(confidence)/support_count无法可靠倒减。有效来源数量不等于结论置信度。
5. 家庭统计、水位和图的推断/聚合边缺少细粒度输入快照；本清单保守覆盖投影，不是所有边的完整因果解释。
6. 缺少统一artifact registry，临时裁剪、未登记文件、管理目录外硬链接无法完全归属；文件盘点是已知路径的best effort。3B禁止按目录粗暴rmtree。
7. Qdrant失联、未知payload或其他namespace不能默认为空/专属；expected locator不等于实际存在。独立绕开受管适配器的裸SQL/Qdrant写入，及未升级的来源不明脚本，不属于自动分析可证明的完整血缘。
8. 当前实例级快照适合先保证正确性，后续可增加证据反向索引与按版本的分页分析，减少大库锁持有时间；不能牺牲同一快照一致性。

## 6. 第3B建议顺序（未实施）

1. 审查unknown、跨scope、共享身份及文件所有权问题；任何不能证明的项目阻断自动执行。
2. 解决任务/成员外键与完成态、重建代次设计；确认全部目标身份墓碑已持久化，后台任务仍被fence/isolation约束。
3. 在受控事务中重新校验计划、成员代次、业务指纹；外部位置独立重新盘点。过期计划重新生成，不直接执行旧JSON。
4. 按精确类型撤除目标素材的关联/证据，保留其他素材贡献与人工内容；生成受影响共享对象的更新/重建任务。禁止按person_id/cluster_id整批删除。
5. 按FK依赖顺序清理专属索引/观察/实例/派生资产等行；共享事件、事实等根据剩余证据另行处理，不能把category当作删除许可。
6. 基于持久清单逐点处理Qdrant、逐文件校验后清理，分别记录进度和幂等结果；不要删除整个collection或共享目录。
7. 从剩余有效原始证据异步更新摘要/画像/图及轮播引用；旧版本任务不得覆盖新版本；完成后恢复对应代次的投影处理，墓碑仍永久保留。

## 7. 涉及文件

本阶段新增：

- `apps/api/src/sentrix/application/media/analyze_deletion.py`：纯依赖分析、类型化证据、归属和不确定性。
- `apps/api/src/sentrix/infrastructure/persistence/repositories/deletion_plans.py`：原表快照、版本化计划、外部盘点与恢复。
- `apps/api/src/sentrix/infrastructure/persistence/migrations/0029_media_deletion_plans.sql`：计划、墓碑、回填和持久拒写基础设施。
- `apps/api/tests/integration/test_media_deletion_plans.py`：3A测试。
- `docs/media_deletion_phase3a.md`：本说明。

在第二阶段文件上继续修改：

- `api/routes/deletions.py`：POST/GET计划API。
- `infrastructure/persistence/media_barrier.py`：缺失源行时仍检查墓碑。
- `infrastructure/vector/qdrant_index.py`：只读实际点盘点及无IO的完整locator计算，保留数字/UUID类型。
- `bootstrap/import_wiring.py`、`infrastructure/persistence/repositories/memory_graph.py`：scope暂停由永久墓碑驱动。

以上路径均属于 `apps/api/src/sentrix/`（测试和文档除外）。没有修改0028迁移、共享业务数据写入规则的领域语义，也未提交或推送代码。

## 8. 验证

测试使用 `SENTRIX_VIDEO_KEYFRAME_ALGORITHM=window`，避免本机缺少Katna影响离线装配；模型是假件，SQLite与Qdrant使用真实适配器。

- 新增3A测试24项：图片、多级视频、共享人物/簇、多证据事实/关系、精确ID/类型、非法JSON、间接画像及历史修订、跨scope家庭/图构建、无来源记录、并发更新/生成、幂等/重启、全部业务行不变、实际Qdrant与孤儿/数字ID、全文和文件定位、共享缓存/符号链接、第二阶段升级回填、缺失源行的旧任务拒写和图屏障。
- 第二阶段隔离测试20项继续保留。
- 聚焦组合回归：211 passed（包含新增24项及第二阶段、迁移、人脸/人物、事实、视频、向量、事件和参与者回归）。
- 五个核心修改模块通过mypy；本阶段核心修改通过Ruff、compileall和git diff --check。

未对正在运行的服务数据库执行迁移，没有启动评测，没有执行物理删除或重建任务。

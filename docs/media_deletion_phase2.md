# 素材删除第二阶段：持久化任务与隔离

基线：`65ce640b`。本阶段只实现隔离，不实现物理删除、证据撤除或摘要/画像/记忆图重建。

## 状态与事务

`AssetStatus` 仍为 queued/processing/processed/failed。导入队列、ImportAlbum处理、视频重用判断、状态更新和API序列化均使用该枚举，因此没有复用它表达删除。

新增迁移 `0028_media_deletion.sql`：

- assets.deletion_state：active / isolated，默认active。
- assets.deletion_generation：默认0；隔离事务加1。阶段2不提供取消隔离或复用原资产ID的接口。触发器禁止回到active、回退版本或覆盖已隔离资产身份。active代次恒为0，因此旧推理快照无法在隔离后的代次提交。
- media_deletions：deletion_id、scope_id、root_asset_id（唯一）、status、created_at、updated_at、retry_count、error、generation、idempotency_key（scope内唯一）。
- media_deletion_members：任务与资产闭包及各自隔离代次的持久关系。
- readable_*视图：过滤隔离来源，不修改底层证据或共享行。
- SQLite触发器：在同一条写语句的最终提交边界检查来源隔离状态，跨进程生效；不是仅在模型调用前查询一次。

请求使用BEGIN IMMEDIATE：验证scope与根资产 → 检查重复请求 → 视频递归UNION展开派生闭包 → 验证闭包scope一致 → 保存任务和成员 → 隔离资产 → fence/cancel已有导入及视频任务 → 提交。图片只锁定目标资产，任何derived_kind或parent_asset_id非空的目标禁止独立删除。

所有动作同事务成功或回滚。返回status=isolated表示“本阶段隔离完成”，绝不表示文件已经删除。因为没有提交后异步执行的隔离步骤，中断在提交前回滚，提交后重启读取同一任务；重复请求不会增加任务、代次、取消次数或重试计数。retry_count/error/failed预留给后续处理，阶段2不提供会重复执行清理的重试worker。

迁移只增加列、表、视图和触发器，未删除/搬迁历史数据。迁移执行器改为sqlite3.complete_statement分句，以支持触发器内部分号，仍逐语句执行并保持原有事务回滚，不使用executescript。

## API与权限边界

```http
POST /api/v1/assets/{asset_id}/deletion
Content-Type: application/json

{"scope_id":"album1","idempotency_key":"optional-request-key"}
```

返回202及持久化任务，包含asset_ids。相同根资产始终返回原任务；已使用的幂等键不能改为另一根资产。幂等身份以根资产为准，重复请求的新键不额外创建别名。

```http
GET /api/v1/media-deletions/{deletion_id}?scope_id=album1
```

跨scope查询/请求按404处理；关键帧或不支持的媒体返回409。API沿用internal_call_guard及现有用户独立数据根/数据库隔离。在单机legacy模式下，scope是相册过滤而非登录用户ACL：本次没有凭空增加账号权限系统。若后续变为一个数据库承载多个用户，必须先增加可信主体与scope授权，不能把客户端传入scope当成跨用户授权证明。

## 写入覆盖

| 入口 | 最终隔离机制 |
|---|---|
| import_assets / observe_asset | observations INSERT/UPDATE触发器；assets处理状态、元数据与路径UPDATE也受保护 |
| extract_video_scenes / add_derived | assets INSERT检查父资产；既存后代已被事务递归隔离；派生事件source_asset_id、观察和事件桥另有触发器 |
| enroll_face | 产品装配注入database.asset_commit，检测完成后匹配/建簇/人物/实例/原型/向量/提及在一个SQLite事务；失败整体回滚；底层实例和原型还有SQL屏障 |
| AssignEvents、MaintainObservationEntities、MaintainFacts | 产品装配在单资产事务内执行，防止已隔离观察先产生空事件/实体等副作用 |
| index_visual | memory_vectors触发器；Qdrant upsert在BEGIN IMMEDIATE下权威检查并完成写入，删除事务与外部发布串行 |
| index_observation_text | memory_vectors/text_vectors的源身份触发器，含observation、face_instance、asset、event；隔离版本不得继续写入 |
| 全文投影 | refresh_from_observation先BEGIN IMMEDIATE并检查资产版本，再改terms和FTS；没有只删旧FTS却未能插入新行的半次刷新 |
| import_jobs | 删除事务设置cancel_requested、cancelled并增加fence；claim_due排除隔离ID；SQL阻止旧任务恢复running/完成状态 |
| video worker/recovery | 隔离事务取消job；SQL禁止隔离job再claim/更新阶段，list_jobs不返回隔离视频；旧任务不能覆盖取消状态 |
| scope_postprocess | scope有隔离资产时，入口返回media_isolation_deferred；图replace_scope在删除旧投影前事务校验；画像/家庭分析/图节点和边有最终SQL防护 |
| 共享事实/属性/关系/事件参与者 | 证据指向隔离观察或事件时阻止新写；不能因旧推理结果产生新的有效证据 |
| 维护脚本 | 使用受管仓储/绑定Qdrant适配器的回填受屏障约束；任意绕过适配器直接改Qdrant不在保证范围 |

多仓储asset_commit使用同一底层SQLite连接，嵌套仓储的with不提前提交。推理在事务之外，禁止把VLM请求包在这个事务里。

### 非事务存储策略

- Qdrant：保留全部旧点；产品容器绑定数据库。写入期间持有短SQLite写事务，不允许“先检查、删除提交、再写Qdrant”的窗口。Qdrant并非可回滚分布式事务；本阶段依靠永久隔离和读侧权威检查屏蔽旧点。
- ffmpeg关键帧/转码：先生成不交付的.pending临时产物，再通过publish_media_file持有SQLite写锁校验代次并原子发布；隔离后不能发布成正式文件。转码发生在首次资产入库之前时允许未登记的新资产ID，已登记隔离ID绝不允许。
- 图片预览与视频speech/scene_windows：临时写入后使用相同发布屏障。
- 已经开始的SVD/Katna分析、人脸模型临时预览可能继续完成临时缓存写入。它们不能提交资产/观察/索引或发布受管可交付素材；临时/失败产物本阶段保留，不在本次进行清理。
- 上传恢复的referenced_paths明确读取底层assets（包括isolated），避免启动对账把保留的原文件当孤儿清除。

## 读取覆盖

| 路径 | 处理 |
|---|---|
| 素材列表、计数、get/get_many、按hash查询 | 使用readable_assets；隔离资产不作为有效素材返回 |
| 原图/视频/预览/缩略图、历史结果集图片交付 | 通过素材权威get，隔离后404；预览发布前再次检查 |
| 视频详情、场景、关键帧 | 根视频权威检查；后代与视频场景查询使用过滤视图 |
| 时间线/回忆 | 使用readable_events；只要事件仍含隔离观察或来源视频，就暂时隐藏整个事件，底层事件与桥记录不删除 |
| 观察/人物证据/瞬间/外观/原型 | 对应readable_*视图过滤，不移除人物或人脸簇身份 |
| 全文 | terms过滤视图；FTS MATCH保留真实虚表并附加readable_assets条件 |
| SQLite向量 | 两张向量表的readable视图按source_type/source_id过滤 |
| Qdrant | 返回前逐批以SQLite核对asset是否有效及scope；不删除旧点 |
| 多路检索/问答检索 | RetrieveEvidence在融合/重排后再次通过asset_repository.get_many校验，覆盖旧Qdrant及图拓展候选 |
| 事实/属性/关系/语义档案/画像 | 对可定位的JSON证据及人物提及使用过滤视图，原共享记录保留 |

## 明确的未覆盖范围和代价

1. 不实现文件、观察、人脸、索引点、人物、事件、事实、关系或画像的实际删除，不调用cleanup_derivatives，不生成新摘要/画像/图。
2. 对共享事件和多证据事实采用保守隔离：即使仍有其他素材支撑，旧派生结果也可能暂时不展示；其他素材本身仍可访问，人物/簇/事实原行不变。第三阶段需精确撤证和重建，恢复这些共享视图。
3. 有隔离资产的scope暂停批末投影重建，直到后续阶段接管；这会影响同scope新导入素材的批末摘要/画像进度，基础素材导入并未禁用。
4. 未对所有无类型的历史文本、entities.summary、family分析历史输出、对话消息逐句溯源清洗；家庭部分读取仍是原有逻辑。不能将历史叙述当作“已删除内容被彻底遗忘”的保证。资产文件和有效素材检索交付已被屏蔽。
5. 记忆图的旧节点、边和路径仍留在数据库；候选资产最终过滤，但间接路径权重未重算。内部诊断数据并非物理删除后的干净图。
6. 本阶段不提供解除隔离、原ID恢复、批量前端交互、物理清理worker；未来恢复功能必须重新设计generation比较，不能直接把deletion_state改回active。
7. 不能收回删除提交前已经流出的响应字节、用户下载文件或浏览器既有缓存。独立第三方连接直接读取数据库/索引也不受产品读适配器过滤。
8. 不可把未绑定数据库的独立QdrantVectorIndex实例用于产品写入；容器已绑定，独立索引单元测试保留无数据库模式。
9. schema升级尚未应用到用户正在运行的服务数据库；本次仅在临时测试数据库运行迁移，没有重启服务或启动评测。

## 自动化验证

新增integration/test_media_deletion.py，共20项：任务持久化/重启、重复与并发幂等、scope、关键帧拒绝、递归视频闭包、跨scope回滚、新派生INSERT/重新挂父拒绝、推理完成后拒绝enrich、新观察/人脸/向量拒绝、状态与代次不可覆盖、共享人物/簇/事实原行不变、Qdrant旧点过滤与写入串行、文件发布拒绝、任务重启不恢复、原件所有权保留、媒体/视频HTTP、全文保留但不交付、共享事件保留但隐藏。

测试使用`SENTRIX_VIDEO_KEYFRAME_ALGORITHM=window`，避免本机未安装Katna造成应用装配失败；模型使用确定性假件，SQLite和Qdrant是真实适配器。

- 组合回归：212 passed，3 failed；三项失败在未修改的65ce640b临时源码副本中均复现（该基线子集25 passed / 3 failed）。
- 基线失败：test_search_routes中两项text_ann缺失；test_memory_graph_refresh_is_fail_open_after_memory_writes仍patch旧media.build_memory_graph。未在本次顺带修复。
- 另运行全绿的聚焦集合：187 passed（其中新增隔离测试20项），覆盖新功能、迁移、人物/簇、事实、视频仓储、两类向量、事件分配和参与者。
- 新增核心代码ruff检查、compileall及git diff --check。

## 本阶段文件清单

- `apps/api/src/sentrix/api/routes/deletions.py`
- `apps/api/src/sentrix/api/routes/media.py`
- `apps/api/src/sentrix/application/identity/enroll_face.py`
- `apps/api/src/sentrix/application/retrieval/retrieve_evidence.py`
- `apps/api/src/sentrix/bootstrap/assistant_wiring.py`
- `apps/api/src/sentrix/bootstrap/container.py`
- `apps/api/src/sentrix/bootstrap/import_wiring.py`
- `apps/api/src/sentrix/domain/media/deletion.py`
- `apps/api/src/sentrix/domain/media/models.py`
- `apps/api/src/sentrix/infrastructure/media/video_keyframes.py`
- `apps/api/src/sentrix/infrastructure/media/video_transcode.py`
- `apps/api/src/sentrix/infrastructure/persistence/database.py`
- `apps/api/src/sentrix/infrastructure/persistence/media_barrier.py`
- `apps/api/src/sentrix/infrastructure/persistence/migrations/0028_media_deletion.sql`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/appearance.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/assets.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/audio.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/deletions.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/entities.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/events.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/faces.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/facts.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/family.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/import_jobs.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/memory_graph.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/moments.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/portraits.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/relationships.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/search_terms.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/semantic.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/structured.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/vectors.py`
- `apps/api/src/sentrix/infrastructure/persistence/repositories/video.py`
- `apps/api/src/sentrix/infrastructure/vector/qdrant_index.py`
- `apps/api/src/sentrix/main.py`
- `apps/api/tests/integration/test_media_deletion.py`
- `docs/media_deletion_phase2.md`

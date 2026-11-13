| 方法 | 路径 | 普通用户 | 备注 |
| --- | --- | --- | --- |
| GET | `/api/v1/assets` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/assets/{asset_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/assets/{asset_id}/file` | 允许 | 媒体交付；按用户数据根隔离 |
| GET | `/api/v1/assets/{asset_id}/poster` | 允许 | 媒体交付；按用户数据根隔离 |
| GET | `/api/v1/assets/{asset_id}/preview` | 允许 | 媒体交付；按用户数据根隔离 |
| GET | `/api/v1/assistant/conversations` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/assistant/conversations` | 允许 | 按当前用户数据根与 scope 过滤 |
| DELETE | `/api/v1/assistant/conversations/{conversation_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/assistant/conversations/{conversation_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| PATCH | `/api/v1/assistant/conversations/{conversation_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/assistant/conversations/{conversation_id}/turns` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/assistant/result-sets/{result_set_id}/photo` | 允许 | 结果集照片；按结果集和 scope 校验 |
| GET | `/api/v1/assistant/turns/{turn_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/assistant/turns/{turn_id}/cancel` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/assistant/turns/{turn_id}/events` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/diagnostics` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/entities` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/entities/{entity_id}` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| PUT | `/api/v1/entities/{entity_id}/properties/{property_key}` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/entity-groups` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/entity-groups/{group_id}` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/events/{event_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/face-clusters` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/face-clusters/merge` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/face-clusters/{cluster_id}/confirm` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/face-clusters/{cluster_id}/reject` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/face-clusters/{cluster_id}/split` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/face-instances/{face_instance_id}/crop` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/facts` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/facts/{fact_id}/confirm` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/facts/{fact_id}/reject` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/family-graph` | 允许 | 按当前用户数据根与 scope 过滤 |
| PATCH | `/api/v1/family-graph/people/{person_id}/membership` | 允许 | 按当前用户数据根与 scope 过滤 |
| PATCH | `/api/v1/family-graph/people/{person_id}/portrait` | 允许 | 按当前用户数据根与 scope 过滤 |
| DELETE | `/api/v1/family-graph/relationships` | 允许 | 按当前用户数据根与 scope 过滤 |
| PUT | `/api/v1/family-graph/relationships` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/family-graph/runs` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/family-graph/scopes/merge` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/health` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/health/live` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/import-jobs` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/import-jobs/{job_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/import-jobs/{job_id}/cancel` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/imports` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/imports/assets` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/imports/{operation_id}/cancel` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/imports/{operation_id}/progress` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/internal/status` | 内部服务 | 仅体验模式 + 内部令牌 |
| GET | `/api/v1/knowledge` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/maintenance/recluster-faces` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/media/import` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| POST | `/api/v1/media/uploads` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/memories/rotation` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/memories/rotation/viewed` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/memory-graph/quality` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/memory-spaces` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/people` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/people/batch-confirm` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/people/seed` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/people/{person_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| PATCH | `/api/v1/people/{person_id}/name` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/people/{person_id}/portrait` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/people/{person_id}/portrait-feedback` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/person-insights` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/person-insights/run` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/pipeline/process` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/relationships` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/relationships` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/relationships/batch` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/relationships/{relationship_id}/confirm` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/relationships/{relationship_id}/retract` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/search` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/timeline` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/video-scenes/{scene_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| GET | `/api/v1/videos/{asset_id}` | 允许 | 按当前用户数据根与 scope 过滤 |
| POST | `/api/v1/videos/{asset_id}/reprocess` | 403 | 体验模式禁用（控制面与用户 API 双层拒绝） |
| GET | `/api/v1/videos/{asset_id}/scenes` | 允许 | 按当前用户数据根与 scope 过滤 |

共 77 条方法+路径。

> 说明：上表由 `create_app().openapi()` 生成，覆盖 69 条既有路由与体验新增的
> 上传/作业/回忆/内部状态路由，共 77 条方法+路径。普通用户判定使用用户 API 的
> `_EXPERIENCE_DENIED`；控制面网关还有一份同规则的前置拒绝。

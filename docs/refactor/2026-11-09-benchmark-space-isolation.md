# 评测人物混入家庭人物的验证与修复

## 已验证事实

本地 `data/sentrix.db` 的 5 个空间均与 2026-11-06 的 PhotoBench full 运行记录逐一对应。
记录中的 pipeline_asset_ids 在对应空间中分别匹配 14、14、344、344、344 条资产，
并存在 352 个跨空间重复的文件 SHA-256，以及 8 个跨空间重复的已确认人物姓名。
确认存在重复测试数据，不能只凭显示名或 PhotoBench 名称前缀判断来源。

此前 scope_setup 不登记空间。种身份/导入调用默认 ensure，将空间建为 household、
include_in_people=1；人物列表又查询所有可见空间，所以多轮评测的人物一起进入家庭人物页。
取消或失败的运行已经写入的数据也保留。

## 修复边界

- 通用 `POST /api/v1/memory-spaces` 接收 `{id,name,kind}`，kind 默认为 household，
  可显式指定 benchmark。重复登记幂等；已有不同用途返回 409，不能静默重分类。
- 空间仓储新建 benchmark 时设置 include_in_people=0，沿用现有实体/簇/家庭图谱
  的展示过滤；普通导入继续默认 household。ensure 不覆盖既有用途与可见性。
- PhotoBench full/build 在种身份与导入前显式登记 benchmark，登记失败或返回身份不符
  即停止，避免旧 API/网络错误时回落为可见家庭空间。reuse 不写空间。
- 新增端口方法 ensure_space，旧方法签名及 run.json 原字段保持；OpenAPI 与前端类型同步。
  不在产品导入逻辑硬编码评测 scope 前缀、人物姓名或测试数据内容。

## 历史修复与回滚

`scripts/maintenance/repair_benchmark_spaces.py` 默认只读。它要求运行记录的 scope_id
与真实资产 ID/空间同时匹配；仅名称匹配不会修复，活跃运行会拒绝执行。
`--apply` 先生成一致 SQLite 备份和 audit.json，再用事务及旧值校验更新空间元数据。
不删除/合并照片、人物、关系，也不改历史评测文件。

本次已修复 5 个空间；57 张表的行数均与备份一致（1110 条资产、3552 条实体）。
备份及原始元数据在 `data/backups/benchmark-spaces-ewr1kt7c/`。
恢复展示时应只按 audit.json 中的 before 恢复相应空间的 kind/include_in_people/updated_at，
不要直接覆盖仍在使用且可能已有新上传的数据库。

本地、局域网与 Vite 代理的 `/api/v1/family-graph?scope_id=` 均已验证不再返回评测人物。
当前库中人物全部来自评测，人物页变空是移除测试人物后的结果；数据仍在数据库中。
这次修复针对人物展示，未将现有评测媒体整体迁往其他数据库，资料库等其他聚合入口
仍可能包含测试素材。评测与日常使用如需完整隔离，应部署独立数据目录的 API 实例。

## 验证

空间 API、历史修复备份/幂等/证据检查、原人物路由与仓储、PhotoBench 注册 HTTP
与执行顺序/失败阻断/reuse 不写的 189 项回归测试通过，无失败或跳过。修改模块的类型检查、
lint、格式、前端 TypeScript、PhotoBench 可达性与能力登记检查通过。
已正常重载 8191 API 与 8772 PhotoBench，确认线上 OpenAPI 含空间登记接口、API 健康检查
各项为 ok、4274 代理不再返回评测人物。没有运行新一轮模型评测。
全量 CI 在其他文件既有的 34 个 lint 问题处中止；既有类型/模块地图问题未扩大修复。

# 0003. 文本向量拆到同库独立表 `text_vectors`

日期：2026-10-26
状态：已接受

## 背景

`memory_vectors` 同时存放三类向量：

- `visual` / `asset`、`visual` / `face_instance`：视觉检索，经 Qdrant 派生索引加速；
- `episodic` / `observation`、`semantic` / `observation`、`episodic` / `event`：
  文本检索，只走 `SqliteVectorRepository.search_exact` 的全表扫描，从不进入 Qdrant。

两类数据共表带来三个具体问题：

1. 文本检索每次都要扫过 `visual` 空间的行。`WHERE space = ?` 能剪枝，但表级统计与
   维护粒度仍混在一起；
2. 视频清理在 `video.py` 用裸 SQL 同时删两类向量，任何一方结构变化都会互相牵连；
3. 将来给文本接 ANN 时，无法只针对文本行建索引，也无法独立搬迁。

## 决策

在**同一个 SQLite 文件**中新增 `text_vectors` 表（`0015_text_vectors.sql`），
`semantic` / `episodic` 的写入与检索切到这张表；`memory_vectors` 之后只存视觉/人脸行。

- 表结构与 `memory_vectors` 逐列一致，物理主键 `id` + 自然键
  `UNIQUE(space, source_type, source_id, model_name)`；
- `CHECK (space IN ('semantic', 'episodic'))` 把「只有文本空间」变成数据库层事实；
- `VectorRepository` 端口**不变**。两个适配器
  （`SqliteVectorRepository` / `SqliteTextVectorRepository`）共享
  `VectorTableRepository`，差别只有表名；`Container` 增加
  `text_vector_repository: VectorRepository` 字段；
- 迁移只建表，不复数据。存量复制由独立维护脚本
  `scripts/maintenance/text_vectors_migrate.py` 执行，默认 dry-run、每次落审计 JSON。

## 被否方案

**独立向量数据库**：文本量级是每张资产几条，SQLite 全表扫描已经在毫秒级；引入第二个
存储要处理备份、事务边界与一致性，收益不成立。

**本次接 ANN**：文本行的召回规模不构成瓶颈，且 ANN 需要先决定索引生命周期与重建策略。
分表是接 ANN 的前置条件，但不是同一次变更。

**运行期双写**：需要开关、写放大，两个连接之间仍有窗口；回滚改用「停 API + 反向同步」
（`reverse`），一次覆盖新增/更新/删除，逻辑单一且可审计。

## 上线与回滚

上线顺序：停 API → `copy --apply` → `verify`（冲突必须为 0）→ 部署新 Container → 启动。

切换后、purge 前回滚：

1. 停 API；
2. `reverse --dry-run` 审阅差异，有 `id_mismatch` / `malformed` 则中止；
3. `reverse --apply`（单事务 upsert 新行 + 删除 old 中已不存在的文本行）；
4. `verify --direction reverse`；
5. revert 切换 Container 的提交并重新部署。

`purge` 是之后单独执行的人工步骤：先做一致性备份（`Connection.backup` +
`PRAGMA integrity_check` + sha256），再删除 `memory_vectors` 的文本空间行。**purge 之后
仅把 Container 切回 `memory_vectors` 无法回滚**——旧行已删除，必须先
`restore-old --backup-path ...` 放回备份，再 `reverse` 补齐增量。

完整命令与判断标准见 `docs/runbooks/text-vector-migration.md`。

## 后果

- 正面：文本检索不再扫视觉行；视频清理按表拆开；文本 ANN 与独立搬迁有了明确落点；
- 负面：同一份数据在迁移窗口内存在两份（old + new），需要 `verify` 把关；purge 前
  必须保持两者的反向同步纪律。

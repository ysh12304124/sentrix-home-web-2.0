# 文本向量迁移：`memory_vectors` → `text_vectors`

适用版本：含 `0015_text_vectors.sql` 的 2.0。设计决策见
[ADR 0003](../adr/0003-text-vectors-separate-table.md)。

所有命令从**仓库根**执行。脚本默认 dry-run，**任何写操作都要显式 `--apply`**；
每次运行都会把审计 JSON 落到 `data/reports/text-vectors/`（可用 `--report-dir` 覆盖）。
默认目标库是 `load_settings().database_path`（通常是 `data/sentrix.db`），可用 `--db` 指定。
脚本启动时先跑迁移，确保 `0015` 已应用。

```bash
MIG=".venv/bin/python scripts/maintenance/text_vectors_migrate.py"
```

下文用 `MIG` 代指 `.venv/bin/python scripts/maintenance/text_vectors_migrate.py`。

## 0. 前提

- 迁移窗口内 **API 与回填脚本都必须停**：`copy` / `reverse` / `purge` 假定
  `text_vectors` 不再变化。
- 建议先复制一份库演练：`cp data/sentrix.db /tmp/sentrix-rehearsal.db`，所有命令加
  `--db /tmp/sentrix-rehearsal.db`。WAL 模式下请用 `sqlite3.Connection.backup`
  （脚本的 `purge` 已这样做）或在 API 停止后再复制。
- 退出码：`0` 成功；`1` 校验失败 / 有冲突 / 拒绝执行（报告里写明原因）；参数错误 `2`。

## 1. 直接上线（old ⊆ new）

```bash
# 1) 停 API / 回填脚本
systemctl --user stop sentrix-api.service

# 2) 看会复制多少行
$MIG copy

# 3) 真正复制；保留 id / created_at / updated_at，幂等
$MIG copy --apply

# 4) 全量校验：必须 conflict_total=0 且 missing_in_new 为空
$MIG verify --direction forward

# 5) 部署新代码（Container 指向 text_vector_repository），启动 API
systemctl --user start sentrix-api.service
```

判读 `verify`：

| 字段 | 含义 | 要求 |
| --- | --- | --- |
| `missing_in_new` | old 有、new 无 | 必须为空；否则重新 `copy --apply` |
| `extra_in_new` | new 有、old 无 | 切换前应为空；不为空说明迁移窗口内有写入 |
| `id_mismatch` / `malformed` | 主键或行结构不一致 | 必须为 0，先人工排查 |
| `dimension_mismatch` | 声明维度 ≠ 实际向量长度 | 必须为 0 |
| `vector_mismatch` / `metadata_mismatch` | 内容哈希不同 | 默认策略下中止，人工确认 |
| `timestamp_mismatch` | 时间戳不同 | 复制路径应完全相等；反向同步时 `updated_at` 允许变新 |
| `conflict_total` | 以上总和 | 切换前必须为 0 |

冲突策略：`--conflict abort`（默认）/ `prefer-old` / `prefer-new`。`id` 冲突永远中止。
报告中只记录自然键、`id` 与截断后的哈希，不落向量明文。

## 2. 切换后、purge 前回滚（反向同步）

```bash
systemctl --user stop sentrix-api.service

$MIG reverse              # dry-run：看将插入/删除什么
$MIG reverse --apply      # 单事务：upsert new → old，并删除 old 中已不存在的文本行
$MIG verify --direction reverse

git revert <切换 Container 的提交>
# 重新部署并启动 API
```

- `reverse` 覆盖**新增 / 更新 / 删除**三类差异：新行 upsert 回 `memory_vectors`，
  `memory_vectors` 文本空间中 new 已不存在的行会被删除。
- `text_vectors` 为空时 `reverse` 默认拒绝（会删光旧文本行）；确实要清空才加
  `--allow-empty-source`。
- 回滚后如果又有新数据，重新 `copy --apply` + `verify` 即可再次切换。

## 3. Purge 旧行（不可逆，人工确认）

前置条件（任一不满足脚本拒绝执行）：`verify` 通过且无冲突、API 已停止、
`--apply` 与 `--backup-path` 同时给出。

```bash
systemctl --user stop sentrix-api.service
$MIG verify --direction forward

$MIG purge --backup-path data/backups/sentrix-pre-text-purge.db          # dry-run
$MIG purge --apply --backup-path data/backups/sentrix-pre-text-purge.db  # 真删
```

`purge --apply` 会：

1. 用 `sqlite3.Connection.backup` 生成一致性备份（WAL 下不直接拷文件），拒绝覆盖已有文件；
2. 校验备份：`PRAGMA integrity_check`，写 `<backup>.sha256` 旁文件，并比对备份与源库
   快照的文本行；
3. 单事务执行 `DELETE FROM memory_vectors WHERE space IN ('semantic','episodic')`；
4. 复查旧表文本空间行数为 0。`text_vectors` 与视觉/人脸行不动。

**风险**：purge 之后仅把 Container 切回 `memory_vectors` **无法回滚**——旧行已经不存在，
文本检索会变空。恢复走第 4 节。

## 4. Purge 后恢复

```bash
systemctl --user stop sentrix-api.service

# 1) 从 purge 前备份把文本行放回 memory_vectors（只增不删）
$MIG restore-old --backup-path data/backups/sentrix-pre-text-purge.db          # dry-run
$MIG restore-old --apply --backup-path data/backups/sentrix-pre-text-purge.db

# 2) 用 text_vectors 补齐备份之后的增量（新增/更新/删除）
$MIG reverse --apply
$MIG verify --direction reverse

git revert <切换 Container 的提交>
# 重新部署并启动 API
```

若备份的 `.sha256` 与文件不符，先别恢复，检查备份是否完整。

## 5. 日常巡检

- 迁移窗口结束后旧行可保留一段时间；建议每周或每次批量导入后跑一次
  `$MIG verify --direction forward`，确认 `extra_in_new`、`missing_in_new` 仍为空。
- 切换后再次运行 `copy` 会把 old 的新增行补进 new（幂等）；`copy` 不会删除 new 里多出的行。
- 视频清理：`asset` / `face_instance` 行删 `memory_vectors`，`observation` / `event`
  行删 `text_vectors`，两者在 `video.py:cleanup_derivatives` 中分表执行。

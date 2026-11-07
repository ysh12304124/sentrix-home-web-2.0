# 体验服务备份与单用户恢复

备份按用户边界建立，可单独删除/恢复一名用户，不影响其他用户与旧 8191。

## 1. 备份对象

| 对象 | 路径 |
| --- | --- |
| 控制库 | `/srv/sentrix/control/control.db` |
| 控制截图 | `/srv/sentrix/control/feedback/` |
| 用户 SQLite | `/srv/sentrix/users/<account>/sentrix.db` |
| 用户媒体 | `/srv/sentrix/users/<account>/media/` |
| 用户 Qdrant | `/srv/sentrix/users/<account>/qdrant/` |
| 媒体清单 | `find media -type f -printf '%P %s %T@\n'` 的输出 |

## 2. 一致性顺序

1. 停止该用户 API：`systemctl stop sentrix-experience-api@<account>.service`，等写入结束。
2. 用 SQLite 在线备份 API 备份库（不要直接 `cp` 正在写的库）：

```bash
sudo -u sentrix-<account> sqlite3 /srv/sentrix/users/<account>/sentrix.db \
  ".backup '/srv/sentrix-backups/<account>/sentrix-$(date +%F).db'"
```

3. 同步媒体与 Qdrant，生成清单；按需加密后写入受控存储。
4. 记录该用户环境的 `SENTRIX_INTERNAL_TOKEN` 指纹（不记录明文）与控制库 schema 版本。
5. 重新启动该用户 API。

控制库备份前同样用 `.backup`；截图目录按反馈保留期一起备份。

## 3. 单用户恢复演练

1. 在隔离目录恢复该用户 `sentrix.db`、`media/`、`qdrant/`。
2. 启动一个使用该目录的临时 API 实例（独立端口、独立令牌，不接控制面）。
3. 核对媒体数量、事件数量、最近一条问答回合与回忆接口首页。
4. 核对另一名用户未受影响，旧 8191 未受影响。
5. 演练记录写入控制库审计：`restore.drill`。

## 4. 删除与保留

- 删除用户：撤销全部会话 → 停该用户单元 → 清理 `/srv/sentrix/users/<account>` 与
  备份保留期内副本 → 控制库写审计。
- 保留：运行日志与分钟级趋势 30 天；反馈与审计 90 天；原件、记忆与问答按体验期
  及用户删除请求保留。
- 已确认接收的新媒体不得因回滚旧快照而丢失；回滚优先切发行目录并向前修复。

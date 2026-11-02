# 体验服务日常运维

适用：`sentrix-demo-*` / `sentrix-experience-*` 控制面与用户 API。功能验收与故障注入见
[`experience-fault-drills.md`](experience-fault-drills.md)。

## 1. 管理台能看到什么

独立管理入口（8220）与用户入口里的管理员账号（8210）共用同一套控制面数据：

- 控制面/前端构建 SHA、实际监听与模型上游。
- 主模型、BGE、CLIP、各用户 API 的独立探活；嵌入用真实编码维度，主模型用低频真实推理。
- 作业与回合的**在飞状态**：阶段、进度、尝试次数、等待原因、下次重试时间、最近更新。
- 每用户区分展示：在飞导入、在飞问答、最近作业/回合、资产数、构建版本、最老活动时间。
- 模型队列：问答/导入/评测等待数、最久等待秒数、60 分钟趋势。
- 告警：服务中断、10 分钟无进展、重启循环、磁盘、GPU 温度/显存、排队过久、传感器缺测。
- 反馈收件箱：按账号/类型/状态/时间/关联 ID 筛选，查看用户确认后提交的截图。
- systemd 单元：真实 `MainPID`、`ActiveState`、`NRestarts`，以及固定单元的重启按钮。

问答正文、媒体内容、用户 SQLite 不进入控制库，也不出现在管理台。

## 2. 固定端口与自恢复

| 服务 | 端口 | 单元 |
| --- | --- | --- |
| 用户入口/控制面 | 8210 | `sentrix-demo-control.service` |
| 独立管理入口 | 8220 | `sentrix-demo-admin.service` |
| Alice API | 8301 | `sentrix-demo-alice.service` |
| Bob API | 8302 | `sentrix-demo-bob.service` |
| 共享主模型 | 8100 | `sentrix-demo-llama.service` |
| Chinese-CLIP | 8113 | `sentrix-demo-clip.service` |
| BGE-M3 | 8101 | 1.0 `sentrix-api.service` 的子进程（见 §6） |

所有 demo 单元 `enabled` 且 `Restart=always`。意外退出由 systemd 拉起；不要用端口/进程号
手工管理服务。

管理台的重启动作只接受白名单单元，命令参数不拼接用户输入；同一 `Idempotency-Key`
重放返回第一次结果，不会再次执行。共享模型单元重启前会提示“影响全部用户”。

## 3. 趋势与采样

- 每 10 秒采样主机、服务、用户 API 与模型队列；面板超 30 秒无新样本标“采样已过期”。
- 每 60 秒把窗口内的 10 秒样本聚合成一条 `minute` 记录：资源取均值，队列与在飞数量取峰值。
- 管理台“60 分钟趋势”读 `minute` 序列；控制面重启不清历史。
- 排队最久超过 `SENTRIX_CONTROL_QUEUE_WAIT_WARN_S`（默认 300 秒）产生 `queue_long` 告警，
  队列回落后自动关闭。

## 4. 保留期

控制面在启动后每 6 小时执行一次清理：

| 数据 | 默认保留 | 配置 |
| --- | --- | --- |
| `metric_samples`（含分钟趋势） | 30 天 | `SENTRIX_CONTROL_METRIC_RETENTION_DAYS` |
| `audit_events` | 90 天 | `SENTRIX_CONTROL_AUDIT_RETENTION_DAYS` |
| `feedback` / 截图文件 | 90 天 | `SENTRIX_CONTROL_FEEDBACK_RETENTION_DAYS` |

运行日志由 systemd journal 管理，保留 30 天。宿主机执行（需 root）：

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/sentrix-retention.conf >/dev/null <<'EOF'
[Journal]
MaxRetentionSec=30day
SystemMaxUse=4G
EOF
sudo systemctl restart systemd-journald
```

查询：

```bash
journalctl -u sentrix-demo-control.service -u sentrix-demo-alice.service --since "1 hour ago"
journalctl -u sentrix-demo-admin.service --since today | grep -E 'service.restart|feedback.attachment'
```

## 5. 重启与故障处理

1. 先看管理台“按用户运行”：是某个用户 API、共享模型，还是 BGE/CLIP 不可用。
2. 依赖不可用时作业会进入 `waiting_dependency`，恢复后自动唤醒；不要重启用户 API 作为恢复手段。
3. 用户 API 重启：管理台单元面板点“重启”，或在主机执行
   `sudo systemctl restart sentrix-demo-alice.service`。
4. 共享模型重启会中断所有在飞问答；重启后回合从持久状态恢复，用户不需要重新提问。
5. 作业卡在 `needs_attention` 时先排除原因，再在管理台“重新排队”；不要对正在执行的作业重排。
6. 控制面重启会清空内存里的模型等待队列；用户 API 的持久作业与回合会重新发起。

## 6. 已知边界

- BGE 8101 目前是 1.0 API 的子进程，没有独立 systemd 单元；子进程挂掉不会由 systemd 单独拉起。
  正式切 3–5 人前应拆成独立受管单元（`sentrix-legacy-bge.service`）。
- 8100 的旁路消费者（8194、8291、8091 等）未接入控制入口时，管理台会显示“共享模型调度未全覆盖”；
  此时不能宣称问答优先级全局生效。
- 用户 API 取消只保证在安全检查点停止，不强制杀死在飞的模型 HTTP 请求。

# 体验演示固定端口与 systemd 托管

演示数据位于 `/home/asus/sentrix-demo/`，代码工作树 `/home/asus/Github/sentrix-home-web2.0-codex`。
端口固定如下，全部由 systemd 系统单元托管，`Restart=always`。

| 端口 | 服务 | 单元 | 说明 |
| --- | --- | --- | --- |
| 8100 | 共享主模型 llama.cpp `qwen35-q4km` | `sentrix-demo-llama.service` | `--parallel 2` |
| 8101 | BGE-M3 文本嵌入 | 1.0 API 子进程 | 不归演示单元管理 |
| 8113 | Chinese-CLIP 768 维 | `sentrix-demo-clip.service` | 演示端口；正式部署用 8103 |
| 8210 | 用户入口（登录/转发/反馈） | `sentrix-demo-control.service` | 局域网 HTTP |
| 8220 | 独立管理台 | `sentrix-demo-admin.service` | 只允许管理员，独立 cookie |
| 8301 | alice 用户 API | `sentrix-demo-alice.service` | 只监听 127.0.0.1 |
| 8302 | bob 用户 API | `sentrix-demo-bob.service` | 只监听 127.0.0.1 |

8102 WeMM 已停止；旧 8191/8772/8091 不归这些单元管理。

## 管理命令

```bash
systemctl status sentrix-demo-{llama,clip,control,admin,alice,bob}.service
journalctl -u sentrix-demo-control.service -f
sudo systemctl restart sentrix-demo-alice.service
```

管理台（8220）的“重启服务”只接受 `control.env` 中 `SENTRIX_CONTROL_RESTART_UNITS`
列出的固定单元，并通过 `/etc/sudoers.d/sentrix-demo` 的最小权限规则执行。

## 自动恢复

所有单元启用 `Restart=always`，进程退出（包括 `kill -9`）后 3–10 秒自动拉起。
实测：杀掉 alice 的 MainPID 后单元回到 `active`，新 MainPID 更换，8301 健康检查恢复 200。

开机自启：全部单元已 `systemctl enable`。若 `llama` 加载较慢，用户 API 仍会启动；
导入/问答在依赖恢复后自动继续，不需要重启 API。

## 登录入口

- 用户：`http://192.168.0.153:8210/`
- 管理：`http://192.168.0.153:8220/`

两个入口使用不同 cookie 命名空间（`sentrix_control_*` / `sentrix_admin_*`），
同一浏览器可同时保持 alice 会话与管理台会话。

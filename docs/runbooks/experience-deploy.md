# 体验服务部署手册

本手册描述**用户执行**的部署步骤。实施代理只验证语法与静态编译，不启动服务、不做容量测试。

## 1. 目录与服务身份

| 对象 | 路径 / 名称 |
| --- | --- |
| 发行件 | `/opt/sentrix/releases/<40-hex-git-sha>/`（只读） |
| 当前版本 | `/opt/sentrix/current` → 发行目录 |
| 控制库与截图 | `/srv/sentrix/control/` |
| 用户数据根 | `/srv/sentrix/users/<account>/`（0700） |
| 控制面单元 | `sentrix-experience-control.service` |
| 每用户单元 | `sentrix-experience-api@<account>.service` |
| CLIP 侧车 | `sentrix-legacy-clip.service`（8103，768 维） |
| 配置 | `/etc/sentrix/control.env`、`/etc/sentrix/user-<account>.env`、`users.json`、`model-clients.json`（均 0600） |

创建身份与目录（一次性，按实际账号）：

```bash
sudo useradd --system --home /srv/sentrix/users/alice --shell /usr/sbin/nologin sentrix-alice
sudo install -d -o sentrix-alice -g sentrix-alice -m 0700 /srv/sentrix/users/alice
sudo useradd --system --shell /usr/sbin/nologin sentrix-control
sudo install -d -o sentrix-control -g sentrix-control -m 0700 /srv/sentrix/control
sudo useradd --system --shell /usr/sbin/nologin sentrix-models
```

## 2. 配置

1. 从 `deploy/experience/*.example` 复制到 `/etc/sentrix/`，填入密钥并 `chmod 600`。
2. `control.env` 的 `SENTRIX_CONTROL_BUILD_SHA` 填本次发行 SHA。
3. `users.json` 的 `api_token` 与 `user-<account>.env` 的 `SENTRIX_INTERNAL_TOKEN` 必须一致。
4. `model-clients.json` 中每个用户 API 的令牌与 `SENTRIX_MODEL_GATEWAY_TOKEN` 一致；
   PhotoBench 使用单独的 `kind=benchmark` 令牌，并设置它的
   `SENTRIX_BENCHMARK_GATEWAY_URL` / `SENTRIX_BENCHMARK_GATEWAY_UPSTREAM` /
   `SENTRIX_BENCHMARK_GATEWAY_TOKEN`。
5. 证书 SAN 必须包含测试设备实际访问的主机名/IP；不能要求浏览器忽略证书警告。

## 3. 启动顺序

```bash
# 1. 发行件
sudo ./deploy/experience/install-release.sh <sha> /path/to/checkout

# 2. 控制库迁移与账号（在发行目录内执行）
cd /opt/sentrix/current
sudo -u sentrix-control .venv/bin/python -m sentrix.control.main seed-user alice \
  --role user --api-url http://127.0.0.1:8301 --data-root /srv/sentrix/users/alice

# 3. 依赖服务：BGE 8101 与 CLIP 8103 各自有唯一托管者
sudo systemctl enable --now sentrix-legacy-clip.service

# 4. 控制面（先只有管理员能登录）
sudo systemctl enable --now sentrix-experience-control.service

# 5. 单用户 API
sudo systemctl enable --now sentrix-experience-api@alice.service

# 6. 核对：各自 MainPID、构建 SHA、BGE 1024 维与 CLIP 768 维真实编码
systemctl show sentrix-experience-control.service -p MainPID -p ActiveState
systemctl show sentrix-experience-api@alice.service -p MainPID -p ActiveState
```

## 4. 灰度

1. 先仅管理员登录，核对控制面 `/control/v1/health/ready`。
2. 开放 1 名用户，执行 `experience-fault-drills.md` 的上传/恢复/隔离项。
3. 按实测资源开放 3 名，再决定是否到 5 名。
4. 每步记录整机 RAM/swap、GPU 按 PID 显存、温度、磁盘、排队时长与旧服务退化。

## 5. 回退

- 优先把 `/opt/sentrix/current` 切回上一版发行目录并重启对应单元。
- 迁移均为扩展式；回退前确认上一版能读新 schema，且不会把新作业写坏。
- 开放上传后不覆盖数据库旧备份；故障先停新入口，保留原件与作业，向前修复。
- 旧 8191、8191 的 PhotoBench 单元与数据根不随本手册移动。

# 100 Marvell网口迁移预配置

用户于2026-11-18要求先准备迁移设置，稍后决定是否切换。本配置绑定Marvell enp12s0（MAC 60:CF:84:8A:6D:73），保留192.168.0.100/24、网关192.168.0.1、DNS 8.8.8.8及114.114.114.114，路由优先级100，IPv6自动。autoconnect=false，导入只保存候选配置，不激活连接。

原Intel enp11s0活动连接UUID 561170b8-471b-4eb3-b00d-1ac30005effd及Marvell旧192.168.3.52配置保持。物理carrier=1只证明有链路；Marvell能否接入192.168.0.0/24和到达网关仍待专门验证。

## 校验与导入

文件先在本机验证commit，再传100的/home/realmagic/xy/diagnostics/marvell-migration-20261118并逐SHA核对。100自带python3-gi/libnm可校验真实配置：

```sh
/usr/bin/python3 /home/realmagic/xy/diagnostics/marvell-migration-20261118/prepare.py
```

管理员仅导入不启用：

```sh
sudo /usr/bin/python3 /home/realmagic/xy/diagnostics/marvell-migration-20261118/prepare.py --install
```

导入后检查：

```sh
nmcli -f connection.id,connection.interface-name,connection.autoconnect,ipv4.addresses,ipv4.gateway,ipv4.dns connection show marvell-static-192.168.0.100-staged
```

prepare.py --install需要root，目标若已存在则停止供核对；只安装并load这一份禁用自动连接的候选配置，不调用up/down、设备重连或NetworkManager重启。普通校验不写系统配置。

## 后续实际切换

本轮只准备配置，尚未验证第二口与当前网关同网。实际切换前用不冲突的临时地址、指定接口验证Marvell到当前网关和外网；确认对应测评/外部请求已安排切换窗口，处理固定Intel接口的root watchdog。通过后再迁移原IP并核路由/ARP/SSH/服务，避免双口同时使用192.168.0.100。原配置可留作回切依据。实际切换及开机主网口选择不在本轮脚本内执行。

审计与交接：产品项目audit/20261118-marvell-migration-prep/README.md，接T006。

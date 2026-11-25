# 100 网卡温度与链路取证

用户于2026-11-18授权记录网卡及其他芯片温度。此采集器每30秒读取Linux已暴露的hwmon温度、两张网卡状态/累计流量/错误计数、目标PCIe省电与错误状态，以及NVIDIA温度/利用率/功耗。只采集，不修改网络、驱动、电源管理或测评服务。

Intel enp11s0目前未暴露温度接口；记录为`temperature_c=null`和`temperature_status=no_exposed_sensor`。Aquantia enp12s0有PHY/MAC温度，不能代替Intel网卡或主板芯片组温度。未出现的温度、不可读取的PCIe字段保留为空。网卡累计计数可按相邻样本时间差计算吞吐和包速，重启用boot_id区分。

部署文件必须先本机验证和commit，再传100并逐SHA验收，最后push。远端目录`/home/realmagic/xy/diagnostics/nic-thermal`，用户单元`sentrix-nic-thermal-recorder.service`，日志`logs/samples.jsonl`。每份32MiB、保留3份轮转文件，总上限约128MiB；当前用户已启用Linger，采集不依赖SSH保持。

检查：`systemctl --user status sentrix-nic-thermal-recorder.service`；停止：`systemctl --user disable --now sentrix-nic-thermal-recorder.service`。重启此单元只重启采集器。

验证与运行回执、同型故障研究：产品项目`audit/20261118-nic-thermal-recording/README.md`，接T006。

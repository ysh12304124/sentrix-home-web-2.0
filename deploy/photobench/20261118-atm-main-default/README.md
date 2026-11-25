# ATM-Bench 默认全集1013题

正式入口继续叫`atm-bench`，使用相册`2026.11.17-atm-sources-addresses`，主集1013题，Hard31题独立保留。3759图、533视频和6742邮件沿用原完整材料包。

博物馆题`500f1059-8d20-4bd7-be43-32ef39bee511`首项答案ID从`20230904_144627`改为`20230905_144627`；已核六图实物内容、EXIF时间、GPS及官方地址。题干及六证据ID保持，其他1012题对象保持。官方原对象留于source_qa，评分目标和指纹同步修正。

## 已发布与验证

先本机数据审计提交6476d88，再同步100共同题包`/var/tmp/sentrix-atm-sources-20261117/2026.11.17-atm-sources-addresses`。8772/8872/8874均返回完整1013题、预检0错误，六图逐端共18次Range GET通过；其他题集SHA保持。无需重启。原文件备份于包内audit/20261118-main-default-before.zip。

`replace.py`和`payload.zip`已执行发布，禁止重放。`verify.py after`可只读复核，但依赖服务器部署目录的before.json。服务器部署目录`/home/realmagic/xy/deploy/20261118-atm-main-default`；完整发布和验收回执位于工作区`audit/20261118-atm-museum-qa-correction/replacement/`。

载荷SHA256：`cf713879fbce61c7d8edbdd7d340ce2b0209c26433563b70f6a7220a18ba70b7`。
主集SHA256：`13d07b33d8e9c522347bf485539a9a71d8ce569385d1ecf26620218b7e3383c3`。

该目录保存已验收发布资产；2026-11-18后续完整测评执行要求见HANDOFF/T006-mainline-shared-media.md顶部。

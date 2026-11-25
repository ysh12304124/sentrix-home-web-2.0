# 100推理上下文128K发布

产品五份源码：config、assistant ports、assistant_wiring、container、chat_client。
8291/8294与本机源码一致；8191叠加本目录8191.patch，保留普通16K合同及诊断。
8191/8294的现役云端代理未自报窗口，新增SENTRIX_VLM_REASONING_CONTEXT_TOKENS=131072声明；
8291本地端点沿用容量探测，精确计数保持部署合同。普通模式、输出上限和模型thinking参数保留。

仅空闲时重启三产品和三测评以刷新产品版本，测评代码与静态产物保持。
更新三个产品build-info及三份当前runtime_connection的产品版本；旧run与聊天快照保持。
install.py是本轮单次部署脚本，执行后检查intent/acceptance，不得重放。
本机验证并commit后生成payload、同步服务器，验收通过后push。
审计入口：产品项目audit/20261118-reasoning-context-128k/README.md。

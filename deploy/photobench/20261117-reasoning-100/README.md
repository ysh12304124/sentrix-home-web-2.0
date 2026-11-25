# 100 推理模式配套发布

源功能133434f及前端66a0883；同时更新100三产品8191/8291/8294及三测评8772/8872/8874。
基线b2030e1加20261117-psh-100既有运行补丁，以及a730335聊天修复。
prepare.py仅本机重建组合，现有8191/8291/8772补丁已在当前源码重新应用并通过377项专项。
产品各8个Python文件、测评共10个Python文件及前端构建更新；保留16K上下文、十条旧pending隔离、模型鉴权及切换适配。
更新产品build-info及三份当前runtime_connection中的产品版本；历史run/聊天快照和模型配置保留。
测评版本由99zz-reasoning-revision.conf最后追加的EnvironmentFile声明，保留旧99/99z覆盖顺序。

先本机验证与commit，再核对preflight.py只读清单和空闲；install.py为本轮一次性操作，不得重放。
发布记录与回执位于产品项目audit/20261117-reasoning-100-release/。

# 100 三实例交互问答发布覆盖层

本机 feature/photobench-evaluation 为功能源。三个 patch 基于2026-11-17只读取得的现役源码，保留各实例已有模型管理、持久化、报表和轨迹改动。patch仅供核对与重建本轮发布组合，已应用实例禁止重复应用。

8772源为062ebab加100-local修复；8872/8874源为a7bb484加既有报表/轨迹覆盖层。manifest逐文件记录发布前/后SHA256。发布包从本机已提交patch生成，包含相应源码及本机构建前端；配置、产品代码、题库与结果不在覆盖范围。新功能主线实现位于apps/photobench，实例适配保留原有pipeline/reuse行为。

本地三组合均通过14项交互后端测试、4项页面交互测试、TypeScript与构建。部署/真实页面/结果保持回执见工作区audit/20261117-interactive-evaluation。先本地commit，再同步和重启验收，验收后push。

通过服务专属PHOTOBENCH_BUILD_REVISION记录测评版本，产品build-info保留。

8872/8874使用旧版RunRequest，单题重测不传新版graph_enabled字段；8772保留图记忆选项。三组合测试从独立源码路径运行，确认实际导入各实例组合；14项分别通过。

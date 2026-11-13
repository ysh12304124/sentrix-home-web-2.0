# PhotoBench Mac 开发入口预算修复

目标运行：20261109-175342-album3-max-video10-current-8f4778。

## 证据

- run.json：17:54:01 开始 pipeline_processing，19:54:04 ReadTimeout，
  request_outcome_unknown，POST 未重放；execution_budgets.import_timeout=7200。
- runtime_snapshot.product_settings.deployment_profile=server；gamma_sync 确认产品 API
  被重启为 server。当前监听命令为普通 uvicorn 开发入口，非 Mac 启动脚本。
- 最后进度 207 processed、137 pending、0 failed；334 图片、10 视频，QA 完成 0/487。
- 只读 SQLite 核验：本次 344 个原始媒体后来均 processed；scope 总计 377 图片、10 视频，
  包含身份参考图和派生关键帧。这证明后台继续执行，不证明 PhotoBench 已获得完整收尾结果。
- photobench.log 的旧 local-*-evaluation 缺 run.json 和 sentrix.log 中 11 月 6 日的异常
  不能作为本次根因。本次空间已正确显式登记，身份与导入阶段成功。

## 修复与边界

仅改 Makefile 两个开发目标的环境默认值：Darwin=mac，其它=server，显式值优先。
保留 Settings 默认 server、HTTP POST 不重放和历史快照语义。未修改数据、模型、评分、API契约。
通过实际 make 子进程测试传入 Settings 的 profile、导入预算与 QA 并发。

原失败记录保留，不回填成功、不伪造耗时。不能直接续跑来验证新预算：续跑继承旧预算。
新建 full/build 才使用新默认；未再次启动耗时整册导入和 487 题付费 Judge 评测。

## 验证

部署 profile 与 suites 测试共 66 项通过。当前 API/PhotoBench 平稳重启后验证 Mac 配置。
全仓 make ci 的既有 lint 问题另行记录，不扩大本次修复范围。

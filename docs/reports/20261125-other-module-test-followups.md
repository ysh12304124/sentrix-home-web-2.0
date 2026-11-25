# 测试发现的其他待修改部分

yxw 已包含主线及视频处理改进。本说明供其他成员验证通用检索、规划、证据绑定、地点解释、引用交付及工程门禁。视频处理的已实现流程和部署要求见 [视频功能修改说明](20261125-video-function-changes.md)。

本轮观察来自同一组 10 个视频和 10 道原始英文题。修复前运行编号为 `20261125-113942-atmbench-video-memory-mac-10-qwen35-4b-mlx-q4-de2a66`，修复后为 `20261125-121613-atmbench-video-memory-mac-10-qwen35-4b-mlx-q4-e98413`。两轮使用相同本机模型与评分模型，`reasoning=false`、`graph_enabled=false`、QA 并发为 1。

## 已修复并需要跨模块回归的行为

| 范围 | 本次改动 | 其他成员需要检查 |
|---|---|---|
| 媒体时间 | 解析重复容器时间并接入 GPS 时区恢复 | 图片、视频、音频的时间排序、跨日查询、夏令时和旧数据读取 |
| 人物筛选 | 泛称用户、人群不进入具体身份过滤 | 真姓名、别名、同名歧义、多人合照，以及未知具体人名仍有明确反馈 |
| 检索首屏 | 恢复结果集编号、候选数量、分页与可精读状态 | 首屏和后续页、图片及文本混合来源、结果集范围校验 |
| 证据预览 | 帧预览携带源视频时间，来源地址可参与地点展示 | 普通图片、文档预览、原视频回看和跨相册权限 |
| 回答与精读 | 加强时间、地点、数量的依据要求 | 日期与金额题、OCR、对象计数、资料引用、无依据时的不确定性表达 |
| PhotoBench 进程控制 | 支持外部托管产品进程 | 外部托管连接验证、原有子进程模式和模型端点不匹配时的反馈 |

这些变化影响共用导入和助手链路，需要使用各模块正常入口验证。旧记忆中的缺失时间和地点需要通过项目已有重建流程更新，源码升级后的新导入会读取修复后的逻辑。

## 测试已发现的待修改问题

| 优先级 | 现象与证据 | 建议修改入口 | 验收条件 |
|---|---|---|---|
| P1 | 正确视频进入候选达到 10/10，最终引用命中仍为 4/10 | `application/assistant/tools.py`、`run_turn.py`、结果集和交付策略 | 对实际用于回答的来源逐项引用，帧映射回父视频，评测能区分候选召回与最终引用 |
| P1 | 10 个回合都保留 `partial`，时间题已有正确值仍缺少事件范围的证据绑定 | `domain/assistant/evidence_binding.py`、`evidence_ledger.py`、`intent.py`、`application/assistant/run_turn.py` | 明确事件与地点限定的核验依据，真实缺口保留，满足条件的证据可正确闭合 |
| P1 | 最近城市被解释成精确地标，机场题把附近居民点延伸为机场名 | `application/assistant/tools.py`、`domain/assistant/evidence_binding.py`、地点事实模型 | 依据来源与精度回答，邻近居民点、行政归属、来源地址和 POI 名称可分别追溯 |
| P1 | 图片文字或建筑风格会引出缺少依据的城市和地标推断 | 精读、回答核验、地点证据投影 | 文字原文与地点归属分别核验，缺少精确名称时如实表达覆盖范围 |
| P2 | 规划器有时增加与最终问题无关的统计或布尔需求，加重未闭合状态 | `domain/assistant/intent.py`、`application/assistant/goal_planner.py` | 只声明当前问题所需证据，对相同问题的需求集合可解释且有工具可满足 |
| P2 | 中英文关键词排序有差异，尚缺完整语言对照结论 | 检索通道、查询规划与 PhotoBench 测试配置 | 同一记忆空间、模型和预算比较原始英文与经核对的中文题，保留各自轨迹和评分 |

P1 项直接影响回答依据或交付，P2 项影响规划稳定性和诊断。上述入口供定位使用，具体改动应遵守仓库分层、端口与外部契约约定。

## 视频关联的后续工作

| 事项 | 当前事实 | 后续验收 |
|---|---|---|
| 全片描述 | 时间排序后拼接分组描述与转写，短描述取首个有效组 | 如增加全片生成式汇总，应保留分组和片段依据，验证跨组动作与描述覆盖 |
| 新字段查看 | 分组、音轨和片段完整内容保存在 SQLite JSON | 在现有项目界面查看依据，核对原视频跳转和权限 |
| 推理来源记录 | 分组保存模型名，完整模型原始响应尚未统一归档 | 记录构建配置、版本与调用来源，重跑复用条件可审计 |
| 音轨质量 | 延迟样片验证通过，ATM 10 个原视频均无音轨 | 增加真实多人交谈、重叠说话、静音、ASR 失败与恢复场景 |

## 工程门禁状态

本轮 324 项专项测试通过，包含导入时间、工具箱、规划、精读、视频仓储、持久任务、视频 API、证据绑定及助手路由。文档与 OpenAPI 校验通过，11 项分层契约通过。

主线基线的全量 `make ci` 仍有既存失败，应另行处理。已记录的首轮阻断包括 `apps/photobench/tests/test_suites.py` 的导入排序和超长行，方法可达性检查还报告 `TurnRepository.list_for_conversation` 缺少生产调用。类型检查在主线基线记录 85 个错误，本轮修复版记录 84 个错误，按文件及消息对照未发现新增错误。发布验证应以实际检查日志为准。

```bash
make ci
make check-docs
make arch
make typecheck
```

324 项测试的分组复现命令如下。使用满足仓库依赖的 Python 环境，并将两个源码目录加入 `PYTHONPATH`。

```bash
export PYTHONPATH="$PWD/apps/api/src:$PWD/apps/photobench/src"

python -m pytest -q -o addopts='' \
  apps/api/tests/unit/test_assistant_intent.py \
  apps/api/tests/unit/test_photo_inspector.py \
  apps/api/tests/integration/test_assistant_toolbox.py \
  apps/api/tests/integration/test_video_metadata.py \
  apps/api/tests/integration/test_video_scenes.py \
  apps/api/tests/integration/test_video_keyframes.py

python -m pytest -q -o addopts='' \
  apps/api/tests/integration/test_capture_time_contract.py \
  apps/api/tests/integration/test_capture_time_rebuild_plan.py \
  apps/api/tests/unit/test_evidence_binding.py \
  apps/api/tests/unit/test_container_wiring.py \
  apps/api/tests/integration/test_run_turn.py \
  apps/api/tests/contract/test_assistant_routes.py \
  apps/api/tests/contract/test_search_routes.py

python -m pytest -q -o addopts='' \
  apps/api/tests/integration/test_video_repository.py \
  apps/api/tests/integration/test_video_job_persistence.py \
  apps/api/tests/contract/test_video_routes.py
```

三组对应 151、146、27 项。发布整理后的工作树再次执行第一组，151 项通过，并核对功能源码与实测版本一致。真实模型评测需另外运行，专项测试通过与问答满分是不同验收项。

## 非视频功能验证建议

由维护者选用自己的测试资料建立独立相册或评测空间，经现有 UI 或 HTTP 入口验证以下场景，并保存提交版本、模型、数据范围、任务编号与实际结果。

1. 普通图片上传、去重、预览、时间排序、搜索和删除。
2. 具体人物姓名与别名、多人合照、未知人名、身份与家庭关系。
3. 邮件与文档导入、正文读取、段落引用、混合来源检索。
4. 普通文字问答、已有对话续接、翻页、精读与引用交付。
5. 无元数据、无结果、模型不可用、任务中断与恢复。
6. 跨相册查询边界、来源读取权限及原文件访问。

本轮实际验证范围为列出的专项测试和视频小样本。非视频功能的独立真实模型验收结果需要各模块维护者补充到对应报告。

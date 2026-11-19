# 本机后端语音与 HTTPS：主干集成记录

基线为 `main@652a4c9b`（包含 #33 视频/部署、#34 助手缓存、#36 图评分及记忆修正）。
分支 `feat/assistant-sensevoice-backend-pr` 仅移植 `d1ae72f7`、`43adcd0d`、`60caebfe`
三个语音提交，不包含原分支前置的七个人物功能提交，也不包含原工作区未提交的人物后台任务。
当前行为以 [语音运行手册](../runbooks/assistant-speech.md) 为准；11-13 开发记录中的
浏览器 WASM 测试属于被替换方案的历史记录。

## 冲突与契约

- container 同时保留 `OpenAIVisualEvidenceUtility` 与 `SenseVoiceRecognizer`，不覆盖主干装配。
- 模块地图以主干为基线，增加语音端点、适配器及对应双向依赖。
- 能力地图保留主干图检索、删除与记忆修正记录，只增加最终后端语音能力。
- OpenAPI 重新从整合后的源码导出；除两个语音端点及其 schema 外，还补齐主干已有但
  尚未导出的六个响应 schema 的 `sync_status/sync_error`（48 行）。这不是新加的人物行为。
  与同一 main 重新导出的契约比较：所有原路径与原 schema 相同，仅新增语音路径/schema。
- 原有人物前端/后端源码与 main 相同。未修改模型回答、检索或媒体导入逻辑。

## 验证（2026-11-16）

在独立工作树执行，并用同一 main 的隔离快照对照失败，不依赖原目录的未提交改动。
共享现有依赖环境，但 pytest 按主干的 pythonpath 配置加载当前工作树；架构检查显式设置
`PYTHONPATH=apps/api/src:apps/photobench/src`，避免 editable 安装指向旧分支。

| 检查 | 结果 |
|---|---|
| 后端语音适配器、准备脚本、HTTP 契约 | 17 通过 |
| 真实模型专项门禁 | 1 通过、零 skip；启动构造一次，两次 HTTP 识别一致，退出释放 |
| 前端语音 + useTurnStream 新增回归 | 27 通过（23 + 4） |
| TypeScript --force / Vite 生产构建 | 通过 |
| 新增后端适配器、路由、准备脚本 strict mypy | 通过 |
| 分层、模块可达性、方法可达性 | 11 个契约全部保持、两项可达性零问题 |
| capability-map / OpenAPI --check | 通过 |
| Vite HTTPS | 隔离端口 4275 启动成功；curl 使用 mkcert 根 CA 严格校验，/assistant 返回 200 |
| make ci | 在 lint 阶段失败：6 项，与 main 基线一致 |
| module-map | 4 项主干既有缺项，均涉及 request_context；无新增语音登记问题 |
| 扩展后端助手回归 | 165 通过、8 失败；main 同组 165 通过、相同 8 项失败 |
| 前端助手目录 | 31 通过、2 失败；main 原有 4 通过、相同 2 项失败 |

新增 useTurnStream 回归验证：建回合接受后返回 true 并继续 SSE；创建失败返回 false 不重试；
创建期间取消在取得回合 ID 后补发取消；断线只重连同一回合并续传游标，取消后停止重连。
既有语音回归覆盖手动停止自动发送、60 秒只留草稿、重复提交互斥、失败保留文字、
会话切换丢弃晚到转写、权限拒绝和录音资源释放。

后端 8 项既有失败均位于 test_assistant_routes.py：详情消息、候选结果、无候选结果、
turn 取消状态、阻塞调用取消、worker 启动失败、包含 debug、无 debug；与 main 的失败节点逐项相同。
前端既有失败为 AssistantPanel 历史照片断言与 TurnTimeline 折叠按钮断言。

没有宣称全仓 CI 全绿，没有执行全部覆盖率/完整 487qa，未重新进行真人麦克风或实体手机验收。
HTTPS 页面访问和模拟/样例音频验证不能替代实体手机安装根 CA 与麦克风权限验收；因此先创建 Draft PR。

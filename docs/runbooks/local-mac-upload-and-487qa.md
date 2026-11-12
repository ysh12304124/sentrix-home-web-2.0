# 本机上传/记忆对话与 487QA

此工作副本提供两个互不覆盖数据的入口：

- **Sentrix 主相册**：`http://127.0.0.1:8191`，上传视频后等导入完成，再到「提问」页与记忆助手对话。
- **PhotoBench**：`http://127.0.0.1:8772`，数据集 `album3-max-video10`，QA 集 `mixed-image-video-487q`（487 题）。

## 数据与模型

本机 487 数据集在 `/Users/rm011/Public/lan-sync-3eHEHj/source/data`，含 334 张照片、10 个视频和 SHA256 为
`73fa4512dde072fb0df9cf45ff3ebd192192db606f9764054bde002dcbf0be47` 的 487 题集。运行数据与 PhotoBench 结果分别写入本工作副本的 `data/`，不会写回只读基准素材。

本机模型拓扑：Qwen VLM `18100/v1`、BGE-M3 `8101`、Chinese-CLIP `8103`。启动脚本只连接这些模型，不负责关闭或重启模型。

## 启动

```bash
cd /Users/rm011/keyfram0/sentrix-home-web-2.0-dynamic
./scripts/deploy/start_local_two_workflows_mac.sh
```

首次使用需在仓库根有 `.venv`（可 `make install`）；PhotoBench 页面产物需先构建：

```bash
cd apps/photobench/web
npm ci
npm run build
```

脚本启动前会检查 VLM、文本/视觉嵌入、主 API 健康、上传执行器体验模式，以及 PhotoBench 数据集目录。若用户已在使用某一服务且它健康，脚本复用现有进程；若端口被其他不健康进程占用，会停止并报告，不强杀未知进程。

## 手动上传与 AI 记忆对话

1. 打开 `http://127.0.0.1:8191`，进入「导入」，选择本地视频上传。
2. 等视频任务到「已完成」后，在「提问」页问视频内容或相册记忆。不要在导入尚未完成时据此判断 AI 未记住视频。
3. 如果上传后任务停在 `accepted`，先看 `GET /api/v1/internal/status` 是否显示 `jobs_active`，以及 `GET /api/v1/video-jobs` 的阶段和错误。

## 487QA

1. 打开 `http://127.0.0.1:8772`，确认数据集 `album3-max-video10` 与 QA `mixed-image-video-487q` 显示 487 题。
2. 建议第一次用新 scope 的完整流程：导入此基准相册后运行 487QA；若目标是只评问答，则复用已经完整导入的相册 scope。
3. PhotoBench 会冻结数据集/QA/模型端点到 run 快照。不要把主相册刚上传的视频当作 487 数据集，也不要把不同数据集的旧 run 混作新结果。

全量 487 题会持续较长时间并占用本机模型；启动正式 run 前先确认当前没有需要交互的记忆对话。当前部署只把数据集与服务准备好，不会自动开始这轮长评测。Judge 未设置时可先验证 QA 执行，评分状态会如实显示未完成；需要分数时再在 PhotoBench「配置」里选择并检查 Judge 服务。

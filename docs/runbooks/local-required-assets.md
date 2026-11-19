# 本地运行所需资源

模型和数据不进版本控制。2026-11-18 本机已接入用户提供的模型与数据，
当前启动入口与实际验收范围见 [Windows Conda 环境](windows-conda.md)。

| 用途 | 所需文件或配置 | 接入位置 |
|---|---|---|
| 主模型/VLM | README 基线 Qwen3.5 的 qwen35-q4km.gguf 与匹配的 qwen35-mmproj-f16.gguf；llama-server 可执行文件 | OpenAI 兼容服务 8100，SENTRIX_VLM_BASE_URL |
| 默认 wemm 检索 | WeMM-Embedding-2B 完整模型目录（权重、config、tokenizer/processor）；对应侧车运行环境 | 8102，SENTRIX_WEMM_URL |
| 可选 legacy 检索 | chinese-clip ViT-L-14 权重 clip_cn_vit-l-14.pt，以及完整 BGE-M3 模型目录 | 视觉侧车 8103、文本侧车 8101；须整套切换并重建向量 |
| 人脸识别与检测 | InsightFace buffalo_l 的 ONNX 权重目录、retinaface_r50.onnx | FACE_MODEL_ROOT 下 models/buffalo_l；RETINAFACE_MODEL_PATH |
| 地点补充 | GeoNames CN.zip、cities15000.zip，保留许可署名 | SENTRIX_DATA_DIR/geo，详见 geonames-data.md |
| 主相册 | 用户自己的图片、视频及可写数据根 | SENTRIX_DATA_DIR；数据库和索引由应用初始化 |
| PhotoBench | 对应相册的 manifest、QA、媒体与参考图，完整目录关系保持一致 | PHOTOBENCH_DATA_ROOT |
| PhotoBench 连接 | runtime_connection.json：本机主 API、模型与嵌入拓扑 | PHOTOBENCH_CONFIG_ROOT；迁移时不能保留旧服务器地址 |
| Judge 评分 | judge_providers.json 与其中引用的密钥环境变量 | 本地配置根，密钥不入 Git；仅运行评分时需要 |
| 媒体工具 | 系统策略允许执行的 FFmpeg、ffprobe | 激活后的 PATH 中可执行 |
| 前端 | 两个 dist 目录 | 已通过现有 npm 锁文件安装与生产构建 |

默认 wemm 与 legacy 二选一；仅需主产品时无需 PhotoBench 数据和 Judge 密钥。
模型目录接入前须核对模型版本、维度和处理器，不能仅靠文件名判断兼容。
人脸/GeoNames 缺失的降级须通过健康状态和实际功能验收确认；真实模型、真实数据、
视频和人脸专项不能用日常假件测试替代。当前已验本机人脸 CPU 推理、视频抽帧、
GeoNames 最近邻和独立一题 QA 链路，尚未完成 487 题全量及全部真实环境专项。

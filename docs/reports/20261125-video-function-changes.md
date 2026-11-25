# 视频功能修改说明

本次将基于主线的 Mac 视频处理改进交付到 yxw，供视频维护与其他模块验证共同使用。视频按 SVD 镜头分析和 Katna 实际选择结果生成关键帧与分组，画面分组和音轨转写共同归属源视频。100 帧、每组 4 帧为讨论中的示例，实际帧数、分组数和组大小由处理结果决定。

主线基线为 `8c209ebc980f04156629d82d69396ced7f12b4bd`，交付功能提交为 `839bc8bc`。此前 Mac 实测的功能版本为 `ee2e95fa`，交付提交汇总了同一组功能源码。视频和 PhotoBench 界面继续使用仓库现有前端。

## 处理流程

1. 导入源视频，读取时长、编码、拍摄时间、GPS 与音轨信息，登记原始资产。
2. 顺序解码并按原有 SVD、相似度去重和镜头变化规则规划候选，由 Katna 选择关键帧。首末帧走各自的单帧组。
3. 保存帧资产、原片时间及选帧组号，按时间顺序将每组实际保留的帧联合输入视觉模型。
4. 保存分组描述、画面文字、标签、实体和依据帧。成功分组按帧编号、内容哈希和时间签名复用。
5. 有音轨时提取临时单声道 16 kHz WAV，调用配置的 ASR。缺少原生说话人标签时，可使用本地 Sherpa-ONNX 说话人模型估计标签。
6. 按原视频时间合并画面与转写依据，保存源视频结构化记录，并刷新既有关键词和文本向量索引。

全片 `caption` 当前由带时间的分组描述和转写拼接，`short_caption` 取首个有效分组。逐帧观察、事件和向量沿用既有处理链，分组语义落在该组代表帧上。全片二次生成式摘要、完整模型原始响应归档、全部新增字段的前端展示列入后续工作。

## 编号与存储

| 记录 | 编号和字段 | 保存位置 |
|---|---|---|
| 源视频 | `asset_id`、原文件名、拍摄时间、GPS | `assets` 与受管原文件 |
| 关键帧 | `frame_id`、`video_id`、`frame_path`、`timestamp_sec` | 派生 `assets` 及分组 JSON |
| 分组 | `<video_id>:group:<序号>`、`selection_group`、有序 `frame_ids`、采样首末秒数 | `video_memory_records` 分组行 |
| 视频记忆 | `record_id = video_id`，包含分组、音轨、转写、汇总字段和 `evidence_refs` | `video_memory_records` 视频行 |
| 音轨 | `<video_id>:audio:<stream_index>`、`source_start_sec` | 视频行的 `audio_track` |
| 转写片段 | `<audio_id>:segment:<序号>`、文字、说话人、音轨起止秒数、原片起止秒数 | 视频行的 `segments` |

迁移 `0037_video_memory_records.sql` 新增 `(video_id, record_id, payload)` 表，`video_id` 外键关联 `assets`，删除源资产时级联删除记录。音轨和转写片段嵌套在视频 JSON 内。`audio_path` 指向原视频，结合 `stream_index` 定位音轨。转写期间生成的 WAV 在处理结束后清理。

```text
$SENTRIX_DATA_DIR/
├── sentrix.db
├── media/
│   ├── <asset_id>_<原文件名>.mp4
│   └── derived/
│       ├── svd_katna/<video_id>/analysis.json
│       └── video/<video_id>/scenes/<帧槽位>/kf_0001.jpg
├── video-jobs/<job_id>/
│   ├── scene_windows.json
│   └── speech.json
└── models/speaker-diarization/
    ├── segmentation.onnx
    └── embedding.onnx
```

`analysis.json` 保存采样、镜头变化、Katna 预算和最终时间及分组号。`scenes` 目录沿用逐帧槽位命名，视觉分组通过数据库中的有序帧列表关联。`video-jobs` 属于持久上传任务检查点，整册处理入口的任务产物有所不同。

片段原片时间满足以下关系。

```text
video_start_sec = start_sec + source_start_sec
video_end_sec   = end_sec   + source_start_sec
```

说话人标签表示同片的声音分组。无法确定时保存空值，多人粗粒度片段缺少明显主导说话人时也保留空值。

## 主要代码入口

| 路径 | 职责 |
|---|---|
| `apps/api/src/sentrix/application/media/build_video_memory.py` | 分组观察、转写关联、视频记录汇总与检查点复用 |
| `apps/api/src/sentrix/application/media/extract_video_scenes.py` | 选帧、派生资产、分组记忆与事件编排 |
| `apps/api/src/sentrix/infrastructure/media/svd_katna_scene_planner.py` | 选帧组号和解码时间保留 |
| `apps/api/src/sentrix/infrastructure/media/vlm_observer.py` | 多帧联合视觉理解 |
| `apps/api/src/sentrix/infrastructure/media/audio_transcription.py` | 分块 ASR、片段时间与说话人标签 |
| `apps/api/src/sentrix/infrastructure/persistence/repositories/video.py` | 视频记录与分组 JSON 读写 |
| `apps/api/src/sentrix/bootstrap/import_wiring.py` | 统一装配视频与原有导入流程 |

## 相关通用修复

重复时间标签仅在各值表示同一时刻时合并，冲突值保持未知。导入装配接入已有 GPS 时区解析器。帧采样使用实际解码时间，JPEG 输出显式设置色彩范围并使用微秒精度 seek。

规划中的泛称人群保留为内容语义，具体姓名继续经过人物目录过滤。检索首屏恢复结果集与分页控制信息，帧预览附原视频拍摄时间及原片秒数。回答和精读规则加强日期、地点来源与对象计数核验。

## 部署与兼容

数据库迁移由现有启动迁移流程执行。部署前一致性备份数据库及对应媒体、索引，回滚时核对迁移兼容性。

- 视频转写使用已有 `SENTRIX_QWEN_ASR_BASE_URL`、`SENTRIX_QWEN_ASR_MODEL`、`SENTRIX_QWEN_ASR_API_KEY` 配置。
- 按需在上述模型目录提供 Sherpa-ONNX 支持的分段与说话人嵌入模型。
- 拍摄地时区解析需要项目已声明的 `tzfpy[tzdata]` 依赖。离线地点文件按 `docs/runbooks/geonames-data.md` 放到各实例的 `geo` 目录。
- launchd 或其他外部服务管理器托管 API 时，可设 `PHOTOBENCH_SENTRIX_EXTERNAL_PROCESS=1`，由 PhotoBench 验证产品连接，进程生命周期由外部管理器负责。
- 本地配置、密钥、模型、原始媒体、数据库、构建产物及完整评测轨迹由部署环境保存。提交包含源码、测试、迁移和说明。

## 实测结果

2026 年 11 月 25 日在 16 GB Apple Silicon Mac 上，以原版 PhotoBench 完成 ATM-Bench 10 个原视频的 full 构建和 10 道原始英文题评分。运行编号为 `20261125-121613-atmbench-video-memory-mac-10-qwen35-4b-mlx-q4-e98413`。

| 验证项 | 结果 |
|---|---|
| 视频构建 | 10/10 完成，45 帧、36 组，各组均有描述 |
| 实际组大小 | 31 个单帧组、2 个双帧组、2 个三帧组、1 个四帧组 |
| 拍摄时间和 GPS 地点记录 | 10/10 入库，跨午夜按拍摄地时区换算 |
| 音轨扩展 | 另用带延迟音轨样片验证转写、标签、子记录和时间偏移 |
| 答案均分 | 0.30/2 → 1.40/2 |
| 正确视频进入候选 | 60% → 100% |
| 最终引用视频命中 | 20% → 40% |
| 人物未解析拦截 | 4 题 → 0 题 |

本机这 10 个 ATM 原视频均无音轨，音轨结果来自单独样片。答题和评分均使用本机 `qwen35-4b-mlx-q4`，该结果属于此配置下的小样本工程测试。10 题均完成评分，仍有 2 题 0 分、2 题 1 分，全部回合的证据绑定状态仍为 `partial`。后续非视频验证范围见 [测试发现的其他待修改部分](20261125-other-module-test-followups.md)。

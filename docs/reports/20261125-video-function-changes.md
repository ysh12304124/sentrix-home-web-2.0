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

## ID 规则与父子关系

内部 `video_id` 使用源视频的 `assets.id`，内部 `frame_id` 使用派生关键帧的 `assets.id`。默认资产 ID 为 `asset_` 加 12 位随机十六进制字符，导入接口也支持由调用方提供资产 ID。图片和视频使用同一资产 ID 命名空间，通过类型和血缘字段识别来源。

| ID 或序号 | 规则 | 关联与用途 |
|---|---|---|
| `scope_id` | 相册或记忆空间编号，由调用入口指定 | 源视频、关键帧、观察和检索都带空间范围；按空间验证读取与引用 |
| 视频 `asset_id` / `video_id` | 源视频资产的内部 ID | `media_type=video`，作为分组、音轨、片段与视频记忆的共同父编号 |
| 普通图片 `asset_id` | 原始图片资产的内部 ID | `media_type=image`，`parent_asset_id` 和 `derived_kind` 为空 |
| `frame_id` | 每张派生关键帧的内部资产 ID | `media_type=image`，`parent_asset_id=video_id`，`derived_kind=video_keyframe` |
| `source_timestamp_sec` / `timestamp_sec` | 关键帧在源视频中的秒数 | 前者是资产血缘字段，后者写入分组的 `frames` 数组，用于原片定位 |
| `source_frame_index` | 从 0 开始的选中帧输出序号 | 与 `parent_asset_id` 组成唯一血缘索引，用于复用同一派生帧 |
| `source_scene_index` | 当前实现使用同一帧槽位的序号 | 关联原有场景事件与 `scenes/<序号>` 文件目录 |
| `group_id` | `<video_id>:group:<group_index>` | 唯一定位一条联合视觉理解记录 |
| `group_index` | 对实际有帧的分组按时间排列，从 1 开始 | 决定分组记录编号和显示顺序 |
| `selection_group` | SVD/Katna 选帧阶段保留的原始组号 | 保留首帧组、镜头内选择组和末帧组的来源；部分组经去重后可能没有保留帧，组号可有间隔 |
| `frame_ids` | 本组实际输入模型的有序 `frame_id` 数组 | 与本组 `frames` 数组一一对应，组大小随 Katna 结果变化 |
| 视频记忆 `id` / `record_id` | `id=video_id`，视频行 `record_id=video_id` | 保存全片分组、音轨、片段与汇总内容 |
| `audio_id` | `<video_id>:audio:<stream_index>` | 定位源视频音轨，音轨内 `video_id` 和 `memory_id` 都指向源视频 |
| `segment_id` | `<audio_id>:segment:<序号>`，从 1 开始 | 定位按时间排序的转写片段，片段同时携带 `audio_id`、`video_id` 和 `memory_id` |
| `job_id` | 持久视频任务编号，默认 `vjob_` 加 16 位随机十六进制字符 | 查询阶段进度和恢复任务，通过任务中的 `asset_id` 关联源视频 |
| PhotoBench `media_id` | 使用数据集定义的素材编号 | 本轮视频适配使用源文件名主干，并通过资产映射关联内部 `video_id` |

`source_frame_index` 表示选中结果的输出序号。原始解码帧号由选帧分析记录表达。视觉 `group_index`、算法 `selection_group` 和文件目录的帧槽位分别承担排序、选帧来源和逐帧文件定位职责。

原始文件名用于展示和数据集映射。同名文件在不同空间通过内部资产 ID 区分。`kf_0001.jpg` 会在多个帧槽位中重复出现，关键帧通过 `frame_id`、父视频与完整路径定位。

下面使用示意编号展示关系。假设某个视频实际选择了三帧，形成两个非空分组。

```text
scope_id = demo-home
video_id = asset_111111111111
├── 源视频资产
│   └── media_type=video
├── 关键帧资产
│   ├── frame_id=asset_222222222222
│   │   └── parent_asset_id=asset_111111111111
│   ├── frame_id=asset_333333333333
│   │   └── parent_asset_id=asset_111111111111
│   └── frame_id=asset_444444444444
│       └── parent_asset_id=asset_111111111111
├── 分组记录
│   ├── asset_111111111111:group:1
│   │   └── frame_ids=[asset_222222222222]
│   └── asset_111111111111:group:2
│       └── frame_ids=[asset_333333333333, asset_444444444444]
├── 音轨子记录
│   └── audio_id=asset_111111111111:audio:1
│       └── segment_id=asset_111111111111:audio:1:segment:1
└── 视频记忆
    ├── id=asset_111111111111
    ├── source_group_ids=[上述两个 group_id]
    └── source_segment_ids=[上述 segment_id]
```

同一源视频资产内，重跑会按既有帧血缘和分组签名复用产物。新空间中的独立导入会建立自己的资产和处理记录。帧集合或时间变化后应检查 `frame_ids`、分组签名与选帧分析的一致性。

## 视频与普通图片的分离方式

当前采用同一工程中的素材类型、父子血缘和处理记录分离，原有图片与视频入口共用资产表、存储适配、嵌入和检索基础设施。

| 维度 | 普通原始图片 | 原始视频 | 视频关键帧 |
|---|---|---|---|
| 资产类型 | `media_type=image` | `media_type=video` | `media_type=image` |
| 血缘 | 父资产和派生类型为空 | 顶层源资产 | `parent_asset_id=video_id`，`derived_kind=video_keyframe` |
| 主要处理 | 图片观察、OCR、人物、索引 | 视频探测、Katna 选帧、分组理解、音轨与片段关联、全片记录 | 作为视频依据，复用图片的编码、向量、人物和观察处理能力 |
| 语义保存 | 自己的观察与索引 | 源视频观察及 `video_memory_records` 视频行 | 独立帧资产和观察，分组语义写在组代表帧上 |
| 物理文件 | 受管原图片路径 | 受管原视频路径 | `media/derived/video/<video_id>/scenes/<帧槽位>/kf_0001.jpg` |
| 回看 | 打开对应原图 | 打开对应原视频 | 读取父视频并跳转 `source_timestamp_sec` |
| 素材级归属 | 原图片 ID | 原视频 ID | 由父视频血缘映射回原视频 |

按媒体类型查询图片会包含图片型关键帧。统计普通原图时还需核对 `parent_asset_id`、`derived_kind` 或使用已有原始素材统计口径。素材级评测将派生帧映射到父视频，同一视频多帧命中按视频编号去重，详细依据继续保留各帧及时间。

普通图片保持自己的素材记忆，源视频拥有自己的视频记忆，视频画面分组和音轨片段关联该源视频。共享图片处理能力允许关键帧参与向量检索和人物识别，父子关系保留其视频来源。

## 数据库与文件组织

迁移 `0037_video_memory_records.sql` 新增 `(video_id, record_id, payload)` 表，主键为 `(video_id, record_id)`，`video_id` 外键关联 `assets`。分组各自保存一行，源视频汇总另保存一行。删除源资产时关联视频记录级联删除。

| 内容 | 持久化位置 |
|---|---|
| 源视频和关键帧资产 | `assets`，包含类型、父视频与帧时间血缘 |
| 分组描述和依据 | `video_memory_records` 中 `record_id=group_id` 的行 |
| 视频整体结构化记忆 | `video_memory_records` 中 `record_id=video_id` 的行 |
| 音轨和转写片段 | 视频行 JSON 内的 `audio_track` 与 `segments` |
| 既有观察、事件与向量 | 原有观察、事件、向量及索引表 |
| 选帧过程和任务恢复 | `analysis.json`、持久任务的 `scene_windows.json` 与 `speech.json` |

`audio_path` 指向原视频，结合 `stream_index` 定位音轨。转写期间生成的 WAV 在处理结束后清理。

```text
$SENTRIX_DATA_DIR/
├── sentrix.db
├── media/
│   ├── <原图片资产ID>_<原图片名>.jpg
│   ├── <video_id>_<原视频名>.mp4
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

受管原件遵循现有存储层的内容共享规则，实际路径以资产记录的 `path` 为准。`analysis.json` 保存采样、镜头变化、Katna 预算和最终时间及分组号。`scenes` 使用逐帧槽位命名，视觉分组通过数据库中的有序帧列表关联。`video-jobs` 属于持久上传任务检查点，整册处理入口的任务产物有所不同。

片段起止时间以音轨起点计，`source_start_sec` 表示音轨起点在原视频中的位置。视频行同时保存音轨时间和换算后的原片时间。

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

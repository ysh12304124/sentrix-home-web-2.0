# S8 违背 / 偏离审计（音频与视频）

> **2026-10-30 更新：**下文记录的是 2026-10-04 的初始裁决（当时不做转写）。当前实现按后续需求改为可选 Qwen ASR：语音按原片时间戳分配到对应视频场景，写入场景来源元数据，并在事件总结与记忆摘要刷新时保留；未配置 ASR 时维持原行为。以下历史背景保留用于解释设计变更，不再代表当前实现状态。

### 当前语音对齐实现（2026-10-30）

- 配置 `SENTRIX_QWEN_ASR_BASE_URL` 后，API 通过 OpenAI-compatible `/audio/transcriptions` 请求 `verbose_json` 的 segment 时间戳；`SENTRIX_QWEN_ASR_MODEL` 指定模型，`SENTRIX_QWEN_ASR_API_KEY` 可选地发送 Bearer token，`SENTRIX_QWEN_ASR_TIMEOUT_S` 控制请求超时。
- 转写片段保留原片 `start_sec` / `end_sec`，按与场景区间重叠关系附到对应视频场景。场景摘要刷新时，文字转写保存在 `source_metadata_json`，摘要中也保留 `语音：...`，不会因摘要重建而丢失。
- 助手检索预览会把视频场景语音作为 `speech_evidence` 一并交给回答模型；视频候选携带场景语音，关键帧候选只携带与帧时间戳重叠的片段。规划器用 `spoken_content` 区分语音与图片 OCR，证据绑定也要求该语音来自视频范围。
- 摘要提示明确区分视觉观察与语音内容：语音只支持说话内容，不能据此推断画面人物、物品或动作。ASR 未配置时与旧流程一致；无音轨或 ASR 失败不会阻断视频场景处理。
- 本机端到端验证使用带语音的 `cooking_paella.mp4`：转写得到 0–29.55 秒和 29.55–38.99 秒两段，原秒数与文本均写入场景来源元数据，事件摘要刷新后仍保留语音；对“视频中说了哪些肉类”提问时，检索返回视频及关键帧，回答覆盖鸡肉、香肠、西班牙香肠和 chorizo，回合 `status=complete`、`answer_coverage=1.0`，没有调用图片 OCR。该样例有个别词识别错误，因此结果只证明链路可用，不代表 ASR 对任意视频都准确。

审计对象：`docs/refactor/2026-10-04-s8-implementation-plan.md`。
基线：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:513`
对 S8 的定义，与 1.0 的实现。

**这份文档现在的形态是「开工前的偏离登记」**（S3/S4/S5 那三份是收尾审计）。
S8 施工中若再有偏离，追加到 §3。

---

## 0. 一句话摘要

S8 的设计文档定义是「**FunASR 音频、关键帧/场景切分、派生帧可回溯原视频秒数**」。
本切片**去掉 FunASR 转写**（用户 2026-10-04 裁决），其余照搬 1.0 的机制。
另有 **1 条修 1.0 的缺陷**（血缘索引覆盖面）与 **3 条 1.0 的历史包袱不移植**。

**没有一条偏离是凭喜好的**——每条都指到 1.0 的具体行，或指到本仓库的一条既有契约。

---

## 1. 与设计文档的偏离

### 1.1 去掉 FunASR 音频转写（**唯一一条实质偏离**）

| 项 | 内容 |
|---|---|
| **出处** | 设计文档 `:513`：`S8 \| 音频与视频 \| FunASR 音频、关键帧/场景切分、派生帧可回溯原视频秒数 \| 导入视频，场景成型、能跳回原片` |
| **1.0 怎么做** | `backend/model_clients.py:1462` 的 `FunASRClient`，`paraformer-zh`，**进程内 import 进主进程**（无 sidecar、无独立端口）。`backend/pipeline.py:880-887` 的 `_audio_observation` 把转写写进 `observations.transcript`；`:350` 把它拼进文本嵌入。失败无 try/except，整条资产标 `failed` |
| **2.0 改成什么** | **不做转写。** 音频只做：入库、ffprobe 元数据（时长/码率/采样率）、播放、按元数据与文件名检索。`observations` **不加 `transcript` 列**；`compose_text` 保持七字段（1.0 是八字段含 transcript） |
| **为什么** | 用户 2026-10-04 裁决「不要 FunASR」。选型理由是**依赖与形态**：FunASR 1.4.1 在 1.0 的 venv 里，2.0 没有；而它要拖进 `torch` / `torchaudio` / `transformers` / `pytorch-lightning`（`backend/requirements.txt:10-15`），与设计 §2.1「2.0 不管理模型进程生命周期」以及 W1 刚立的**重模型一律侧车**先例都不合。走侧车则要新增一个端口与一套运维件，而这一块在 1.0 里本身很薄（无重试、无测试） |
| **代价** | 「音频里说了什么」这类问题答不了。但**验收里本来就没有这一条**——S8 的验收是「导入视频，场景成型、能跳回原片」，是视频中心的 |
| **可逆性** | **高。** 不加列、不引依赖，将来要转写是纯新增：加列 + 加适配器 + 改 `compose_text` |
| **证据强度** | 用户裁决，且与机器现状一致（153 上除 FunASR 外无任何 ASR：whisper 系无、`~/models` 无 ASR 模型、8100 的 qwen35 能力标的是 `completion`+`multimodal` 不含音频） |

---

## 2. 修 1.0 的缺陷（不是偏离，是**不把缺陷带过来**）

| # | 1.0 的缺陷 | 2.0 怎么修 |
|---|---|---|
| 1 | **唯一血缘索引只覆盖一种 `derived_kind`**：`db.py:1054-1056` 是 `WHERE derived_kind = 'video_keyframe'`，而 1.0 的**默认**算法产出的是 `video_keyframe_webp` → **默认路径没有 DB 级幂等保证**，重复导入只能靠 `cleanup_video_derivatives` 兜底 | S8a 的 T501 把索引做成**覆盖所有 `derived_kind`**。2.0 只保留一条抽帧路径（§3），所以一个覆盖全部的索引就够 |
| 2 | **视频场景摘要不刷新**：`db.py:5576` 对 `source_type='video_scene'` 跳过重建，摘要只在导入时算一次，后续补齐无法更新 | **S8b 已落地（T512）**：2.0 走 `ConfirmedPersonEvents.refresh_participants` + `EventRepository.apply_summary`，视频场景**不豁免**——确认人物之后场景摘要照常重写。由 `test_video_scenes.py::test_a_video_scene_summary_is_not_exempt_from_the_participant_refresh` 钉住 |
| 3 | **前端关键帧计数恒 0**：`src/app.js:742` 用 `derived_kind === "video_keyframe"` 统计，而默认算法产出 `video_keyframe_webp` | 2.0 前端见 §3 第 3 条 |

---

## 3. 1.0 的历史包袱，**不移植**

| # | 包袱 | 为什么不 |
|---|---|---|
| 1 | **双抽帧算法并存**：`worldmm`（legacy）与 `hybrid_webp`（现用），两套 `derived_kind`（`video_keyframe` / `video_keyframe_webp`）、两套输出（`memory.json` / `frame_map.json`）、`list_derived_assets` 要兼容两者 | 用户裁决①「先做简化闭环」。**只保留一条路径**，`derived_kind` 只有一个取值 |
| 2 | **WorldMM 的 160 帧预算与挑选逻辑**（`SENTRIX_VIDEO_MAX_KEYFRAMES`、`summary_keyframes.json`、「每场景先保最高分帧再按时间序补齐」） | 随第 1 条一起去掉 |
| 3 | **1.0 前端那套场景 UI 的统计口径**（`worldmm_scene_count` 等） | 2.0 前端整体改版待用户主导（见 F7 一节），S8 只保证**接口给得出** `source_timestamp_sec` 与场景区间 |

---

## 4. 施工中的新增偏离

### 4.1 S8b 抽到的两条新常量（**不是 1.0 的值**）

| 项 | 内容 |
|---|---|
| **场景切分** | 固定时长窗 `SCENE_WINDOW_SEC = 60.0`、每窗取中点一帧、总场景数封顶 `MAX_SCENES = 40`（超过时按比例放大窗口） |
| **为什么** | 用户 2026-10-04 裁决①「先做简化闭环」。1.0 那一套是 YOLO/Pose 预筛 + Katna 质量门 + WorldMM 聚类，没有「固定时长窗」这个概念，所以这三条**没有 1.0 的值可抄** |
| **登记位置** | `behaviour-baseline.md` 的「视频抽帧与场景（S8b）」段，逐条写明「不是 1.0 的值」 |
| **可逆性** | 高。换算法只需换 `plan_scenes` 与抽帧适配器；血缘列、场景事件、派生帧走图片流水线三件都不动（裁决①的原文） |

### 4.2 场景抽取失败只留日志，没有落到资产上

`ImportAssets._extract_video_scenes` 接住抽帧链的异常后只 `log.warning`，没有像
ffprobe 失败（`video_probe_error`）或转码失败（`browser_preview_error`）那样把原因写进
`assets.metadata_json`。理由：那两条各有 1.0 的键名可抄，而场景抽取是 2.0 的新链路，
开一个 `video_scene_error` 键属于本切片之外的设计（键名、读侧、响应形状三样都要一起定）。
代价是明确的：**抽帧失败在接口上不可见**，只有服务端日志里有。补法是给
`VideoRepository` 加一对键的读写，挂在 `GET /videos/{id}` 上。

### 4.3 T514 的清理级联与 1.0 的一处不同

1.0 的 `cleanup_asset_derivatives` 用 `metadata_json LIKE '%"asset_id": "..."%'` 找向量；
2.0 按 `source_id` 删（`asset` / `observation` / `event` / `face_instance` 四个方向）。
理由：`source_id` 是 2.0 的权威键（`memory_vectors` 的唯一约束就是它），LIKE 会误伤
id 互为后缀的行，而那种误删不报错。

### 4.4 施工中**没有**发生其他偏离

除上面三条，S8b 完全按计划书 T511–T514 落地：抽帧落 infrastructure、场景事件带
`source_*` 血缘、派生帧是 `media_type='image'` 的普通资产走现有图片流水线、
`reprocess` 真重跑、`video_scene` 的摘要刷新不豁免。

---

## 4. 施工中的新增偏离（S8a · T501–T506，2026-10-04）

S8a 交付「元数据 + 血缘列能用 + 能播 + 路由通」，不建场景、不抽帧（那是 S8b）。
下面每条都指到具体的一处代码或一件 2.0 没有的东西，**没有一条是凭喜好**。

### 4.1 `VideoMetadata` 不带 1.0 的 `raw`

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `backend/video/contracts.py` 的 `VideoMetadata` 有一个 `raw: dict` 字段，装的是整包 ffprobe JSON（`metadata.py` 的 `raw=payload`）；`processor.py` 把 `metadata.as_dict()`（含 `raw`）整份写进 `assets.metadata_json.video_metadata` |
| **2.0 改成什么** | `domain/media/models.py` 的 `VideoMetadata` 保留其余 12 个字段，**去掉 `raw`** |
| **为什么** | `raw` 在 1.0 **没有任何读侧**（全仓 grep 只命中写入处与 dataclass 定义），而它把每条流的完整标签树都带进库里——一个 4K 视频的 ffprobe payload 是几十 KB，每次导入写一份没人读。2.0 的 `metadata_json` 只放真正要被读的东西 |
| **可逆性** | 高。要原始 payload 时加回一个字段、适配器多传一个键即可 |
| **证据强度** | 全仓 grep：`raw` 只出现在 `contracts.py` 的定义与 `metadata.py` 的赋值处 |

### 4.2 `video_scenes` 的响应只回 2.0 拿得到的那几块

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `GET /api/video-scenes/{id}` 返回 `get_event_detail`（`db.py:3273`）= `{event, observations, facts, entities, event_revisions}`，其中 `event` 被 `get_event` 顺手挂上 `keyframe_assets` / `source_video`（`db.py:2345-2367`） |
| **2.0 改成什么** | 只回 `{event, keyframe_assets, source_video}`——**六个键去掉三个**（`observations` / `facts` / `entities` / `event_revisions` 里的四个），且 `keyframe_assets` / `source_video` 放在 `event` **旁边**而不是里面 |
| **为什么** | 2.0 没有通用的事件详情读侧（`GET /events/{id}` 只回 `TimelineEvent`），而 `event_revisions` 这张表**在 2.0 根本不存在**（0005_timeline.sql 的表头注释：「2.0 本切片只保留 events 与 event_observations」）。按 1.0 的形状填四个恒空的键，正是 `read_entity_detail` 明确拒绝过的那种「恒空占位键」。摊平而不是嵌套，是为了让列表端点（`/videos/{id}/scenes`）复用同一个 `VideoScenePayload` 时不背上两个它永远为空的字段 |
| **可逆性** | 高。三个键的读路径将来落地时，加回去是纯新增 |
| **证据强度** | 迁移 0005 的表头注释 + `GET /events/{id}` 的现存响应形状 |

### 4.3 `reprocess` 在 S8a 只清场 + 重置状态

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `app.py:1472-1481`：清派生数据 → 状态置 `video-queued` → `background_tasks.add_task(process_asset, asset_id)` 真跑一遍抽取链 |
| **2.0 改成什么** | 前两步照做，**第三步没有**——`cleanup_derivatives` + `update_status(QUEUED)`，返回 `202 {accepted, asset_id, status: "video-queued"}` |
| **为什么** | 抽取链是 S8b（T511–T514）。2.0 **没有任务队列**，起一个查无此人的后台任务只会把「跑到哪了」藏起来——与 `routes/timeline.py` 的 `import_album` 选同步是同一条纪律（那里写着「2.0 没有任务队列，装作异步只会把『跑到哪了』藏起来」） |
| **代价** | S8a 里 `queued` 没有消费者，重跑之后资产停在 `queued`。**这是登记在案的已知状态，S8b 的 T511–T514 接走它**；S8a 是切片、没有前端调用方 |
| **可逆性** | 高。S8b 往 `update_status` 之后接上抽取链即可，路由不动 |
| **证据强度** | 计划书 T504 原文「可以只做「清派生数据 + 重置状态」，把真正重跑留到 S8b 接上，并在注释里写明」 |

### 4.4 三处 2.0 新加的可观测面（1.0 没有）

| 项 | 1.0 | 2.0 为什么加 |
|---|---|---|
| `metadata_json.video_probe_error` | 没有。ffprobe 失败 → 整条资产标 `failed`（`processor.py` 的 `except` 把 stage 写成 `video-failed`） | 2.0 选择「导入照收、原因留在资产上」（与 EXIF 读不出来不阻断同一条既有口径），那就得有地方放原因；没有它，失败又变成不可见 |
| `ImportResponse.video_probed` | 没有。接口上看不出视频有没有被处理过 | 正是 S8a 要修的失败模式：「视频收下了但什么都没发生」在响应里完全不可见。与 `indexed` / `placed` 同一个位置 |
| `VideoPayload.metadata` | 1.0 把整行 asset（含整个 `metadata_json`）交出去 | 2.0 只列出视频真正有的那几位 + 探测结果，形状固定、不随 metadata_json 里塞了什么都变 |

### 4.5 预览转码的时机与落点

| 项 | 1.0 | 2.0 |
|---|---|---|
| 时机 | 后台 worker（`process_asset`）里转 | **导入请求内同步转**（`ImportAssets._probe_video`） |
| 落点 | `{SENTRIX_DATA_DIR}/derived/video/{asset_id}/preview.mp4` | `{SENTRIX_DATA_DIR}/media/derived/video/{asset_id}/preview.mp4` |

**时机**：2.0 的导入本来就是同步的（整册导入甚至在同一请求里跑完整趟画像流水线），
没有 worker 可以挂。代价是导入一个长视频要等一次转码——S8a 取「能播」优先。
**落点**：2.0 的媒体文件统一在 `media_dir` 下，派生件跟它同一个根，换数据目录就整体换
（与 `Settings.preview_dir` / `qdrant_dir` 都由 `data_dir` 派生是同一条理由）。

### 4.6 `cleanup_derivatives` 的级联范围

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `cleanup_video_derivatives` → 对每个派生物再调 `cleanup_asset_derivatives`，后者递归清掉那张图名下的观察 / 向量 / 人脸 / 提及等一大堆行 |
| **2.0 S8a 做多少** | 只清：场景事件、事件的 `event_observations`、派生物本身、以及挂在派生物上的 `observations`。**人脸 / 实体提及 / 地点 / 向量那几张表的级联没做** |
| **为什么** | 那几行是 S8b 的 T513（派生帧送进图片流水线）造出来的，而 T514 才是「幂等与清理」。照着还没写的写侧去猜表结构、多删一张表都可能是错的。**S8a 里这些行根本不存在**，所以不做不会漏 |
| **可逆性** | 高。S8b 的 T514 在同一个方法里往下接 |
| **证据强度** | 计划书 §2 的 S8b 任务表（T513 落派生物、T514 幂等与清理） |

---

## 5. 施工中的新增偏离（S8c · T521–T523，2026-10-04）

S8c 交付「音频能导入、能读元数据、能播」，**不转写**（§1.1）。下面每条都指到具体的一处
代码或一件 1.0 没有的东西，**没有一条是凭喜好**。

### 5.1 音频元数据是 **NEW-2.0**，1.0 没有对应物

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | 对音频只有一个动作：FunASR 转写（`pipeline.py:880` 的 `_audio_observation`）。它的 `probe_video_metadata` 遇到没有视频流的文件直接抛 `ValueError("ffprobe found no video stream")`（`video/metadata.py:52`），**音频永远走不到元数据那条路**，时长/编码/采样率从没落过库 |
| **2.0 改成什么** | 单列 `AudioMetadataReader` + `AudioMetadata`（时长/编码/采样率/声道/码率 + 拍摄时间/GPS），ffprobe 子进程与容器标签解析与视频**共用 `infrastructure/media/ffprobe.py`** |
| **为什么单列而不是复用 `VideoMetadataReader`** | 形状不同：音频要采样率/声道/码率，视频要帧率/宽高/旋转角；硬塞进同一个类型会让两边各背一半恒空字段，而类型上看不出哪几个字段对这类媒体有意义。与 S8a 单列 `VideoMetadataReader`（不复用 EXIF 的 `MetadataReader` 三元组）是同一条理由 |
| **可逆性** | 高。不加列、不引依赖，元数据全在 `assets.metadata_json` 的 `audio_metadata` 键里 |
| **证据强度** | 用户 2026-10-04 裁决「音频只做入库与播放」里「能读元数据（时长等）」那一句；1.0 源码（`video/metadata.py:52`）证明它没有这条能力 |

### 5.2 音频交付的 content-type 由后端按后缀定（**1.0 是照抄上传方**）

| 项 | 1.0 | 2.0 |
|---|---|---|
| `asset_file` | `media_type = value.get("mime_type") or "application/octet-stream"`（`app.py:2463`），原样交出去 | 音频（且上传方没给具体 `audio/*`）时按后缀定：mp3→`audio/mpeg`、m4a→`audio/mp4`…… |

**为什么。** S8a 的独立验收观察到 `?original=1` 会回 `application/octet-stream`，根因就是
这一句——预览那条路有转码件兜底（错的类型只在逃生口上），而**音频不转码、交出去的就是
原文件，类型错了浏览器没有第二次机会**。判定收在后端（后缀是权威），不指望上传方
（curl / 整册导入给的正是 `application/octet-stream`）。
`docs/refactor/behaviour-baseline.md` 的音频段写明了这六个后缀的映射。

### 5.3 音频的落库只写一半（**没有读方法**）

`AudioRepository` 只有 `save_probe`，**刻意没有 `probe()`**。视频那边的读写成对是因为
`GET /videos/{id}` 与 `deliver_media` 要读回预览件路径；音频没有读侧消费者（元数据随
`/assets/{id}` 的行在库里，检索侧读的是 `asset.media_type`）。**预留一个没有调用者的读
方法就是死代码**，`scripts/check_method_reachability.py` 会当场报红。

### 5.4 `/preview` 对音频回落原文件（与视频同一个 500 的修法）

2.0 的 `/preview` 原先无条件走 `PillowPreviewEncoder`；S8a 给视频开了分流，S8c 把音频
挂进同一条路（`_self_served_or_none` 认 video ∪ audio）。音频的「预览」就是它自己。
顺带把原片分支补上 `path.exists()` 检查——两条路交付同一个文件时，缺失文件应当是同一个
404（此前视频那条会变成 500）。

### 5.5 T522 的核查结论：计划书 §2 引的 `fact_query.py` 那一行**引错了**，且**不改**

计划书 T515/T522 写「`query_contracts.py:321-324`、`fact_query.py:47` 的媒体过滤已含
audio」。实测：

- `query_contracts.py` 的 `parse_media_type` **确实**认「音频 / audio / 录音」→ `"audio"`
  （:323-324）；
- `application/retrieval/retrieve_metadata.py:97` 直接按 `asset.media_type` 过滤，
  `api/routes/search.py:131` 用 `MediaType(media_type)`，两处对音频**都通**；
- `infrastructure/persistence/repositories/structured.py` 的媒体 SQL 是通用的
  `a.media_type IN (...)`，值域由约束决定，**也通**；
- **但 `domain/retrieval/fact_query.py:47` 的 `MEDIA_VALUES` 是
  `frozenset({"image", "video"})`——不含 audio**，`filters.media="audio"` 会被判
  `unsupported_media`。

这个不含**是 1.0 的行为、且有既有用例钉住**：`tests/unit/test_fact_query.py` 的
`test_unsupported_media_is_rejected` 的 docstring 写着「1.0 的 `_STRUCTURED_FACT_FILTERS`
只放 image / video，2.0 照旧」，参数化里 `audio` 与 `text` / `pdf` 并列。
**按 T522 的纪律不改**：这不是本切片的缺口，而是一条被测试钉住的 1.0 兼容决定；为了让
「音频也能被结构化统计」去改它，等于替 1.0 做一个它没做过的产品决定。记在这里备查。
音频的检索面（词法文件名锚点 + `media_type=audio` 结构化条件）在
`tests/contract/test_audio_routes.py::test_audio_is_reachable_by_file_name_and_media_type`
里钉住。

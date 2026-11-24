"""嵌入方案注册表：**整方案原子切换，禁止两个槽各切各的**。

历史教训（两个来源，都读过再改）：IMAGE/TEXT 嵌入器曾被 env/.env **各自**反复切换
（clip / chinese_clip / bge），结果 memory_vectors、ANN 索引、FTS 各套并存且互不匹配，
检索时好时坏，而且**没有任何一处会报错**。

2.0 原先把方案钉死在 wemm。2026-10-20 为 16 GB Mac mini 重新引入 legacy 方案
（chinese-clip 768d + bge-m3 1024d），但保留了那条教训的核心约束：

1. **一次只选一个具名方案**（``SENTRIX_EMBEDDING_SCHEME``），不存在「图片用 A、
   文本用 B」的独立开关。方案内部的图文配对由这张表保证。
2. **切换必须伴随重建索引**。向量按 ``model_name`` 分套；旧方案的向量可以留着，
   但查询只会命中当前方案的行，不重建就是「索引为空」。
3. **默认仍是 wemm**，153 不设这个变量，行为与之前完全一致。

禁止的是「零散切换」，不是「有版本、可审查的整方案迁移」。

**W1（2026-10-03）把默认方案换成 wemm：文本/图像/视频在同一个 2048 维空间里。**
legacy 是 1.0 的历史方案，只用于没有 2B 嵌入显存/内存的机器（如 16 GB Mac mini）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# 1.0 ``backend/embeddings/wemm_sidecar.py:34-35`` 的模型名与原生维度。
# 侧车在 8102，实测 ``/health`` 报 ``{"dimension": 2048,
# "matryoshka_dimensions": [64,128,256,512,1024,2048]}``。
WEMM_MODEL = "tencent/WeMM-Embedding-2B"
# **不截断。** 1.0 的 ``SENTRIX_WEMM_TRUNCATE_DIM`` 只在显式设置时才带该字段，
# 默认走满维；用户验证 wemm 效果时用的就是这个配置。MRL 截断（64..1024）是
# 一个可选的省空间手段，但它会改变向量，**换截断档等同于换模型**，要伴随重建。
WEMM_DIMENSION = 2048

# ---- 两个槽：W1 起由同一个模型服务 ------------------------------------------------
IMAGE_EMBEDDER = "wemm"
TEXT_EMBEDDER = "wemm"
IMAGE_MODEL = WEMM_MODEL
TEXT_MODEL = WEMM_MODEL
VISUAL_DIMENSION = WEMM_DIMENSION
TEXT_DIMENSION = WEMM_DIMENSION

# ---- 三个 space：**保留，不合并** --------------------------------------------------
# 统一空间指的是「文本和图在同一个向量空间里，跨模态检索原生成立」，
# **不是**「所有向量都塞进一个 space」。三个 space 各有分工：
#
# - ``visual``：资产图像向量（``source_type=asset``）+ **人脸向量**
#   （``source_type=face_instance``，buffalo_l 512 维，与图像共用这个 space 靠
#   ``source_type`` 与 ``model_name`` 区分）。合并会把 512 维的人脸向量卷进文本检索；
# - ``semantic`` / ``episodic``：观察与事件的文本向量。
#
# 1.0 的 ``consistency.SPACE_ROLE`` 在 wemm 下**同样分三个**（visual→image_model，
# episodic/semantic→text_model），这是照搬而不是这里的选择。
VISUAL_SPACE = "visual"
SEMANTIC_SPACE = "semantic"
EPISODIC_SPACE = "episodic"
# 事件文档独立命名空间：只放 active 事件正文，不与观察/事件旧桥共用集合或 top-k。
EVENT_DOCUMENT_SPACE = "event_document"

# ---- 方案注册表：整方案原子切换 ------------------------------------------------

# 1.0 ``backend/embeddings/chinese_clip_visual.py`` 与 ``bge_text.py``。
# 两个模型**不在同一个空间**：视觉 768 维、文本 1024 维，分别落在 visual 与
# semantic/episodic 两个 space 里。检索仍成立，因为 visual 通道只与 visual 空间比，
# 文本通道只与 semantic/episodic 比；不存在跨模态同空间的前提。
LEGACY_VISUAL_MODEL = "chinese-clip-ViT-L-14"
LEGACY_TEXT_MODEL = "BAAI/bge-m3"
LEGACY_VISUAL_DIMENSION = 768
LEGACY_TEXT_DIMENSION = 1024


@dataclass(frozen=True, slots=True)
class EmbeddingScheme:
    """一个具名方案。槽位（visual/text）是方案内部的决定，不是两个独立开关。"""

    name: str
    visual_embedder: str
    text_embedder: str
    visual_model: str
    text_model: str
    visual_dimension: int
    text_dimension: int
    unified: bool


SCHEMES: Mapping[str, EmbeddingScheme] = {
    "wemm": EmbeddingScheme(
        name="wemm",
        visual_embedder="wemm",
        text_embedder="wemm",
        visual_model=WEMM_MODEL,
        text_model=WEMM_MODEL,
        visual_dimension=WEMM_DIMENSION,
        text_dimension=WEMM_DIMENSION,
        unified=True,
    ),
    "legacy": EmbeddingScheme(
        name="legacy",
        visual_embedder="chinese_clip",
        text_embedder="bge",
        visual_model=LEGACY_VISUAL_MODEL,
        text_model=LEGACY_TEXT_MODEL,
        visual_dimension=LEGACY_VISUAL_DIMENSION,
        text_dimension=LEGACY_TEXT_DIMENSION,
        unified=False,
    ),
}

DEFAULT_SCHEME = "wemm"


def get_scheme(name: str) -> EmbeddingScheme:
    """按名字取方案。**未知名字直接抛**：静默回退到默认会让「配错了」变成
    「跑的是另一套模型」，而两套模型的向量互不兼容。"""
    try:
        return SCHEMES[name]
    except KeyError:
        raise ValueError(f"未知的嵌入方案 {name!r}；可选：{', '.join(sorted(SCHEMES))}") from None

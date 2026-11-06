"""文本召回：整句查询 → semantic / episodic 两个空间的向量检索 → 按 Asset 聚合。

对应 1.0 ``backend/retrieval/text_ann.py``。它是 `fusion.py` 的权重表里一直留着位置、
而 2.0 一直没有实现的那一路（``DEFAULT_CHANNEL_WEIGHTS`` 的 ``text_ann: 0.5``）。

## 三处照抄 1.0

**一、查两个空间。** 1.0 的 ``spaces = ("semantic", "episodic")``——写入侧把同一条向量
写进这两个空间（``IndexObservationText``），查询侧就要两边都查。只查一个会漏掉一半。

**二、按 ``asset_id`` 聚合，取最大相似度。** 命中是 observation 级的，而候选要按 Asset
交付；同一张图的 Obs1 与 Obs2 都命中时只该出一条。1.0 ``text_ann.py:110-113``：
``if current is None or similarity > current[1]`` ——取**最大**那个，不是求和也不是取先到的。

**三、映不回去的命中直接丢。** ``metadata["asset_id"]`` 是写入侧刻意带的
（``IndexObservationText`` 的模块注释）。没有它就交付不出一张图，
**而检索结果里少几张图不会有任何报错**——所以这里显式丢掉，让它在计数上可见。

## 一处与 1.0 的差异

1.0 先问 Qdrant、为空再回退静态 HNSW。**2.0 的文本通道不走索引**：``vector_index``
的镜像只写视觉向量（``IndexAssetVisual._mirror``），文本向量从没镜像过。
所以这里只用 ``search_exact``（SQLite 权威存储）——与 ``retrieve_visual`` 的降级路径
是同一条，只是这边没有「索引可用」那一档。等哪天文本向量也进了镜像，
这里再加索引分支，``degraded`` 字段的形状已经留好了。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sentrix.application.retrieval.scheme_drift import scheme_drift_reason
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.fusion import fuse
from sentrix.domain.retrieval.models import CandidateHit
from sentrix.domain.retrieval.ports import TextEmbedder, VectorHit, VectorRepository

# 与写入侧 ``IndexObservationText`` 同一个字面量。两处各写一遍是有代价的，
# 但它比「从写入用例 import 一个常量」好：召回侧不该依赖写入侧的模块。
OBSERVATION_SOURCE = "observation"
AUDIO_SEGMENT_SOURCE = "audio_segment"

log = logging.getLogger("sentrix.retrieval.text_ann")

CHANNEL = "text_ann"
# 1.0 ``text_ann.py:26`` 的 ``spaces``。写入侧两个空间都写，查询侧就要两个都查。
SPACES = (scheme.SEMANTIC_SPACE, scheme.EPISODIC_SPACE)
# 与视觉通道同一个过取倍数：多取一些再让融合层裁。
OVERSAMPLE_FACTOR = 4
# 交付时上报的来源标识。``sqlite_exact`` 与 ``retrieve_visual`` 的降级档同名，
# 因为那确实是同一件事：权威存储上的精确检索。
BACKEND_EXACT = "sqlite_exact"


@dataclass(frozen=True, slots=True)
class TextSearchRequest:
    text: str
    # None = 跨全部相册，理由同 `EvidenceRequest.scope_id`。
    scope_id: str | None = None
    limit: int = 20


@dataclass(frozen=True, slots=True)
class TextSearchHit:
    asset_id: str
    score: float
    channels: Mapping[str, int] = field(default_factory=dict)
    raw_scores: Mapping[str, float] = field(default_factory=dict)
    # 音频分段命中时保留定位；普通 Observation 命中为 None。
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TextSearchResult:
    hits: Sequence[TextSearchHit] = field(default_factory=tuple)
    backend: str = "none"
    degraded: bool = False
    degraded_reason: str = ""
    authoritative_vectors: int = 0


class RetrieveTextAnn:
    """单通道用例。与 ``RetrieveVisual`` 对称：内部先 ``fuse`` 一次再交付。"""

    def __init__(self, *, embedder: TextEmbedder, vectors: VectorRepository) -> None:
        self._embedder = embedder
        self._vectors = vectors

    def execute(self, request: TextSearchRequest) -> TextSearchResult:
        text = request.text.strip()
        if not text:
            return TextSearchResult()

        query = self._embedder.embed_query(text)
        if not query:
            # **端口上问不出「可用吗」**（``TextEmbedder`` 只有 model_id / dimension /
            # embed_query），所以不可用表现为空向量——这是适配器的契约：
            # sidecar 没起、模型没加载、熔断中，一律返回空表而不是抛。
            return TextSearchResult(
                hits=(),
                backend="none",
                degraded=True,
                degraded_reason="embedder_unavailable",
            )
        if len(query) != self._embedder.dimension:
            return TextSearchResult(
                hits=(),
                backend="none",
                degraded=True,
                degraded_reason=f"query_dimension_mismatch:{len(query)}!={self._embedder.dimension}",
            )

        limit = max(1, request.limit)
        fetch = max(limit * OVERSAMPLE_FACTOR, limit)
        candidates = self._recall(request.scope_id, query, fetch)
        authoritative = self._vectors.count(space=scheme.SEMANTIC_SPACE, model_name=self._embedder.model_id)
        # 文本通道写**两个** space，所以两个都要看：只查 semantic 的话，episodic 侧
        # 单独漂移会漏过去。
        drift = next(
            (
                found
                for found in (
                    scheme_drift_reason(self._vectors, space=space, model_name=self._embedder.model_id)
                    for space in SPACES
                )
                if found
            ),
            "",
        )

        hits = [
            CandidateHit(asset_id=asset_id, channel=CHANNEL, rank=rank, raw_score=score)
            for rank, (asset_id, score, _metadata) in enumerate(candidates, start=1)
        ]
        fused = fuse({CHANNEL: hits}) if hits else []

        metadata_by_asset = {asset_id: metadata for asset_id, _score, metadata in candidates}
        return TextSearchResult(
            hits=tuple(
                TextSearchHit(
                    asset_id=candidate.asset_id,
                    score=candidate.score,
                    channels=dict(candidate.channels),
                    raw_scores=dict(candidate.raw_scores),
                    start_ms=_optional_int(metadata_by_asset.get(candidate.asset_id, {}).get("start_ms")),
                    end_ms=_optional_int(metadata_by_asset.get(candidate.asset_id, {}).get("end_ms")),
                )
                for candidate in fused[:limit]
            ),
            backend=BACKEND_EXACT,
            # 与视觉通道不同，**文本通道不依赖索引**：它本来就只查权威存储，所以
            # 「索引落后」这一类降级在这里不存在。但这不等于它永远不会降级——
            # **方案漂移**（库里有向量、没有一条属于当前方案）对它是同样致命的，
            # 那条判据就在上面。两者的区别写在这里，免得读的人以为它比视觉通道更可信。
            degraded=bool(drift),
            degraded_reason=drift,
            authoritative_vectors=authoritative,
        )

    # ---- 召回 ----------------------------------------------------------------

    def _recall(
        self, scope_id: str | None, query: Sequence[float], fetch: int
    ) -> list[tuple[str, float, Mapping[str, object]]]:
        """两个空间各查一次，按 ``asset_id`` 取最大相似度，降序返回。

        **按 asset 聚合是 1.0 的行为**（``text_ann.py:110-113``）。不聚合的话，
        同一张图被三个 observation 命中就会在融合层拿到三个名次，
        而 RRF 对「同一候选出现多次」没有去重——它的分会被抬成三倍。
        """
        best: dict[str, tuple[float, Mapping[str, object]]] = {}
        best_audio_location: dict[str, tuple[float, Mapping[str, object]]] = {}
        unmapped = 0
        for space in SPACES:
            for hit in self._vectors.search_exact(
                space=space,
                vector=query,
                limit=fetch,
                scope_id=scope_id,
                model_name=self._embedder.model_id,
            ):
                # **事件行不是异常。** 写入侧给 ``episodic/event`` 那行的 metadata 按 1.0
                # 只有 ``observation_id``，它本来就不指向 Asset。每次查询都拿它报一条
                # warning，会把真正的数据缺口淹掉。
                if hit.source_type not in {OBSERVATION_SOURCE, AUDIO_SEGMENT_SOURCE}:
                    continue
                asset_id = self._asset_of(hit)
                if asset_id is None:
                    unmapped += 1
                    continue
                if asset_id not in best or hit.score > best[asset_id][0]:
                    best[asset_id] = (hit.score, dict(hit.metadata))
                if hit.source_type == AUDIO_SEGMENT_SOURCE and (
                    asset_id not in best_audio_location or hit.score > best_audio_location[asset_id][0]
                ):
                    best_audio_location[asset_id] = (hit.score, dict(hit.metadata))
        if unmapped:
            # **观察行**缺 asset_id 才是异常：命中了却交付不出来，在结果条数上表现为
            # 「少几张图」——最容易没人发现的那种错。
            log.warning("文本召回有 %d 条观察命中缺少 asset_id，已丢弃", unmapped)
        # 只有音频片段本身是该资产最强的文本证据（允许浮点并列）时才向上交付
        # 时间锚点。否则一个很弱的音频尾部命中会给视觉/Observation 强命中的资产
        # 附上一段无关对白，扩展上下文反而污染回答模型。
        rows: list[tuple[str, float, Mapping[str, object]]] = []
        for asset_id, (score, _metadata) in best.items():
            audio = best_audio_location.get(asset_id)
            audio_metadata: Mapping[str, object] = {}
            if audio is not None and audio[0] >= score - 1e-9:
                audio_metadata = audio[1]
            rows.append((asset_id, score, audio_metadata))
        return sorted(
            rows,
            key=lambda item: item[1],
            reverse=True,
        )

    @staticmethod
    def _asset_of(hit: VectorHit) -> str | None:
        """命中 → 它属于哪张图。**只认 metadata 里的 ``asset_id``，不退回 source_id**。

        1.0 那条是 ``metadata.get("asset_id") or source_id``（``text_ann.py:109``）——
        退回 source_id 在文本空间里得到的是 **observation id**，而那个东西不是 Asset：
        它会一路当成 asset_id 传下去，到交付那一步才 404，**而检索这一层看起来完全正常**。
        """
        asset_id = hit.metadata.get("asset_id")
        return str(asset_id) if asset_id else None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except ValueError:
        return None

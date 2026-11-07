"""元数据锚点召回：只有已知拍摄时间、媒体类型和确定行政归属能支持相应条件。未知时间/GPS不作为该条件的确定命中，仍可经语义通道召回。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from sentrix.domain.media.capture_time import within_time_bounds
from sentrix.domain.media.models import Asset, MediaType
from sentrix.domain.media.ports import AssetRepository
from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.matching import place_admin_matches
from sentrix.domain.places.models import Place
from sentrix.domain.places.ports import AssetPlaceRepository
from sentrix.domain.retrieval.models import CandidateHit

CHANNEL = "metadata"

# 1.0 把召回上限做成了 SENTRIX_METADATA_RECALL_LIMIT 环境变量（默认 "200"）。
# 2.0 落成常量：这个值没有任何生产改动记录，而 1.0 的配置纪律是「~126 个 env flag
# 的值互相矛盾」（见 2.0 config.py 的开头注释）。做成 env 只会多一个没人动过的开关。
# 它同时是「纯结构化查询最多返回多少张」的唯一上限，改它等于改 metadata 通道的强度，
# 应当连同证据一起改，而不是在部署时改。
METADATA_RECALL_LIMIT = 200

# 1.0 ``metadata.py:38`` 的 ``list_assets(limit=100_000)``。它是个「一次扫完」的
# 上界，不是一个有语义的配额；比召回上限大两个数量级，写出来是为了说明这一点。
ASSET_SCAN_LIMIT = 100_000

DEFAULT_SCOPE_ID = "home-default"


class RetrieveMetadata:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        places: AssetPlaceRepository,
        aliases: PlaceAliases,
        recognize_place: Callable[[str], bool] = lambda _asset_id: False,
    ) -> None:
        self._assets = assets
        self._places = places
        self._aliases = aliases
        # 「这个 asset 本该有地点但解析不出来」的判据由调用方给：S4 里它就是
        # 「GPS 存在」；写成回调是为了不必让应用层去读 asset 的 GPS 字段。
        self._recognize_place = recognize_place

    def execute(
        self,
        *,
        scope_id: str | None,
        time_bounds: tuple[datetime, datetime] | None = None,
        media_types: Sequence[MediaType] = (),
        negated_media: Sequence[MediaType] = (),
        place: str = "",
        limit: int = METADATA_RECALL_LIMIT,
    ) -> list[CandidateHit]:
        # Metadata 只在有正向结构化条件时才给召回信号。没有条件就返回空，
        # 否则一个纯语义查询会被「scope 内的每张图」当 anchor 污染。
        if not time_bounds and not media_types and not place:
            return []
        internal_limit = max(int(limit or 20), METADATA_RECALL_LIMIT)

        # ``scope_id=None`` = 跨全部相册（1.0 的 ``if scope_id:``）。**不兜底成某个空间**：
        # 那会把「用户在界面上选了全部相册」与「查一个叫 home-default 的空间」当成同一件事，
        # 而后者在 2.0 的空库里根本不存在——表现是检索一个东西都搜不到。
        candidates: list[Asset] = list(self._assets.list_by_scope(scope_id, limit=ASSET_SCAN_LIMIT))
        places = self._places.get_many([asset.id for asset in candidates])
        allowed_media = set(media_types)
        denied_media = set(negated_media)

        hits: list[CandidateHit] = []
        for asset in candidates:
            # 二次过滤：``list_by_scope`` 已经按 scope 筛过了，这里只是不让上游改动
            # 悄悄放过跨空间的候选。**但 None 时不能过滤**——那正是「全部相册」。
            if scope_id is not None and asset.scope_id != scope_id:
                continue
            if allowed_media and asset.media_type not in allowed_media:
                continue
            if asset.media_type in denied_media:
                continue
            if time_bounds and not _within(time_bounds, asset.captured_at):
                continue
            if place and not self._place_ok(place, asset, places.get(asset.id)):
                continue
            hits.append(
                CandidateHit(
                    asset_id=asset.id,
                    channel=CHANNEL,
                    rank=len(hits) + 1,
                    # metadata 是**锚点**通道：命中即确定性成立，没有「有多像」。
                    # 融合按 domain/retrieval/fusion.py 的 ANCHOR_CHANNELS 给它加固定加成。
                    raw_score=0.0,
                    score_kind="discrete",
                    higher_is_better=True,
                )
            )
            if len(hits) >= internal_limit:
                break
        return hits

    def _place_ok(self, place: str, asset: Asset, resolved: Place | None) -> bool:
        """GPS解析缺失或仅有附近点时，不建立地点锚点。"""
        if resolved is not None:
            return place_admin_matches(place, resolved, self._aliases)
        return False


def _within(bounds: tuple[datetime, datetime], captured_at: datetime | None) -> bool:
    return within_time_bounds(bounds, captured_at) is True


__all__ = ["ASSET_SCAN_LIMIT", "CHANNEL", "METADATA_RECALL_LIMIT", "RetrieveMetadata"]

"""地点解析落到 ``asset_places``：一张 Asset 的 GPS → 一行地点。

对应 1.0 ``pipeline.prepare_asset`` 对每张图调一次 ``geocoder.lookup`` 的那一步，
差别只在落点：1.0 把结果塞进 ``assets.metadata_json["reverse_geocode"]``（一个两套键
的扁平 dict），2.0 落到独立表 ``asset_places``（迁移 0005）。

与 ``AssetVisualIndexer`` 同构：导入用例依赖 ``AssetPlaceIndex`` 这个抽象，不依赖
具体的解析器。

**解析不出就不写行。** 「没解析过」= 没有行；「解析过且有结果」= 有行。第三个状态
「有 GPS 但解析不出」由 **asset 上有 GPS + 表里没有行** 这两件事一起表达
（``RetrieveMetadata`` 的 ``recognize_place`` 回调就是按这个判据剔除的），而不是写
一行空地点——``Place`` 两槽皆空会直接抛，那是它设计上要拦的东西。见
``s4-deviations.md`` §2 第 23 项。
"""

from __future__ import annotations

from sentrix.domain.media.models import Asset
from sentrix.domain.places.models import AdminArea, NearestPlace, Place, PlacePrecision, PlaceSource
from sentrix.domain.places.ports import AssetPlaceIndex, AssetPlaceRepository, PlaceResolver


class IndexAssetPlace(AssetPlaceIndex):
    def __init__(self, *, resolver: PlaceResolver, places: AssetPlaceRepository) -> None:
        self._resolver = resolver
        self._places = places

    def index(self, asset: Asset) -> bool:
        gps = asset.captured_location
        supplied = asset.source_metadata
        place: Place | None
        if supplied.precise_address.strip():
            place = Place(
                admin=AdminArea(),
                nearest=NearestPlace(),
                precision=PlacePrecision.ADDRESS,
                source=PlaceSource.PROVIDED,
                gps=gps,
                precise_address=supplied.precise_address,
                address_source=supplied.address_source,
                address_source_sha256=supplied.address_source_sha256,
            )
        elif gps is not None:
            place = self._resolver.resolve(gps)
        else:
            return False
        if place is None:
            return False
        self._places.save(asset_id=asset.id, scope_id=asset.scope_id, gps=gps, place=place)
        return True

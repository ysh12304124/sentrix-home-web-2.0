"""``OfflinePlaceResolver``：面（PyGeoCN）与点（GeoNames）**互补**，不是二选一。

1.0 的 ``OfflineReverseGeocoder.lookup()``（``geocoding.py:196-216``）先问 PyGeoCN，
拿到结果就**不再问** ``reverse_geocoder``——它只有一个 ``label`` 槽位，两个来源写进去
会互相盖掉。2.0 有 ``admin`` / ``nearest`` 两个语义不同、不可互换的槽位（设计 §3.2），
所以两个来源都要问一遍，才能拼出 ``浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近``。

这里合成的是**数据**（一份两行的 GeoNames dump），不是被测的那条合成规则：假
``AdminResolver`` 提供面，真 ``GeoNamesNearestResolver`` 提供点。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import AdminArea, PlacePrecision, PlaceSource
from sentrix.infrastructure.places.composite import (
    GEONAMES_CONFIDENCE,
    PYGEOCN_CONFIDENCE,
    OfflinePlaceResolver,
)
from sentrix.infrastructure.places.geonames import GeoNamesNearestResolver

XIAOSHAN = GpsPoint(30.1839, 120.2642)
XIAOSHAN_AREA = AdminArea(province="浙江省", city="杭州市", district="萧山区")


def row(
    geoname_id: int, name: str, latitude: float, longitude: float, feature_code: str, country: str
) -> str:
    columns = [""] * 19
    columns[0] = str(geoname_id)
    columns[1] = name
    columns[2] = name
    columns[4] = str(latitude)
    columns[5] = str(longitude)
    columns[6] = feature_code[0]
    columns[7] = feature_code
    columns[8] = country
    return "\t".join(columns)


DUMP = "\n".join(
    [
        row(1, "赵州镇", 30.1840, 120.2643, "ADM4", "CN"),
        row(2, "大石桥村", 30.1841, 120.2644, "PPL", "CN"),
    ]
)


class StubAdminResolver:
    """面来源的假件。``area`` 为空表示这个坐标不在边界数据覆盖内。"""

    def __init__(self, area: AdminArea | None) -> None:
        self._area = area

    @property
    def available(self) -> bool:
        return True

    def resolve_admin(self, gps: GpsPoint) -> AdminArea | None:
        return self._area


def build(tmp_path: Path, *, area: AdminArea | None, with_points: bool) -> OfflinePlaceResolver:
    directory = tmp_path / ("geo" if with_points else "geo-empty")
    directory.mkdir()
    if with_points:
        (directory / "synthetic.txt").write_text(DUMP, encoding="utf-8")
    return OfflinePlaceResolver(
        admin=StubAdminResolver(area),
        nearest=GeoNamesNearestResolver(directory),
    )


def test_both_sources_land_in_their_own_slots(tmp_path: Path):
    place = build(tmp_path, area=XIAOSHAN_AREA, with_points=True).resolve(XIAOSHAN)

    assert place is not None
    assert place.admin == XIAOSHAN_AREA
    assert place.nearest.township == "赵州镇"
    assert place.nearest.locality == "大石桥村"
    assert place.display() == "浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近"


def test_precision_and_confidence_prefer_the_admin_face(tmp_path: Path):
    """两个来源都在时来源记 PyGeoCN、置信度 0.90——``admin`` 是权威归属那一段。"""
    place = build(tmp_path, area=XIAOSHAN_AREA, with_points=True).resolve(XIAOSHAN)

    assert place is not None
    assert place.precision is PlacePrecision.DISTRICT
    assert place.source is PlaceSource.PYGEOCN
    assert place.confidence == pytest.approx(PYGEOCN_CONFIDENCE)


def test_missing_admin_still_returns_the_nearest_point(tmp_path: Path):
    """面缺席（国外坐标，或 PyGeoCN 没装）不该让整次解析失败。"""
    place = build(tmp_path, area=None, with_points=True).resolve(XIAOSHAN)

    assert place is not None
    assert place.admin.is_empty
    assert place.nearest.locality == "大石桥村"
    assert place.precision is PlacePrecision.LOCALITY
    assert place.source is PlaceSource.GEONAMES
    assert place.confidence == pytest.approx(GEONAMES_CONFIDENCE)
    assert place.display() == "赵州镇 / 大石桥村附近"


def test_missing_points_still_returns_the_admin_area(tmp_path: Path):
    """点缺席（GeoNames 数据没下）时展示里**没有「附近」**——那是它唯一的诚实说法。"""
    place = build(tmp_path, area=XIAOSHAN_AREA, with_points=False).resolve(XIAOSHAN)

    assert place is not None
    assert place.nearest.is_empty
    assert place.display() == "浙江省杭州市萧山区"


def test_neither_source_returns_none_not_an_empty_place(tmp_path: Path):
    """两个槽位都空时必须返回 ``None``——构造一个两槽皆空的 ``Place`` 会直接抛。"""
    assert build(tmp_path, area=None, with_points=False).resolve(XIAOSHAN) is None

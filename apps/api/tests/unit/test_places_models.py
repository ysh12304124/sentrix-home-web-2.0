"""地点领域模型：两个槽位的不可互换性、展示形式、精度枚举。"""

from __future__ import annotations

import pytest

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)


def place(admin: AdminArea | None = None, nearest: NearestPlace | None = None, **overrides) -> Place:
    fields: dict[str, object] = {
        "admin": admin or AdminArea(),
        "nearest": nearest or NearestPlace(),
        "precision": PlacePrecision.DISTRICT,
        "source": PlaceSource.PYGEOCN,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Place(**fields)  # type: ignore[arg-type]


def test_both_slots_empty_is_rejected():
    """两个槽位都空时必须返回 None，而不是构造一个空 Place——构造即抛。"""
    with pytest.raises(ValueError, match="两个槽位都为空"):
        place()


def test_admin_only_is_valid():
    assert place(admin=AdminArea("浙江省", "杭州市", "萧山区")).admin.district == "萧山区"


def test_nearest_only_is_valid():
    """国外坐标只有最近邻点，没有行政面——这是合法状态，不是残缺。"""
    only_nearest = place(nearest=NearestPlace(locality="Chiang Mai", distance_m=3417.0))
    assert only_nearest.admin.is_empty
    assert only_nearest.nearest.locality == "Chiang Mai"


def test_display_uses_dot_separator_and_fujin():
    """展示形式来自设计 §3.2：``浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近``。"""
    value = place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
    )
    assert value.display() == "浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近"


def test_display_without_nearest_has_no_fujin():
    """``附近`` 只属于最近邻点。给权威归属加「附近」会把确定性说没。"""
    value = place(admin=AdminArea("浙江省", "杭州市", "萧山区"))
    assert value.display() == "浙江省杭州市萧山区"
    assert "附近" not in value.display()


def test_admin_label_dedupes_repeated_levels():
    """直辖市 ``province == city``，只出现一次。1.0 ``geocoding.py:124-127``。"""
    assert AdminArea("北京市", "北京市", "朝阳区").label() == "北京市朝阳区"


def test_nearest_label_dedupes_repeated_levels():
    assert NearestPlace("赵州镇", "赵州镇").label() == "赵州镇"


def test_admin_area_rejects_unstripped_names():
    with pytest.raises(ValueError, match="空白"):
        AdminArea(" 浙江省 ")


def test_nearest_rejects_negative_distance():
    with pytest.raises(ValueError, match="距离"):
        NearestPlace("赵州镇", "大石桥村", -1.0)


def test_nearest_allows_unknown_distance():
    """距离未知是合法值（乡镇来自面、村庄来自点，只有一个时距离可能拿不到）。"""
    assert NearestPlace("赵州镇").distance_m is None


def test_confidence_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="confidence"):
        place(admin=AdminArea("浙江省"), confidence=1.5)


def test_searchable_text_never_contains_coordinates():
    """1.0 的 label 可能退化成坐标串，``_event_display_place`` 专门写了正则去挡。"""
    gps = GpsPoint(30.123456, 120.654321)
    value = place(admin=AdminArea("浙江省", "杭州市", "萧山区"), gps=gps)
    text = value.searchable_text()
    assert "30.1" not in text
    assert "120.6" not in text
    assert text == "浙江省 杭州市 萧山区"


def test_precision_enum_has_city_level():
    """``city`` 是相对设计 §3.2 的有意增补——1.0 ``geocoding.py:121`` 本来就有这一级。"""
    assert PlacePrecision.CITY.value == "city"
    assert {item.value for item in PlacePrecision} == {"district", "city", "township", "locality"}


def test_gps_distance_km_matches_known_pair():
    """北京—上海大圆距离约 1067 km。"""
    beijing = GpsPoint(39.9042, 116.4074)
    shanghai = GpsPoint(31.2304, 121.4737)
    assert 1060 < beijing.distance_km(shanghai) < 1075


def test_gps_distance_to_self_is_zero():
    point = GpsPoint(30.0, 120.0)
    assert point.distance_km(point) == pytest.approx(0.0, abs=1e-9)

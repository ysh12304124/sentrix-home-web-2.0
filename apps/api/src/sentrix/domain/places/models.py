"""地点领域模型。纯数据与规则，零 IO。

1.0 把地点压成一个字符串：``backend/geocoding.py`` 的 ``lookup()`` 返回一个**扁平
dict**，``assets.metadata_json["reverse_geocode"]`` 原样存下它，检索层再把它拼成一个
``label`` 做子串比较。那个 dict 长这样（两处分支，字段还不一样）：

    # _lookup_pygeo（国内，走 PyGeoCN 的天地图边界）
    {"source": "tianditu", "precision": "district", "label": "浙江省杭州市萧山区",
     "province": "浙江省", "city": "杭州市", "district": "萧山区",
     "admin1": "浙江省", "admin2": "萧山区", "country": "CN",
     "latitude": 30.1, "longitude": 120.2, "confidence": 0.90, "distance_km": 0}

    # _lookup_reverse_geocoder（国外，走 GeoNames）
    {"source": "geonames", "precision": "city", "label": "Chiang Mai, Chiang Mai, TH",
     "name": "Chiang Mai", "city": "Chiang Mai", "province": "Chiang Mai",
     "district": "Chiang Mai", "country": "TH", "cc": "TH",
     "latitude": 18.7, "longitude": 98.9, "matched_latitude": 18.79,
     "matched_longitude": 99.0, "confidence": 0.70, "distance_km": 3.417}

两类信息被压进了同一层：``province/city/district`` 来自**面**（行政区边界，点落进去
就是权威归属，可以拿来做硬条件过滤），而 GeoNames 那一路的 ``name`` 来自**最近邻点**
（只能说「在附近」）。混成一个字符串之后，检索层看不出哪个词是权威的、哪个只是邻近的，
于是「萧山区」与「赵州桥附近」在过滤时被同等对待。

2.0 按设计文档 §3.2 拆成 ``admin``（面）+ ``nearest``（点）两个不可互换的槽位，
``precision`` 说明这次解析到底落在了哪一级。

NEW-2.0：这个结构 1.0 没有，是设计文档 §3.2 明确定的新模型。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from sentrix.domain.media.models import GpsPoint


class PlacePrecision(StrEnum):
    """这次解析落到了哪一级。国内：district > township > locality；国外同构。

    ``CITY`` 是相对设计文档 §3.2 的一处增补。设计只列了三级，但 1.0
    ``_lookup_pygeo`` 的返回值是 ``"precision": "district" if district else "city"``，
    ``_lookup_reverse_geocoder`` 恒返回 ``"precision": "city"``——**1.0 本来就有这一级**。
    把「只解析到市」硬塞进 ``district`` 会让下游以为拿到了区县级归属，而这正是
    admin / nearest 分槽要防的那种谎。依据：``backend/geocoding.py:121``、`:172`。
    """

    DISTRICT = "district"
    CITY = "city"
    TOWNSHIP = "township"
    LOCALITY = "locality"


class PlaceSource(StrEnum):
    """解析来源。1.0 用 ``"tianditu"`` 指 PyGeoCN，2.0 按数据源改名。"""

    PYGEOCN = "pygeocn"
    GEONAMES = "geonames"


@dataclass(frozen=True, slots=True)
class AdminArea:
    """行政区归属：来自边界数据，是**面**，可以做硬条件过滤。

    ``district`` 是 PyGeoCN 能给出的最细一级。为空表示这一级没有解析出来，不是
    「这个地方没有区」——区分这两件事是这个 dataclass 存在的理由之一。
    """

    province: str = ""
    city: str = ""
    district: str = ""

    def __post_init__(self) -> None:
        for name in ("province", "city", "district"):
            value = getattr(self, name)
            if value != value.strip():
                raise ValueError(f"行政区名两头不许有空白: {name}={value!r}")

    @property
    def is_empty(self) -> bool:
        return not (self.province or self.city or self.district)

    def label(self) -> str:
        """``浙江省杭州市萧山区``。同名层级（直辖市 province==city）只出现一次。"""
        parts: list[str] = []
        for part in (self.province, self.city, self.district):
            if part and part not in parts:
                parts.append(part)
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class NearestPlace:
    """最近邻地点：来自点数据，只能说「在……附近」。

    ``distance_m`` 是查询坐标到匹配点的距离。1.0 只把距离写进 ``distance_km`` 一个
    数字里，从不展示、也不参与任何判定；2.0 保留它是因为「0.27 公里」和「6 公里」
    对「附近」这个词的含义完全不同，展示层有权知道。
    """

    township: str = ""
    locality: str = ""
    distance_m: float | None = None
    township_distance_m: float | None = None
    township_country: str = ""
    locality_country: str = ""
    township_admin_codes: tuple[str, ...] = ()
    locality_admin_codes: tuple[str, ...] = ()
    hierarchy_verified: bool = False

    def __post_init__(self) -> None:
        for distance in (self.distance_m, self.township_distance_m):
            if distance is not None and (not math.isfinite(distance) or distance < 0):
                raise ValueError(f"距离必须是非负有限值: {distance!r}")

    @property
    def is_empty(self) -> bool:
        return not (self.township or self.locality)

    def label(self) -> str:
        # 两个独立最近邻不拼接成隶属关系。
        return " / ".join(dict.fromkeys(p for p in (self.township, self.locality) if p))


@dataclass(frozen=True, slots=True)
class Place:
    """一次地点解析的结果。``admin`` 与 ``nearest`` 语义不同，不可互相顶替。"""

    admin: AdminArea
    nearest: NearestPlace
    precision: PlacePrecision
    source: PlaceSource
    gps: GpsPoint | None = None
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.admin.is_empty and self.nearest.is_empty:
            raise ValueError("地点解析结果两个槽位都为空——这种情况必须返回 None，不是 Place")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 越界: {self.confidence!r}")

    def display(self) -> str:
        """展示形式：``浙江省杭州市萧山区 · 赵州镇大石桥村附近``。

        ``nearest`` 始终带「附近」——那是它唯一的诚实说法；``admin`` 是权威
        归属，加「附近」会把确定性说没。
        """
        head = self.admin.label()
        tail = self.nearest.label()
        if tail:
            tail = f"{tail}附近"
        return " · ".join(part for part in (head, tail) if part)

    def searchable_text(self) -> str:
        """写进词法索引与地点匹配的文本。刻意**不含** ``gps`` 坐标。

        1.0 把 ``label`` 直接写进索引，而 ``label`` 在 PyGeoCN 那条路里可能退化成
        坐标串（``_event_display_place`` 专门用 ``DISPLAY_COORDINATE_RE`` 挡这件事）。
        2.0 直接不给坐标进入这个词的机会。
        """
        return " ".join(
            part
            for part in (
                self.admin.province,
                self.admin.city,
                self.admin.district,
                self.nearest.township,
                self.nearest.locality,
            )
            if part
        )

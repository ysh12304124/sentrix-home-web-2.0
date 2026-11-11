"""PyGeoCN 行政区解析（面）。国内省/市/区县。

对应 1.0 ``backend/geocoding.py:103-140`` 的 ``_lookup_pygeo``。数值与分支原样保留：

- ``confidence = 0.90``（1.0 ``:134``；行为基线「tianditu confidence 0.90」）
- ``precision = "district" if district else "city"``（1.0 ``:121``）
- 三级都空 → 解析失败，返回 ``None``（1.0 ``:118-119`` 返回 ``{}``）
- 同名层级去重（1.0 ``:124-127``：直辖市 ``province == city``）

**1.0 把 source 记成 ``"tianditu"``**（天地图是 PyGeoCN 内置边界数据的来源）。
2.0 按数据源命名记成 ``"pygeocn"``——``frozen.md`` 与设计 §3.3 都按 ``pygeocn`` /
``geonames`` 两个来源描述。见 ``s4-deviations.md`` §2 第 4 项。

延迟：``PyGeoCN.regeo`` 每次调用都 ``geopandas.read_file`` 三份 GeoJSON，实测
单次约 0.30 秒。整册导入时同一场活动的照片 GPS 常常逐张不同，所以这里加了一层
按坐标的 LRU——重复坐标不再重复解码，但**每个新坐标仍要付一次 0.3 秒**。
这是 1.0 就有的成本（``pipeline.prepare_asset`` 对每张图各调一次），2.0 不放大也不假装消除。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import AdminArea, PlacePrecision, PlaceSource

log = logging.getLogger(__name__)

PYGEOCN_CONFIDENCE = 0.90
PYGEOCN_SOURCE = PlaceSource.PYGEOCN

# 坐标缓存键的精度：小数点后 5 位 ≈ 1.1 米。再细就是在为浮点噪声建缓存。
_COORD_PRECISION = 5


def _cache_key(gps: GpsPoint) -> tuple[float, float]:
    return (round(gps.latitude, _COORD_PRECISION), round(gps.longitude, _COORD_PRECISION))


@lru_cache(maxsize=4096)
def _regeo(latitude: float, longitude: float) -> tuple[str, str, str] | None:
    """调 PyGeoCN 并归一成 ``(province, city, district)``；失败返回 ``None``。

    ``lru_cache`` 挂在模块函数上而不是方法上：``self`` 会进缓存键，把缓存打散。
    """
    try:
        from PyGeoCN.regeo import regeo
    except ImportError:
        return None
    try:
        result = regeo(latitude, longitude)
    except Exception as error:  # PyGeoCN 内部是 geopandas/shapely，异常面很宽
        log.warning("PyGeoCN 反编码失败 (%s, %s): %s", latitude, longitude, error)
        return None
    if not result or result.get("status") != 1:
        return None
    address = result.get("address") or {}
    province = str(address.get("province") or "").strip()
    city = str(address.get("city") or "").strip()
    district = str(address.get("district") or "").strip()
    if not province and not city and not district:
        return None
    return province, city, district


class PyGeoCNAdminResolver:
    """实现 ``AdminResolver`` 端口。数据源缺席时 ``available`` 为假、``resolve_admin``
    恒返回 ``None``——调用方据此决定是「降级」还是「报错」，本类不替它决定。"""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if not self._enabled:
            return False
        if self._available is None:
            try:
                import PyGeoCN.regeo  # noqa: F401
            except ImportError:
                log.warning("PyGeoCN 未安装，国内行政区解析不可用")
                self._available = False
            else:
                self._available = True
        return self._available

    def resolve_admin(self, gps: GpsPoint) -> AdminArea | None:
        if not self.available:
            return None
        # PyGeoCN 只认中国边界；先用粗 bbox 挡掉绝大多数国外坐标，避免每个点都
        # 扫一遍 geopandas 多边形（批量回填国外相册时这点是分钟级差异）。
        if not (18.0 <= gps.latitude <= 54.0 and 73.0 <= gps.longitude <= 135.0):
            return None
        latitude, longitude = _cache_key(gps)
        resolved = _regeo(latitude, longitude)
        if resolved is None:
            return None
        province, city, district = resolved
        return AdminArea(province=province, city=city, district=district)


def precision_for(admin: AdminArea) -> PlacePrecision:
    """1.0 ``geocoding.py:121``：``"district" if district else "city"``。

    PyGeoCN 是**面**判定，点落在多边形内，没有「距离最近点」这回事——1.0 在返回值里
    硬写 ``"distance_km": 0``（``:135``）。2.0 让 ``NearestPlace`` 缺席，而不是拿一个
    0 去冒充「距离为零」。
    """
    return PlacePrecision.DISTRICT if admin.district else PlacePrecision.CITY

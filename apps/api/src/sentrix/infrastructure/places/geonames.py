"""GeoNames 最近邻解析（点）：国内乡镇/村庄，国外城市/乡镇。

**NEW-2.0。** 1.0 完全没有这一层：``backend/geocoding.py`` 对国内只调 PyGeoCN
（面，到区县），对国外只调 ``reverse_geocoder``（其自带数据到城市）。
``township`` / ``locality`` 这两个槽位、以及下面这套最近邻索引，1.0 零参考。
依据：用户裁决 #1 ——「乡镇/村庄，S4 就加上；这是明确的新特性」。

数据源（设计 §3.3）：

===========  ==================  ========  ==========
级别          来源                条数       性质
===========  ==================  ========  ==========
省/市/区县    PyGeoCN             16MB 边界  面，准确
乡镇/街道     GeoNames ``ADM4``   11,450    点，最近邻
村庄/居民点    GeoNames ``PPL``    860,831   点，最近邻
国外城市       GeoNames ``PPL*``   —         点，最近邻
===========  ==================  ========  ==========

**明确接受的上限**：国内到「区县 + 乡镇/村庄」，国外到「城市/乡镇」，
**永远拿不到「赵州桥景区」这类 POI 地标名**——实测「赵州桥」在 961,888 条中国区
记录里 0 命中，全国 S 类（spot）仅 13,757 条。这不是实现缺陷，是数据源的能力边界。

实现要点：

1. 点存成**三维单位向量**再建 KD-tree。直接在 (lat, lon) 度数上建树在日界线和高纬度
   会给出错误的最近邻；单位向量的欧氏弦长与球面大圆距离单调对应，换算回公里即可。
2. 流式加载、常驻点索引，**逐条查询时不碰磁盘**（设计 §3.3：「导入时计算一次、结果落库，
   运行时零网络依赖」）。
3. **半径上限** ``MAX_NEAREST_RADIUS_M``（默认 50 km）。这一条是相对 1.0 的有意偏离，
   见模块底部与 ``s4-deviations.md`` §2 第 3 项。
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import NearestPlace

log = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_008.8

# GeoNames 特征码分组。
# 乡镇/街道：ADM4 = fourth-order administrative division。设计 §3.3 实测中国区
# 11,450 / 11,878 = 96.4% 的中文名以 镇/乡/街道 结尾。
TOWNSHIP_FEATURE_CODES = frozenset({"ADM4"})
# 村庄/居民点：PPL（populated place）是主体；PPLA* 是行政驻地（县城），
# 对一个「最近的居民点」来说同样是正确回答，且比 PPL 更权威。
LOCALITY_FEATURE_CODES = frozenset(
    {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLX", "PPLL"}
)

# 国内记录的国家码。`CN.zip` 与 `allCountries` 里中国记录都带这个值。
HOME_COUNTRY = "CN"

# 见模块 docstring 第 3 条。
MAX_NEAREST_RADIUS_M = 50_000.0

_COLUMN_COUNT = 19

# 取名字用的汉字区间。用正则而不是逐字符比较：备选名一列可能有几十个名字，
# 逐字符扫在 89 万行上是白花的钱。
_CJK_RE = re.compile(r"[一-鿿]+")


@dataclass(frozen=True, slots=True)
class GeoNamesPoint:
    latitude: float
    longitude: float
    name: str
    feature_code: str
    country: str
    population: int
    admin_codes: tuple[str, ...] = ()


def _to_unit(latitude: float, longitude: float) -> tuple[float, float, float]:
    import math

    lat = math.radians(latitude)
    lon = math.radians(longitude)
    cos_lat = math.cos(lat)
    return (cos_lat * math.cos(lon), cos_lat * math.sin(lon), math.sin(lat))


def _chord_to_metres(chord: float) -> float:
    """弦长 → 大圆距离。``chord`` 是单位向量之差的欧氏长度。"""
    import math

    half = min(1.0, max(0.0, chord / 2.0))
    return 2.0 * EARTH_RADIUS_M * math.asin(half)


def _pick_member(archive_name: str, members: dict[str, int]) -> str | None:
    """从 zip 里挑出**数据文件**，不是 readme。

    官方包里每个 dump 都带一个 ``readme.txt``——实测 ``CN.zip`` 的两个成员是
    ``readme.txt``（8,843 字节）与 ``CN.txt``（132,055,747 字节）。按 ``namelist()``
    的顺序取第一个会拿到 readme：解析出 **0 个点**，不报错，只是整个国内最近邻
    静默失效（`counts` 是 `(0, 2117, 31975)`，而那 2,117 全来自 `cities15000`）。
    这个 bug 只有在拿真数据端到端跑一次时才会露出来——合成数据的 zip 里没有 readme。

    选法：同名成员优先（``CN.zip`` → ``CN.txt``），否则取**最大**的那个 ``.txt``。
    """
    if not members:
        return None
    stem = Path(archive_name).stem.lower()
    for name in members:
        if Path(name).stem.lower() == stem:
            return name
    return max(members, key=lambda name: members[name])


def _iter_dump_lines(path: Path) -> Iterator[str]:
    """逐行读取 dump，避免整份解压文本及 splitlines 列表驻留内存。"""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = {
                info.filename: info.file_size
                for info in archive.infolist()
                if info.filename.lower().endswith(".txt")
            }
            chosen = _pick_member(path.name, members)
            if chosen is None:
                log.warning("GeoNames 压缩包里没有 .txt 成员: %s", path)
                return
            with archive.open(chosen) as handle:
                yield from io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
    else:
        with path.open(encoding="utf-8", errors="replace") as handle:
            yield from handle


def _display_name(name: str, alternates: str) -> str:
    """**优先取中文名。**

    GeoNames 中国记录的 ``name`` 列（第 1 列）绝大多数是**罗马字**
    （``ZhaozhouShitacun`` / ``Xiaoshan``），中文名在 ``alternatenames`` 列（第 3 列）。
    实测：

        ADM4  name='Mei Xian'   alts='Mei Hsien,Mei Xian,Meixian Shi,mei xian,梅县'
        PPL   name='Nyêngbo'    alts='Ningbu,ning bu,རུ་པ,སྙེང་པོ,宁布'
        PPL   name='Changshousi'  alts='Changshousi,zhang shou si,长寿寺'

    只在 ``name`` 里没有汉字时才去备选名里找，取**第一个含汉字的**备选
    （确定性、可复现）。设计 §3.2 给的展示形式是
    ``浙江省杭州市萧山区 · 赵州镇大石桥村附近``，而展示层的「附近」正是给用户看的
    那一句——罗马字的「ZhaozhouShitacun附近」不算兑现那条设计。
    """
    if _CJK_RE.search(name):
        return name
    match = _CJK_RE.search(alternates)
    return match.group(0) if match else name


def _parse_lines(lines: Iterable[str]) -> Iterator[GeoNamesPoint]:
    """按 GeoNames 的 19 列制表符格式解析，只留乡镇/村庄两类特征码。

    列位：0 geonameid · 1 name · 2 asciiname · 3 alternatenames · 4 latitude ·
    5 longitude · 6 feature class · 7 feature code · 8 country code · 14 population。
    列数不足 15 的行直接跳过——截断的下载包会产出这种行，让它进索引比丢一行更糟。
    """
    wanted = TOWNSHIP_FEATURE_CODES | LOCALITY_FEATURE_CODES
    for line in lines:
        if not line:
            continue
        columns = line.split("\t")
        if len(columns) < 15:
            continue
        feature_code = columns[7]
        if feature_code not in wanted:
            continue
        name = _display_name(columns[1].strip(), columns[3] if len(columns) > 3 else "")
        if not name:
            continue
        try:
            latitude = float(columns[4])
            longitude = float(columns[5])
        except ValueError:
            continue
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            continue
        try:
            population = int(columns[14] or 0)
        except ValueError:
            population = 0
        yield GeoNamesPoint(
            latitude=latitude,
            longitude=longitude,
            name=name,
            feature_code=feature_code,
            country=(columns[8] or "").strip().upper(),
            population=population,
            admin_codes=tuple(columns[10:14]),
        )


def parse_dump(text: str) -> list[GeoNamesPoint]:
    """解析内存中的小型 dump；运行时文件加载使用流式路径。"""
    return list(_parse_lines(io.StringIO(text)))


class _PointIndex:
    """一组点的 KD-tree。空组不建树，``query`` 恒返回 ``None``。"""

    def __init__(self, points: list[GeoNamesPoint]) -> None:
        self._points = points
        self._tree = None
        if not points:
            return
        try:
            import numpy
            from scipy.spatial import cKDTree
        except ImportError as error:  # pragma: no cover - 依赖缺失时明确降级
            log.warning("numpy/scipy 不可用，GeoNames 最近邻索引降级为空: %s", error)
            return
        coords = numpy.empty((len(points), 3), dtype="float64")
        for index, point in enumerate(points):
            coords[index] = _to_unit(point.latitude, point.longitude)
        self._tree = cKDTree(coords)

    def __len__(self) -> int:
        return len(self._points)

    @property
    def ready(self) -> bool:
        return self._tree is not None

    def nearest(self, gps: GpsPoint) -> tuple[GeoNamesPoint, float] | None:
        if self._tree is None:
            return None
        import numpy

        query = numpy.array(_to_unit(gps.latitude, gps.longitude), dtype="float64")
        chord, index = self._tree.query(query, k=1)
        distance = _chord_to_metres(float(chord))
        return self._points[int(index)], distance

    def nearest_within(self, gps: GpsPoint, max_metres: float) -> tuple[GeoNamesPoint, float] | None:
        found = self.nearest(gps)
        if found is None:
            return None
        point, distance = found
        return found if distance <= max_metres else None


class GeoNamesNearestResolver:
    """实现 ``NearestResolver`` 端口。

    ``directory`` 下的 ``*.txt`` / ``*.zip`` 全部加载。缺目录、缺文件、scipy 不在
    都只降级为空索引——乡镇/村庄是**增强**，不是小区县解析的前置条件。
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        max_radius_m: float = MAX_NEAREST_RADIUS_M,
        home_country: str = HOME_COUNTRY,
    ) -> None:
        self._directory = directory
        self._max_radius_m = max_radius_m
        self._home_country = home_country
        self._townships: _PointIndex | None = None
        self._localities: _PointIndex | None = None
        self._world_localities: _PointIndex | None = None

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        assert self._townships is not None and self._localities is not None
        assert self._world_localities is not None
        return self._townships.ready or self._localities.ready or self._world_localities.ready

    @property
    def counts(self) -> tuple[int, int, int]:
        """``(乡镇, 国内居民点, 国外居民点)``。"""
        self._ensure_loaded()
        assert self._townships is not None and self._localities is not None
        assert self._world_localities is not None
        return (len(self._townships), len(self._localities), len(self._world_localities))

    def _ensure_loaded(self) -> None:
        if self._townships is not None:
            return
        townships: list[GeoNamesPoint] = []
        localities: list[GeoNamesPoint] = []
        world: list[GeoNamesPoint] = []
        for path in self._dump_paths():
            try:
                for point in _parse_lines(_iter_dump_lines(path)):
                    if point.feature_code in TOWNSHIP_FEATURE_CODES and point.country == self._home_country:
                        townships.append(point)
                    elif point.feature_code in LOCALITY_FEATURE_CODES:
                        if point.country == self._home_country:
                            localities.append(point)
                        else:
                            world.append(point)
            except (OSError, zipfile.BadZipFile) as error:
                log.warning("GeoNames 数据读不了，跳过 %s: %s", path, error)
                continue
            log.info("GeoNames 已载入 %s", path.name)
        self._townships = _PointIndex(townships)
        self._localities = _PointIndex(localities)
        self._world_localities = _PointIndex(world)
        log.info(
            "GeoNames 索引就绪：乡镇 %d / 国内居民点 %d / 国外居民点 %d",
            len(townships),
            len(localities),
            len(world),
        )

    def _dump_paths(self) -> list[Path]:
        if self._directory is None or not self._directory.is_dir():
            return []
        return sorted(
            path
            for path in self._directory.iterdir()
            if path.suffix.lower() in {".txt", ".zip"} and path.is_file()
        )

    def resolve_nearest(self, gps: GpsPoint) -> NearestPlace | None:
        """比较两池实际距离；分池只用于组织数据，不据此推断国家归属。"""
        self._ensure_loaded()
        assert self._townships is not None and self._localities is not None
        assert self._world_localities is not None

        domestic = self._resolve_from(gps, self._townships, self._localities)
        foreign = self._resolve_from(gps, None, self._world_localities)
        if domestic is None:
            return foreign
        if foreign is None:
            return domestic
        domestic_distance = min(
            d for d in (domestic.distance_m, domestic.township_distance_m) if d is not None
        )
        assert foreign.distance_m is not None
        return domestic if domestic_distance <= foreign.distance_m else foreign

    def _resolve_from(
        self,
        gps: GpsPoint,
        townships: _PointIndex | None,
        localities: _PointIndex,
    ) -> NearestPlace | None:
        township_hit = townships.nearest_within(gps, self._max_radius_m) if townships else None
        locality_hit = localities.nearest_within(gps, self._max_radius_m)
        if township_hit is None and locality_hit is None:
            return None
        township = township_hit[0].name if township_hit else ""
        locality = locality_hit[0].name if locality_hit else ""
        # 距离取**实际给出 locality 的那个点**：展示层写的是「大石桥村附近」，
        # 那个「附近」说的是村庄的距离，不是乡镇驻地的。
        distance = locality_hit[1] if locality_hit else (township_hit[1] if township_hit else None)
        if township and locality and township == locality:
            locality = ""
        return NearestPlace(
            township=township,
            locality=locality,
            distance_m=distance,
            township_distance_m=township_hit[1] if township_hit else None,
            township_country=township_hit[0].country if township_hit else "",
            locality_country=locality_hit[0].country if locality_hit else "",
            township_admin_codes=township_hit[0].admin_codes if township_hit else (),
            locality_admin_codes=locality_hit[0].admin_codes if locality_hit else (),
        )

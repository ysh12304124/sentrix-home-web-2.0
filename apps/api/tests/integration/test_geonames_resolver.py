"""GeoNames 最近邻适配器：合成 dump 下的解析、分池、半径上限、降级。

用合成数据而不是真 dump：真 dump 有 96 万行、几十 MB，进 `make ci` 会让每次运行
多花十几秒去解析一个与断言无关的文件。合成数据里每一行都是为了断言某个行为而写的。

真数据的行为在规划阶段单独实测过，记在 `docs/refactor/2026-09-22-s4-implementation-plan.md`
的「环境事实」与 Task 48。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.places.geonames import (
    HOME_COUNTRY,
    MAX_NEAREST_RADIUS_M,
    GeoNamesNearestResolver,
    parse_dump,
)


# GeoNames 的 19 列制表符格式。列位：0 geonameid · 1 name · 4 lat · 5 lon ·
# 6 feature class · 7 feature code · 8 country code · 14 population。
def row(
    geoname_id: int,
    name: str,
    latitude: float,
    longitude: float,
    feature_code: str,
    country: str,
    population: int = 0,
    alternates: str = "",
) -> str:
    columns = [""] * 19
    columns[0] = str(geoname_id)
    columns[1] = name
    columns[2] = name
    columns[3] = alternates
    columns[4] = str(latitude)
    columns[5] = str(longitude)
    columns[6] = feature_code[0]
    columns[7] = feature_code
    columns[8] = country
    columns[9] = ""
    columns[10] = "01"
    columns[11] = ""
    columns[12] = ""
    columns[13] = ""
    columns[14] = str(population)
    return "\t".join(columns)


# 萧山（30.1839, 120.2642）周边：一个乡镇 + 一个中文村庄 + 一个更近的罗马字城市名。
DUMP = "\n".join(
    [
        row(1, "赵州镇", 30.1840, 120.2643, "ADM4", "CN"),
        row(2, "大石桥村", 30.1841, 120.2644, "PPL", "CN"),
        row(3, "Xiaoshan", 30.1838, 120.2641, "PPLA2", "CN"),
        row(4, "Chiang Mai", 18.7883, 98.9853, "PPLA", "TH"),
        row(5, "Eifuku", 35.6762, 139.6503, "PPL", "JP"),
        # 与萧山同一位置的外国点：分池生效时它必须输给国内池的任意命中。
        row(6, "Nowhere", 30.1839, 120.2642, "PPL", "US"),
        # 特征码不在采集范围内：不该进任何池子。
        row(7, "Mount Something", 30.1839, 120.2642, "MT", "CN"),
        # 列数不足的截断行：直接跳过。
        "123\tBroken",
    ]
)


@pytest.fixture
def geo_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "geo"
    directory.mkdir()
    (directory / "synthetic.txt").write_text(DUMP, encoding="utf-8")
    return directory


def test_parse_dump_prefers_the_chinese_alternate_name():
    """GeoNames 中国记录的 ``name`` 列是罗马字，中文名在 ``alternatenames`` 列。

    实测三行：

        ADM4  name='Mei Xian'     alts='Mei Hsien,Mei Xian,Meixian Shi,mei xian,梅县'
        PPL   name='Nyêngbo'      alts='Ningbu,ning bu,རུ་པ,སྙེང་པོ,宁布'
        PPL   name='Changshousi'  alts='Changshousi,zhang shou si,长寿寺'
    """
    dump = "\n".join(
        [
            row(11, "Mei Xian", 24.28, 116.12, "ADM4", "CN", alternates="Mei Hsien,mei xian,梅县"),
            row(12, "赵州镇", 37.75, 114.77, "ADM4", "CN"),
            row(13, "Somewhere", 1.0, 1.0, "PPL", "US"),
        ]
    )
    names = {point.name for point in parse_dump(dump)}

    assert "梅县" in names  # 罗马字 name → 取备选里的中文
    assert "Mei Xian" not in names
    assert "赵州镇" in names  # name 本身是中文 → 原样保留
    assert "Somewhere" in names  # 没有中文备选 → 退回罗马字，不丢点


def test_parse_dump_keeps_only_townships_and_localities():
    points = parse_dump(DUMP)
    names = {point.name for point in points}
    assert "赵州镇" in names
    assert "大石桥村" in names
    assert "Mount Something" not in names  # MT 不在采集范围内
    assert "Broken" not in names  # 列数不足


def test_resolver_reads_txt_and_zip(tmp_path: Path):
    directory = tmp_path / "geo"
    directory.mkdir()
    with zipfile.ZipFile(directory / "packed.zip", "w") as archive:
        archive.writestr("packed.txt", DUMP)
    resolver = GeoNamesNearestResolver(directory)

    assert resolver.available is True
    assert resolver.resolve_nearest(GpsPoint(18.7883, 98.9853)) is not None


def test_zip_member_picker_skips_the_readme(tmp_path: Path):
    """官方 dump 包里除数据文件外还有一个 ``readme.txt``。

    实测 ``CN.zip`` 的成员是 ``readme.txt``（8,843 B）与 ``CN.txt``（132,055,747 B），
    而 ``namelist()`` 里 readme **排在前面**。按顺序取第一个会解析出 0 个点、
    不报错、整个国内最近邻静默失效——只有拿真数据端到端跑一次才看得见。
    """
    directory = tmp_path / "geo"
    directory.mkdir()
    archive_path = directory / "CN.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "this file is a readme, not a dump\n")
        archive.writestr("CN.txt", DUMP)
    resolver = GeoNamesNearestResolver(directory)

    # 同名成员优先：CN.zip → CN.txt。计数 (乡镇, 国内, 国外) = (赵州镇, 大石桥村+Xiaoshan,
    # Chiang Mai + Eifuku + Nowhere)。
    assert resolver.counts == (1, 2, 3)
    assert resolver.resolve_nearest(GpsPoint(18.7883, 98.9853)) is not None


def test_zip_member_picker_falls_back_to_the_largest_txt(tmp_path: Path):
    directory = tmp_path / "geo"
    directory.mkdir()
    with zipfile.ZipFile(directory / "unrelated.zip", "w") as archive:
        archive.writestr("readme.txt", "short\n")
        archive.writestr("data_dump.txt", DUMP)
    resolver = GeoNamesNearestResolver(directory)

    assert resolver.available is True
    assert resolver.resolve_nearest(GpsPoint(18.7883, 98.9853)) is not None


def test_closer_foreign_point_wins_over_domestic_pool(geo_dir: Path):
    resolver = GeoNamesNearestResolver(geo_dir)
    nearest = resolver.resolve_nearest(GpsPoint(30.1839, 120.2642))
    assert nearest is not None
    assert nearest.locality == "Nowhere"
    assert nearest.locality_country == "US"
    assert nearest.township == ""


def test_nearest_is_chosen_by_distance_within_the_domestic_pool(geo_dir: Path):
    resolver = GeoNamesNearestResolver(geo_dir)

    nearest = resolver.resolve_nearest(GpsPoint(30.1841, 120.2644))

    assert nearest is not None
    # ``Xiaoshan`` 是 PPLA2、``大石桥村`` 稍远一点点；按距离取最近的，不分特征码优先级。
    assert nearest.locality in {"Xiaoshan", "大石桥村"}
    assert nearest.distance_m is not None
    assert nearest.distance_m < 50.0


def test_foreign_coordinate_falls_through_to_the_world_pool(geo_dir: Path):
    resolver = GeoNamesNearestResolver(geo_dir)

    chiang_mai = resolver.resolve_nearest(GpsPoint(18.7883, 98.9853))
    tokyo = resolver.resolve_nearest(GpsPoint(35.6762, 139.6503))

    assert chiang_mai is not None and chiang_mai.locality == "Chiang Mai"
    assert tokyo is not None and tokyo.locality == "Eifuku"


def test_distance_is_measured_to_the_point_that_supplied_the_locality(geo_dir: Path):
    resolver = GeoNamesNearestResolver(geo_dir)
    nearest = resolver.resolve_nearest(GpsPoint(30.1839, 120.2642))
    assert nearest is not None and nearest.distance_m is not None
    # 合成点的最近距离在几十米量级，不是公里量级。
    assert nearest.distance_m < 1000.0


def test_pacific_coordinate_returns_none(geo_dir: Path):
    """半径上限：没有任何点落在 50 公里内时返回 None，而不是编一个三千公里外的。"""
    resolver = GeoNamesNearestResolver(geo_dir)
    assert resolver.resolve_nearest(GpsPoint(0.0, -160.0)) is None


def test_radius_can_be_tightened(geo_dir: Path):
    """半径收紧到 100 米之后，约 1.6 公里外的清迈落在圈外。

    查询坐标刻意**不与任何合成点重合**：距离 0 的点在任何正半径内都成立，测不出收紧。
    """
    resolver = GeoNamesNearestResolver(geo_dir, max_radius_m=100.0)
    assert resolver.resolve_nearest(GpsPoint(18.7883, 99.0)) is None


def test_missing_directory_degrades_to_unavailable(tmp_path: Path):
    resolver = GeoNamesNearestResolver(tmp_path / "nope")
    assert resolver.available is False
    assert resolver.resolve_nearest(GpsPoint(30.0, 120.0)) is None
    assert resolver.counts == (0, 0, 0)


def test_no_directory_at_all_degrades_to_unavailable():
    resolver = GeoNamesNearestResolver(None)
    assert resolver.available is False
    assert resolver.resolve_nearest(GpsPoint(30.0, 120.0)) is None


def test_corrupt_zip_is_skipped_without_raising(tmp_path: Path):
    directory = tmp_path / "geo"
    directory.mkdir()
    (directory / "broken.zip").write_bytes(b"not a zip at all")
    resolver = GeoNamesNearestResolver(directory)
    assert resolver.available is False
    assert resolver.resolve_nearest(GpsPoint(30.0, 120.0)) is None


def test_constants_are_the_documented_values():
    assert HOME_COUNTRY == "CN"
    assert MAX_NEAREST_RADIUS_M == 50_000.0


def test_border_uses_nearer_country_and_foreign_adm4_is_not_domestic(tmp_path: Path):
    (tmp_path / "border.txt").write_text(
        "\n".join(
            [
                row(10, "中国居民点", 22.05, 100.0, "PPL", "CN"),
                row(11, "Laos village", 22.0, 100.0, "PPL", "LA"),
                row(12, "Laos admin", 22.0, 100.0, "ADM4", "LA"),
            ]
        )
    )
    resolver = GeoNamesNearestResolver(tmp_path)
    nearest = resolver.resolve_nearest(GpsPoint(22.0, 100.0))
    assert nearest is not None and nearest.locality_country == "LA"
    assert nearest.township == ""
    assert resolver.counts == (0, 1, 1)


def test_resolver_streams_zip_without_materializing_dump(tmp_path: Path, monkeypatch):
    import io

    from sentrix.infrastructure.places import geonames

    class LinesOnly(io.StringIO):
        def read(self, *args, **kwargs):
            raise AssertionError("dump must be iterated line by line")

    monkeypatch.setattr(geonames, "_iter_dump_lines", lambda path: LinesOnly(DUMP))
    (tmp_path / "source.txt").touch()
    assert GeoNamesNearestResolver(tmp_path).counts == (1, 2, 3)

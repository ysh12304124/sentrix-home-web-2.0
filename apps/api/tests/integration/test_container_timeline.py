"""S4 的装配：容器上那六个新端口真的是能用的适配器，不是占位符。

``test_container_wiring.py`` 管的是「字段按端口声明」；这条管的是「装上去的东西真的
能跑」——两条都必要：前者保证调用方在类型上只看得见协议，后者保证协议后面有实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import GpsPoint
from sentrix.domain.media.ports import ObservationSemantics
from sentrix.domain.places.models import AdminArea, Place, PlacePrecision, PlaceSource

pytestmark = pytest.mark.usefixtures("fake_embedding_startup")

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
PACIFIC = GpsPoint(0.0, -160.0)


def build(tmp_path, monkeypatch) -> Container:
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    from sentrix.config import Settings

    container = Container.build(Settings())
    container.database.migrate()
    return container


def test_event_and_fact_repositories_round_trip(tmp_path, monkeypatch):
    container = build(tmp_path, monkeypatch)

    event = container.event_repository.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="浙江省杭州市萧山区",
        admin_district="萧山区",
        activity="拍照",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )

    assert container.event_repository.get("evt_1") == event
    assert container.fact_repository.list_facts("album1", limit=10) == []


def test_lexical_index_and_search_are_two_sides_of_one_projection(tmp_path, monkeypatch):
    container = build(tmp_path, monkeypatch)
    container.asset_repository.add(_asset())
    observation = _observation()
    container.observation_repository.add(observation)
    container.observation_repository.enrich(
        observation.id, semantics=ObservationSemantics(caption="在海边拍照", place="海边")
    )
    enriched = container.observation_repository.get(observation.id)
    assert enriched is not None

    written = container.lexical_index.refresh_from_observation(enriched)

    assert written > 0
    assert dict(container.lexical_search.search_fts("海边", scope_id="album1", limit=10)) == {"asset_1": 1}


def test_place_repository_is_wired(tmp_path, monkeypatch):
    container = build(tmp_path, monkeypatch)

    assert container.asset_place_repository.get("asset_1") is None
    assert container.asset_place_repository.count() == 0


def test_place_resolver_is_offline_and_returns_none_outside_coverage(tmp_path, monkeypatch):
    """太平洋正中：面与点两个来源都没有覆盖，必须返回 ``None``。

    这一条同时验证了两件事：PyGeoCN 装好了（真反编码，约 0.3 秒），以及
    GeoNames 数据缺失时最近邻那一路**降级而不是抛异常**。
    """
    container = build(tmp_path, monkeypatch)

    assert container.place_resolver.resolve(PACIFIC) is None


def test_place_aliases_come_from_the_packaged_data_file(tmp_path, monkeypatch):
    """别名表随包发布，不是运行期数据目录里的可选文件——空表意味着中英桥接失效。"""
    container = build(tmp_path, monkeypatch)

    assert container.place_aliases.table, "包内别名表没被读进来"
    assert container.place_aliases.names_for("清迈")


def _asset():
    from sentrix.domain.media.models import Asset, AssetStatus, MediaType

    return Asset(
        id="asset_1",
        scope_id="album1",
        file_name="a.jpg",
        media_type=MediaType.IMAGE,
        path="/tmp/a.jpg",
        mime_type="image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256="sha-1",
        captured_at=NOW,
        captured_location=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _observation():
    from sentrix.domain.media.models import Observation

    return Observation(
        id="obs_1",
        scope_id="album1",
        asset_id="asset_1",
        source_type="import",
        captured_at=NOW,
        confidence=0.5,
        created_at=NOW,
        updated_at=NOW,
    )


def test_place_display_is_the_contract_for_the_web_card():
    """展示形式这一条是接口契约的一部分，所以在这里钉一次（前端不做拼接）。"""
    place = Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=_nearest(),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
    )

    assert place.display() == "浙江省杭州市萧山区 · 赵州镇 / 大石桥村附近"


def _nearest():
    from sentrix.domain.places.models import NearestPlace

    return NearestPlace("赵州镇", "大石桥村", 270.0)

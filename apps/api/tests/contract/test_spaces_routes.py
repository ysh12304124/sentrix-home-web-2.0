"""``GET /api/v1/memory-spaces`` 的契约。

**这是 2.0 补的一个缺口**：1.0 有 ``/api/memory-spaces``（``src/api.js:17``，
侧边栏的相册切换器靠它），而 2.0 的 domain 端口与仓储都在、容器字段也在，
只差这一层 HTTP。

返回体的形状按 **1.0 前端的消费处** 定（``src/app.js:1161``
``spaceResult[0].value.spaces || []``）：``{"spaces": [...]}``，不是裸数组。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sentrix.config import Settings
from sentrix.main import create_app


def test_empty_database_is_an_empty_list_not_a_404(settings: Settings) -> None:
    """空库是合法状态（2.0 就是空库起步）。缺的是数据，不是端点。"""
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/memory-spaces")

    assert response.status_code == 200
    assert response.json() == {"spaces": []}


def test_every_space_carries_the_columns_its_readers_need(settings: Settings) -> None:
    """``kind`` 与 ``created_at`` 各有真实读者，都不能少。

    - ``kind``：前端按它决定回落行为（只有 ``benchmark`` 的旧选择被保留，见
      ``ScopeProvider.resolveStoredScope``）。
    - ``created_at``：PhotoBench 按它排「新创建的在前」、挑最新 scope 做复用基座
      （``_build_reuse_bases``）、并当 ``_qa_scope_id_for_album`` 的次级排序键。

    **``created_at`` 那两条断言是刻意写强的。** 少了这一列不会报错，只会让上面那三处
    退化成空串比较——「键存在」这种断言对那个后果毫无区分力。所以第二条断言要求它
    落在「刚刚」这个窗口里：写死一个常量、或回空串，都会红。
    """
    app = create_app(settings)
    app.state.container.spaces.ensure("album1", "第一册", kind="benchmark")
    client = TestClient(app)

    space = client.get("/api/v1/memory-spaces").json()["spaces"][0]

    assert space["id"] == "album1"
    assert space["name"] == "第一册"
    assert space["kind"] == "benchmark"
    assert space["status"] == "active"
    created_at = datetime.fromisoformat(space["created_at"])
    assert created_at.tzinfo is not None
    assert abs((datetime.now(UTC) - created_at).total_seconds()) < 300


def test_benchmark_registration_hides_people_and_survives_import_ensure(client: TestClient) -> None:
    payload = {"id": "test-scope", "name": "Test scope", "kind": "benchmark"}
    first = client.post("/api/v1/memory-spaces", json=payload)
    assert first.status_code == 200
    assert client.post("/api/v1/memory-spaces", json=payload).json() == first.json()
    container = client.app.state.container
    # 导入和种身份仍会调用默认 ensure；不能把预登记的用途和可见性改回来。
    container.spaces.ensure("test-scope", "test-scope")
    with container.database.connect() as connection, connection:
        row = connection.execute(
            "SELECT kind, include_in_people FROM memory_spaces WHERE id='test-scope'"
        ).fetchone()
        assert tuple(row) == ("benchmark", 0)
        connection.execute(
            "INSERT INTO entities (id,scope_id,entity_type,canonical_name,status,created_at,updated_at)"
            " VALUES ('test-person','test-scope','person','测试人物','confirmed',"
            " '2026-11-09T00:00:00+00:00','2026-11-09T00:00:00+00:00')"
        )
    assert client.get("/api/v1/people").json()["people"] == []
    assert client.get("/api/v1/family-graph", params={"scope_id": ""}).json()["people"] == []
    # 测试人物与空间均保留；定向读取人物仍可供评测使用。
    assert container.entity_repository.get("test-person") is not None
    assert container.spaces.get("test-scope").kind == "benchmark"


def test_registration_cannot_reclassify_an_existing_household(client: TestClient) -> None:
    container = client.app.state.container
    container.spaces.ensure("home-default", "家庭")
    response = client.post(
        "/api/v1/memory-spaces", json={"id": "home-default", "name": "test", "kind": "benchmark"}
    )
    assert response.status_code == 409
    assert container.spaces.get("home-default").kind == "household"
    with container.database.connect() as connection:
        assert (
            connection.execute(
                "SELECT include_in_people FROM memory_spaces WHERE id='home-default'"
            ).fetchone()[0]
            == 1
        )

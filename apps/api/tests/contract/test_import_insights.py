"""``POST /api/v1/imports`` 的**批末自动画像**（Task 86i，裁决④）契约测试。

被测的是 ``routes/timeline.py`` 的 ``_maybe_run_insights``——导入批末那一趟人物画像，
以及响应体里 ``insights`` 的形状。这一趟最多几十次模型调用，那道闸的全部意义就是
「没有新照片就别付这个代价」，所以用例要钉的不只是响应长什么样，还有**它到底跑没跑**：
「跑了但把结果丢掉」与「压根没跑」在响应上长得一模一样（都是 ``insights: null``），
只断言响应的话，把调用整个删掉、或改成「跑了再丢」，用例都不会红。

**注入手法**：路由里的两个协作者都是 ``from ... import`` 进来的模块级名字，所以
monkeypatch 必须打在 ``routes.timeline`` 这个模块上，打在 ``routes.insights`` 上不会生效
（那里的是另一个绑定）。

- ``ImportAlbum``：换成一个只回构造好的 ``AlbumImportResult`` 的假件，**不拿真照片驱动**。
  这条链路里真正相关的只有 ``result.imported`` 一个数，而它由去重 + 模型流水线决定；
  让这个文件依赖那整条链，一次无关的导入行为改动（换嵌入器、改去重时机）就会把它弄红。
  批次计数本身在 ``test_timeline_routes.py`` 与 integration 里另有人钉，这里不重复。
- ``run_insights_for_scope``：换成一个记录调用、回固定计数的假件。真件会走模型，契约
  测试验的是**接线与那道闸**，不是流水线。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sentrix.api.routes import timeline as timeline_routes
from sentrix.application.media.import_album import AlbumImportRequest, AlbumImportResult

SCOPE = "album1"
DIRECTORY = "/albums/2026"

# 六个计数键，与 ``InsightCounts`` 一一对应。用例只引用它，不各自手抄一份。
SIX_KEYS = ("ranked", "core", "selections", "moments", "moment_failures", "portraits")

# 普通一趟的计数：六个值互不相同，任何一个键在路由里对调都会红。
COUNTS = {"ranked": 7, "core": 3, "selections": 21, "moments": 5, "moment_failures": 2, "portraits": 4}

# 透传用例专用的一份：与上表取值完全不同，防止路由把某一组计数写死。
PASSTHROUGH = {"ranked": 13, "core": 6, "selections": 41, "moments": 8, "moment_failures": 1, "portraits": 9}


def _batch(
    *,
    imported: int,
    deduplicated: int = 0,
    events: int = 0,
    lexical_terms: int = 0,
    summarized: int = 0,
) -> AlbumImportResult:
    """构造一个批导入结果。字段全给全，好让「路由读了哪个字段」有据可查。"""
    return AlbumImportResult(
        scope_id=SCOPE,
        directory=DIRECTORY,
        imported=imported,
        deduplicated=deduplicated,
        events=events,
        lexical_terms=lexical_terms,
        summarized=summarized,
    )


def _stub_import(monkeypatch: pytest.MonkeyPatch, result: AlbumImportResult) -> list[AlbumImportRequest]:
    """把路由那个 ``ImportAlbum`` 换成只回 ``result`` 的假件，并记录收到的请求。

    走假件而不是「tmp 目录 + 真 JPEG」：这个文件要验的是**批末那一趟跑没跑**，驱动它
    只需要 ``result.imported`` 这一个人造数；真批次得依赖去重语义、观察器与向量库，
    耦合进来只会让这条用例因为无关的导入改动而红。
    """
    requests: list[AlbumImportRequest] = []

    class _StubImportAlbum:
        def __init__(
            self,
            *,
            importer: object,
            summarize_event: object = None,
            workers: int = 1,
            progress: object = None,
            cancel_check: object = None,
        ) -> None:
            self._importer = importer
            self._summarize_event = summarize_event
            # 并发宽度与这条契约无关：这一组测的是批次跑完之后那一段 insights 收尾。
            self._workers = workers

        def execute(self, request: AlbumImportRequest) -> AlbumImportResult:
            requests.append(request)
            return result

    monkeypatch.setattr(timeline_routes, "ImportAlbum", _StubImportAlbum)
    return requests


def _stub_insights(
    monkeypatch: pytest.MonkeyPatch,
    *,
    counts: dict[str, int] | None = None,
    error: Exception | None = None,
) -> list[str]:
    """替掉路由调用的 ``run_insights_for_scope``，记录它被调过几次、用的是哪个 scope。

    **记录调用是这套用例的一半**：只断言 ``insights is None`` 的话，「跑了然后把结果
    丢掉」与「压根没跑」都会绿，而那道闸省下来的正是那几十次模型调用。
    """
    calls: list[str] = []

    def _fake(container: object, scope_id: str) -> dict[str, int]:
        calls.append(scope_id)
        if error is not None:
            raise error
        return dict(counts or {})

    monkeypatch.setattr(timeline_routes, "run_insights_for_scope", _fake)
    return calls


def _import(client: TestClient) -> dict:
    response = client.post("/api/v1/imports", json={"directory": DIRECTORY, "scope_id": SCOPE})
    assert response.status_code == 201
    return response.json()


def test_a_batch_with_new_photos_runs_insights_and_returns_the_six_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有导入的批要真跑一趟，响应里的 ``insights`` 是那六个键、值与假件一致。

    这一批刻意 ``events=0``：判据取 ``imported`` 而不是 ``events``（新照片可能还没形成
    事件，但人可能是新的），所以「有 imported 就不该因为 events 为 0 而跳过」必须红一次。
    """
    _stub_import(monkeypatch, _batch(imported=2, events=0, lexical_terms=4))
    calls = _stub_insights(monkeypatch, counts=COUNTS)

    body = _import(client)

    assert body["imported"] == 2
    assert body["insights"] == COUNTS
    assert set(body["insights"]) == set(SIX_KEYS)
    assert calls == [SCOPE]


def test_a_fully_deduplicated_batch_never_runs_insights(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整批都被去重（``imported == 0``）时，画像**一次都没跑**——响应与调用两半都要钉。

    ``insights is None`` 只说明「没结果」，说明不了「没跑」：把实现改成「照跑一趟再
    把结果丢掉」，响应照样是 ``None``，而模型调用一次不少——那道闸的意义就没了。
    所以 ``calls == []`` 是这条用例真正的判据。

    ``deduplicated=3`` 也是有意的：判据是 ``imported``，不是「这一批有几个文件」。
    若实现改读 ``deduplicated`` / ``scanned``，这里就会跑起来而红。
    """
    _stub_import(monkeypatch, _batch(imported=0, deduplicated=3, events=0))
    calls = _stub_insights(monkeypatch, counts=COUNTS)

    body = _import(client)

    assert body["imported"] == 0
    assert body["deduplicated"] == 3
    assert body["insights"] is None
    assert calls == [], "没有新照片时不该有任何模型调用"


def test_a_run_that_recognized_nobody_is_all_zeros_not_null(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跑了但一个人都没认出来 → 全 0 的对象，**不是** ``null``。

    这条与「没跑」是一对：``null`` 的意思是「没跑」，全 0 的意思是「跑了，没结果」，
    排查时是完全不同的结论。任何「计数全 0 就当没跑、回 None」的简化实现都会在这里红。
    """
    _stub_import(monkeypatch, _batch(imported=1))
    calls = _stub_insights(monkeypatch, counts={key: 0 for key in SIX_KEYS})

    body = _import(client)

    assert calls == [SCOPE], "有导入就一定要跑"
    assert body["insights"] is not None, "跑了没结果是全 0 对象，不是 null"
    assert body["insights"] == {key: 0 for key in SIX_KEYS}


def test_a_failing_insights_run_fails_the_whole_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画像那一趟抛异常 → 整个请求 500，**不被吞**（``import_album`` 的 docstring）。

    不吞是有意的：吞掉之后「画像从来没生成过」会变成一个没人看得见的状态。代价是照片
    其实已经入库了——``ImportAlbum`` 那一步在画像之前就跑完并落库，异常只发生在它之后，
    所以重跑同一批会被去重兜住（``deduplicated`` 那一列）。这里用 ``requests == 1`` 钉住
    「导入那一步确实走完了」，而不是断言库里的行（这个文件不拿真照片驱动，见模块 docstring）。

    用 ``raise_server_exceptions=False`` 的 client 才看得到 500：默认的 TestClient 会把
    服务端异常直接抛给测试，那样断言不到状态码。
    """
    requests = _stub_import(monkeypatch, _batch(imported=1))
    _stub_insights(monkeypatch, error=RuntimeError("画像流水线炸了"))

    loud = TestClient(client.app, raise_server_exceptions=False)
    response = loud.post("/api/v1/imports", json={"directory": DIRECTORY, "scope_id": SCOPE})

    assert response.status_code == 500
    assert len(requests) == 1, "导入那一步已经走完，异常发生在它之后"


def test_the_six_counts_are_passed_through_verbatim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """六个计数是**透传**，不是路由自己编的：六个互不相同的值原样出现在响应里。

    用另一组取值（``PASSTHROUGH``）、并且六个两两不同：路由里任何一个键写错来源、
    写死、或对调两个键，这里都只差一个数——所以六个要逐个对拍，不能只挑两个看。
    """
    assert len(set(PASSTHROUGH.values())) == 6, "六个值必须互不相同，否则对调两个键也看不出来"
    _stub_import(monkeypatch, _batch(imported=1))
    calls = _stub_insights(monkeypatch, counts=PASSTHROUGH)

    body = _import(client)

    insights = body["insights"]
    assert insights is not None
    assert sorted(insights) == sorted(SIX_KEYS)
    for key in SIX_KEYS:
        assert insights[key] == PASSTHROUGH[key], f"{key} 透传错了"
    assert calls == [SCOPE]


def test_cancelled_import_retains_progress_and_skips_batch_insights(client, monkeypatch):
    from sentrix.domain.media.progress import OperationCancelled

    calls = _stub_insights(monkeypatch, counts=COUNTS)

    class CancelledAlbum:
        def __init__(self, **kwargs):
            self.progress = kwargs["progress"]

        def execute(self, request):
            self.progress(completed=1)
            raise OperationCancelled("test cancellation")

    monkeypatch.setattr(timeline_routes, "ImportAlbum", CancelledAlbum)
    response = client.post(
        "/api/v1/imports", json={"directory": DIRECTORY, "scope_id": SCOPE, "operation_id": "cancelled-batch"}
    )
    assert response.status_code == 409
    progress = client.get("/api/v1/imports/cancelled-batch/progress").json()
    assert progress["status"] == "cancelled"
    assert progress["completed"] == 1
    assert calls == []
    assert client.post("/api/v1/imports/cancelled-batch/cancel").json()["cleanup_status"] == "stopped"

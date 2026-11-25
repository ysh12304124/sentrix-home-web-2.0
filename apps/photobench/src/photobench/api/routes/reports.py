"""三份报告的路由（1.0 ``do_GET`` 里 ``/api/runs/{id}/{keyframe-analysis,memory-effectiveness,
export-trace,export-sft}`` 那四条，``:7724-7819``）。

**两条计算路由与两条下载路由混在一个模块里**，因为它们读同一份 run、由同一个用例类
提供，1.0 里也是挨着的连续四个分支。分开挂只会让「这个 run 有哪些报告」散成两处。

``export-*`` 两条**返回的是下载响应而不是 JSON**（``Content-Disposition: attachment``，
前端用 ``window.open`` 触发）。形状见 ``_download`` 的 docstring。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from photobench.api.deps import get_container
from photobench.domain.runs import exports
from photobench.domain.runs.stream_exports import buffered_chunks

router = APIRouter(tags=["reports"])


@router.get("/runs/{run_id}/video-comparison")
def video_comparison(request: Request, run_id: str) -> dict[str, Any]:
    return get_container(request).reports.video_comparison(run_id)


@router.get("/runs/{run_id}/keyframe-analysis")
def keyframe_analysis(request: Request, run_id: str) -> dict[str, Any]:
    """1.0 ``:7724-7727``。"""
    return get_container(request).reports.keyframe_analysis(run_id)


@router.get("/runs/{run_id}/memory-effectiveness")
def memory_effectiveness(request: Request, run_id: str) -> dict[str, Any]:
    """1.0 ``:7728-7731``。"""
    return get_container(request).reports.memory_effectiveness(run_id)


@router.get("/runs/{run_id}/graph-quality")
def graph_quality(request: Request, run_id: str) -> dict[str, Any]:
    """可验边、人脸身份聚类和分题型图 QA 的只读汇总。"""

    return get_container(request).reports.graph_quality(run_id)


@router.get("/runs/{run_id}/export-trace")
def export_trace(
    request: Request,
    run_id: str,
    scores: str = "",
    min_score: str = "",
    scoring_profile: str = "",
    scoring_batch: str = "",
) -> Response:
    """1.0 ``:7789-7803``。``scores`` 与 ``min_score`` 同时生效（1.0 是两个独立的 ``if``）。"""
    payload = get_container(request).reports.stream_export(
        run_id,
        scoring_profile=scoring_profile,
        scoring_batch=scoring_batch,
        scores=exports.parse_filter(scores) or None,
        min_score=exports.parse_min_score(min_score),
    )
    return StreamingResponse(
        buffered_chunks(payload),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-trace.json"'},
    )


@router.get("/runs/{run_id}/export-sft")
def export_sft(
    request: Request,
    run_id: str,
    scores: str = "",
    min_score: str = "",
    scoring_profile: str = "",
    scoring_batch: str = "",
) -> Response:
    """1.0 ``:7804-7819``。**给了 ``scores`` 就忽略 ``min_score``**（1.0 是 ``if/elif``）。"""
    payload = get_container(request).reports.stream_export(
        run_id,
        sft=True,
        scoring_profile=scoring_profile,
        scoring_batch=scoring_batch,
        scores=exports.parse_filter(scores) or None,
        min_score=exports.parse_min_score(min_score),
    )
    return StreamingResponse(
        buffered_chunks(payload),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-sft-traces.json"'},
    )

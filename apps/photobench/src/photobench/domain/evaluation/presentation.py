"""Portable, escaped rendering of versioned per-question scores."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Any


def evaluation_html(result: Mapping[str, Any] | None, definition: Mapping[str, Any] | None = None) -> str:
    if not result or not result.get("scorer"):
        return ""

    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    data = {"evaluation": definition, "result": result}
    return (
        "<section><h4>题型评分："
        + esc(result["scorer"])
        + "</h4><p>原分 "
        + esc(result.get("score"))
        + "/"
        + esc(result.get("max_score"))
        + " · 归一化 "
        + esc(result.get("normalized_score"))
        + " · "
        + esc(result.get("judge_status"))
        + "</p><p>"
        + esc(result.get("reason", ""))
        + "</p><details><summary>选项比较、集合指标及辅分依据</summary><pre>"
        + esc(json.dumps(data, ensure_ascii=False, indent=2))
        + "</pre></details></section>"
    )


def evaluation_summary_html(summary: Mapping[str, Any]) -> str:
    if not summary.get("count"):
        return ""

    def pct(value: Any) -> str:
        return f"{value:.1%}" if type(value) in (int, float) else "未评分"

    names = {
        "choice": "选择题",
        "media_list": "媒体列表",
        "open": "开放回答",
        "number": "ATM数值题",
        "list_recall": "ATM列表题",
        "open_end": "ATM开放题",
    }
    rows = "".join(
        f"<tr><td>{html.escape(names.get(key, key))}</td>"
        f"<td>{row['scored']} / {row['count']}</td><td>{pct(row['mean'])}</td></tr>"
        for key, row in (summary.get("by_type") or {}).items()
    )
    return (
        "<section><h3>题型评分汇总</h3>"
        f"<p>新版评分覆盖 {summary.get('scored')} / {summary.get('count')} 题"
        f" · 归一化平均分 {pct(summary.get('mean'))}"
        f" · 其他 {summary.get('other_count', 0)} 题</p>"
        "<p>评分覆盖不全时不作为完整 QS。ATM 主集与 hard 分别统计；累计集合召回与有序 Recall@k 分开。</p>"
        "<table><thead><tr><th>题型</th><th>有效 / 总数</th><th>平均归一化分</th></tr></thead><tbody>"
        + rows
        + "</tbody></table><details><summary>集合平均指标与数值辅分差异</summary><pre>"
        + html.escape(json.dumps(dict(summary), ensure_ascii=False, indent=2))
        + "</pre></details></section>"
    )

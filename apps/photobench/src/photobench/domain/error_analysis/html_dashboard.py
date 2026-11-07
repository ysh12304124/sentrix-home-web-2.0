"""Offline charts derived from saved analysis facts, with explicit sample denominators."""

from __future__ import annotations

import html
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from photobench.domain.evaluation.profiles import ATM, OPEN

COLORS = ("#248278", "#cc942f", "#c25e70", "#96a2b4", "#5576b4")
UNLABELED = {"unknown", "pending_review", "not_applicable", ""}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def rate(numerator: float, denominator: float) -> str:
    return f"{numerator / denominator:.1%}" if denominator else "未覆盖"


def card(title: str, body: str, note: str = "") -> str:
    return (
        f'<article class="chart-card"><h3>{esc(title)}</h3>'
        + (f'<p class="note">{esc(note)}</p>' if note else "")
        + body
        + "</article>"
    )


def jump(text: str, dimension: str, value: str) -> str:
    return (
        f'<button class="chart-link" data-dimension="{esc(dimension)}" '
        f'data-value="{esc(value)}">{esc(text)}</button>'
    )


def donut(title: str, slices: Sequence[tuple[str, int, str]], unit: str, dimension: str = "") -> str:
    total = sum(n for _, n, _ in slices)
    if not total:
        return '<p class="empty">当前范围未记录。</p>'
    segments, legend = [], []
    start = 0.0
    for i, (name, count, value) in enumerate(slices):
        end = start + count / total * 100
        color = (
            {"normal": COLORS[0], "recovered": COLORS[4], "failed": COLORS[2]}.get(value, COLORS[3])
            if dimension == "execution"
            else COLORS[i % len(COLORS)]
        )
        segments.append(f"{color} {start:.5f}% {end:.5f}%")
        label = jump(name, dimension, value) if dimension else esc(name)
        legend.append(
            f'<li><i style="background:{color}"></i><span>{label}</span>'
            f"<b>{count:,}</b><small>{rate(count, total)}</small></li>"
        )
        start = end
    description = title + "：" + "，".join(f"{name}{n}{unit}" for name, n, _ in slices)
    return (
        '<div class="donut-layout"><div class="donut" role="img" '
        f'aria-label="{esc(description)}" style="background:conic-gradient({",".join(segments)})">'
        f"<div><strong>{total:,}</strong><small>{esc(unit)}</small></div></div>"
        f'<ul class="chart-legend">{"".join(legend)}</ul></div>'
    )


def quality_chart(
    rows: Sequence[dict[str, Any]], labels: Mapping[str, str], unit: str, dimension: str
) -> str:
    """One row per membership; never turn overlapping tags into pie slices."""
    known = [row for row in rows if row["value"] not in UNLABELED and row["total"]]
    if not known:
        return '<p class="empty">保存记录中没有有效分类标签，详见数据覆盖。</p>'
    body = '<div class="quality-chart"><div class="chart-columns"><span>类别 / 样本量</span>'
    body += "<span>得分构成</span><span>满分率</span></div>"
    for row in sorted(known, key=lambda row: -row["total"]):
        total = row["total"]
        valid = total - row.get("score_None", 0)
        name = labels.get(row["value"], row["value"])
        counts = [row.get("score_" + score, 0) for score in ("2", "1", "0", "None")]
        description = "，".join(
            f"{label}{n}"
            for label, n in zip(("正确", "部分正确或集合未满分", "错误", "缺分"), counts, strict=True)
            if n
        )
        body += (
            '<div class="quality-row"><div>'
            + jump(name, dimension, row["value"])
            + f"<small>{total:,}{unit} · 有效评分{valid:,}</small></div>"
            + '<div class="score-track" role="img" '
            + f'aria-label="{description}">'
            + "".join(
                f'<span style="width:{n / total * 100:.4f}%;background:{COLORS[i]}"></span>'
                for i, n in enumerate(counts)
            )
            + f"</div><strong>{rate(counts[0], valid)}</strong></div>"
        )
    return body + "</div>"


def selected_turns(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        t for t in case["turns"] if case.get("matched_turns") is None or t["turn"] in case["matched_turns"]
    ]


def score_labels(cases: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Saved score categories; typed scales keep their original score in details."""
    typed = any(case.get("evaluation") or case.get("evaluation_result") for case in cases)
    if not typed:
        return {"0": "0分", "1": "1分", "2": "2分", "None": "未评分"}
    records = [record for case in cases for record in [case, *case.get("turns", [])]]
    partial = any(
        not record.get("evaluation_result")
        or record["evaluation_result"].get("max_score") == 2
        or record["evaluation_result"].get("scorer") in {"media_set", "atm_list"}
        or record.get("score") == 1
        for record in records
    )
    labels = {"0": "错误", "1": "部分正确／集合未满分", "2": "正确", "None": "未评分"}
    return {key: value for key, value in labels.items() if key != "1" or partial}


def retrieval_quality_counts(cases: Sequence[dict[str, Any]], channel: str) -> dict[str, Any]:
    """Pair saved QA-level media counts with that QA's primary quality bucket."""
    cells: Counter[tuple[bool, int]] = Counter()
    excluded: Counter[str] = Counter()
    for case in cases:
        evaluation = case.get("evaluation") or {}
        turns = case.get("turns") or []
        primary_turn = evaluation.get("applies_to_turn", len(turns) - 1)
        if case.get("matched_turns") is not None and primary_turn not in case["matched_turns"]:
            excluded["主评分轮不在筛选范围"] += 1
            continue
        row = case.get("retrieval_counts", {}).get(channel, {})
        if (
            evaluation.get("media_metrics_policy") == "exclude"
            or row.get("status") == "not_applicable"
            or set(case.get("dimension_labels", {}).get("answerability", []))
            & {"unanswerable", "insufficient_evidence"}
        ):
            excluded["媒体指标不适用"] += 1
            continue
        values = [row.get(key) for key in ("matched", "predicted", "gt")]
        if (
            not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values)
            or row["gt"] <= 0
            or row["matched"] > min(row["gt"], row["predicted"])
            or row.get("available") is False
        ):
            excluded["缺少有效媒体计数或GT"] += 1
            continue
        score = case.get("score")
        if type(score) is not int or score not in (0, 1, 2):
            excluded["缺少有效主评分"] += 1
            continue
        cells[row["matched"] > 0, score] += 1
    return {"cells": cells, "valid": sum(cells.values()), "excluded": excluded}


def retrieval_quality_card(counts: Mapping[str, Any], channel: str, *, partial: bool) -> str:
    title = (
        ("候选检索" if channel == "candidate" else "最终交付")
        + " × "
        + ("含部分正确率" if partial else "满分率")
    )
    total = counts["valid"]
    excluded = "；".join(f"{name}{n}题" for name, n in counts["excluded"].items()) or "无"
    note = f"按题级主评分统计 · 有效{total}题；排除：{excluded}。"
    if not total:
        return card(title, '<p class="empty">没有可配对的有效媒体计数和主评分。</p>', note)
    cells = counts["cells"]
    correct = {
        hit: sum(cells[hit, score] for score in ((1, 2) if partial else (2,))) for hit in (True, False)
    }
    groups = {hit: sum(cells[hit, score] for score in (0, 1, 2)) for hit in (True, False)}
    body = '<div class="retrieval-matrix" role="table" aria-label="' + esc(title) + '">'
    body += '<div role="row" class="matrix-row"><span role="columnheader">主评分 ↓ / 媒体 →</span>'
    body += '<b role="columnheader">命中GT</b><b role="columnheader">未命中GT</b></div>'
    for passed, label in ((False, "未达标"), (True, "正确" if partial else "满分")):
        body += '<div role="row" class="matrix-row"><b role="rowheader">' + label + "</b>"
        for hit in (True, False):
            n = correct[hit] if passed else groups[hit] - correct[hit]
            name = ("命中" if hit else "未命中") + " · " + label
            body += (
                f'<div role="cell" class="matrix-cell {"hit" if hit else "miss"}'
                f' {"correct" if passed else "wrong"}" data-count="{n}" '
                f'aria-label="{name}：{n}题，占有效样本{rate(n, total)}">'
                f"<span>{name}</span><strong>{rate(n, total)}</strong><small>{n} / {total}题</small></div>"
            )
        body += "</div>"
    body += '</div><div class="matrix-conditions">'
    for hit in (True, False):
        body += (
            "<div><span>"
            + ("命中组" if hit else "未命中组")
            + ("正确率" if partial else "满分率")
            + "</span><b>"
            + rate(correct[hit], groups[hit])
            + f"</b><small>{correct[hit]} / {groups[hit]}题</small></div>"
        )
    body += '</div><p class="note">命中指至少1个GT媒体被'
    body += "工具候选集合检索到" if channel == "candidate" else "最终交付"
    body += "；未命中指GT命中数为0。"
    body += "正确指主评分大于0，包含部分正确。" if partial else "满分指主评分达到该量表满分。"
    body += "四格百分比均以有效题数为分母；下方按各组题数计算。"
    body += "按轮筛选时，仅纳入主评分轮在范围内的题。</p>"
    return card(title, body, note)


def retrieval_quality(cases: Sequence[dict[str, Any]], source: Mapping[str, Any]) -> str:
    profile = source.get("scoring_profile") or (source.get("scoring_definition") or {}).get("profile_id")
    results = [case.get("evaluation_result") or {} for case in cases]
    if not profile:
        profiles = {r.get("profile_id") for r in results if r.get("profile_id")}
        if len(profiles) == 1:
            profile = next(iter(profiles))
    native_atm = bool(cases) and all(
        str((case.get("evaluation") or {}).get("scorer", "")).startswith("atm_") for case in cases
    )
    atm = profile == ATM or (profile != OPEN and native_atm)
    counts = {channel: retrieval_quality_counts(cases, channel) for channel in ("candidate", "delivery")}
    return "".join(
        retrieval_quality_card(counts[channel], channel, partial=partial)
        for partial in ((False,) if atm else (False, True))
        for channel in ("candidate", "delivery")
    )


def retrieval(cases: Sequence[dict[str, Any]], source: Mapping[str, Any] | None = None) -> str:
    body = ""
    for channel, title in (("candidate", "候选检索"), ("delivery", "最终交付媒体")):
        rows = []
        for case in cases:
            row = case.get("retrieval_counts", {}).get(channel, {})
            values = [row.get(k) for k in ("matched", "predicted", "gt")]
            if all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values) and row["gt"] > 0:
                rows.append(row)
        matched = sum(row["matched"] for row in rows)
        predicted = sum(row["predicted"] for row in rows)
        gt = sum(row["gt"] for row in rows)
        bars = ""
        for name, numerator, denominator in (("召回率", matched, gt), ("精确率", matched, predicted)):
            width = numerator / denominator * 100 if denominator else 0
            bars += (
                f'<div class="metric-bar"><span>{name}</span><b>{rate(numerator, denominator)}</b>'
                f'<div><i style="width:{min(100, width):.4f}%"></i></div>'
                f"<small>命中 {matched:,} / {'GT' if name == '召回率' else '返回'} "
                f"{denominator:,}</small></div>"
            )
        body += card(
            title,
            bars if rows else '<p class="empty">没有同时记录有效GT与检索计数的题目。</p>',
            f"按题微平均 · 覆盖{len(rows)} / {len(cases)}题；仅纳入已保存且GT大于0的计数。",
        )
    body += retrieval_quality(cases, source or {})
    signals = Counter(signal for case in cases for signal in case.get("signals", []))
    names = {
        "candidate_miss": "候选未命中GT",
        "candidate_hit_not_delivered": "候选命中但未交付",
        "delivered_low_score": "媒体已交付但原分偏低",
    }
    body += card(
        "检索与回答 · 保存的定位线索",
        "".join(
            f'<div class="signal-row"><span>{esc(names.get(key, key))}</span><b>{count}题</b></div>'
            for key, count in signals.most_common()
        )
        or '<p class="empty">当前范围未记录这类定位信号。</p>',
        "按题记录的信号，可重叠；无信号不表示检索正确，不将相关现象解释为因果。",
    )
    tools: Counter[str] = Counter()
    covered = 0
    for case in cases:
        inventory = [
            row
            for row in case.get("tool_inventory", [])
            if case.get("matched_turns") is None or row.get("turn") in case["matched_turns"]
        ]
        covered += bool(inventory)
        tools.update({row["tool_name"] for row in inventory})
    body += card(
        "工具使用 · 涉及题数",
        "".join(
            f'<div class="signal-row"><span>{esc(name)}</span><b>{n}题</b></div>'
            for name, n in tools.most_common(8)
        )
        or '<p class="empty">当前范围没有工具清单记录。</p>',
        f"清单覆盖{covered} / {len(cases)}题，展示前8种工具。同题同工具只计一次，限本次命中轮。",
    )
    return body


def multiturn(cases: Sequence[dict[str, Any]]) -> str:
    changes: Counter[str] = Counter()
    pairs: Counter[tuple[int, int]] = Counter()
    multi = [c for c in cases if len(c["turns"]) > 1]
    for case in multi:
        turns = selected_turns(case)
        if len(turns) != len(case["turns"]):
            changes["范围不完整"] += 1
            continue
        first, last = turns[0].get("score"), turns[-1].get("score")
        if type(first) is not int or type(last) is not int or first not in (0, 1, 2) or last not in (0, 1, 2):
            changes["缺少评分"] += 1
            continue
        pairs[first, last] += 1
        changes["末轮较高" if last > first else "原分相同" if last == first else "末轮较低"] += 1
    body = '<div class="transition-grid" role="table" aria-label="多轮首末质量分对比">'
    labels = score_labels(cases)
    categories = [int(key) for key in labels if key != "None"]
    body = body[:-1] + f' style="grid-template-columns:repeat({len(categories) + 1},1fr)">'
    body += '<span class="note">首轮 ↓ / 末轮 →</span>' + "".join(
        f"<b>{labels[str(key)]}</b>" for key in categories
    )
    for first in categories:
        body += f"<b>{labels[str(first)]}</b>"
        for last in categories:
            n = pairs[first, last]
            color = "#e7f3f0" if last > first else "#fbecef" if last < first else "#edf2f8"
            body += (
                f'<span style="background:{color}" '
                f'title="首轮{labels[str(first)]}，末轮{labels[str(last)]}">{n}</span>'
            )
    body += "</div><p class=note>" + " · ".join(f"{esc(k)} {v}题" for k, v in changes.items()) + "</p>"
    return card(
        "多轮：首轮与末轮的原分变化",
        body if multi else '<p class="empty">当前范围没有多轮样例。</p>',
        f"{len(multi)}道多轮题。首末轮问题可能不同，此图描述已保存分数变化，不作为澄清效果的独立评分。",
    )


def score_composition(
    cases: Sequence[dict[str, Any]], filters: Mapping[str, Any], labels: Mapping[str, str]
) -> str:
    """Show category membership within each saved score; overlapping tags use bars."""
    unit = "轮" if filters.get("content_unit") == "turn" else "题"
    records = (
        [t for c in cases for t in selected_turns(c)]
        if unit == "轮"
        else [dict(c["dimension_labels"], score=c["score"]) for c in cases]
    )
    tabs: list[str] = []
    panels: list[str] = []
    for key, name in score_labels(cases).items():
        score = None if key == "None" else int(key)
        selected = [r for r in records if r.get("score") == score]
        charts = ""
        for dim, title in (
            ("content_type", "内容题型"),
            ("ability_family", "一级能力"),
            ("ability_type", "二级能力"),
        ):
            memberships = [set(r.get(dim) or []) - UNLABELED for r in selected]
            valid = [v for v in memberships if v]
            if not valid:
                continue
            counts = Counter(v for values in valid for v in values)
            rows = counts.most_common()
            note = f"{name}{len(selected)}{unit}，其中{len(valid)}{unit}有{title}标签。"
            if dim == "content_type" and all(len(v) == 1 for v in valid) and len(rows) <= 6:
                chart = donut(title, [(labels.get(k, k), n, k) for k, n in rows], unit, dim)
                note += "每个样本仅一个类别，环图表示类别构成。"
            else:
                chart = ""
                for value, count in rows:
                    chart += '<div class="composition-row"><div>' + jump(labels.get(value, value), dim, value)
                    chart += f"<span>{count}{unit} · {rate(count, len(valid))}</span></div>"
                    chart += '<div class="composition-track">'
                    chart += f'<i style="width:{count / len(valid) * 100:.4f}%"></i></div></div>'
                note += "横条表示该类别占有标签样本的比例；多标签可重叠，比例之和可超过100%。"
            charts += card(title, chart, note)
        if not charts:
            continue
        key = str(score)
        first = not panels
        tabs.append(
            f'<button type="button" role="tab" id="score-tab-{key}" data-composition-tab="{key}" '
            f'aria-controls="score-panel-{key}" aria-selected="{str(first).lower()}">'
            f"{name} · {len(selected)}{unit}</button>"
        )
        panels.append(
            f'<div role="tabpanel" id="score-panel-{key}" aria-labelledby="score-tab-{key}" '
            f'data-composition-score="{key}" class="chart-grid"'
            + ("" if first else " hidden")
            + ">"
            + charts
            + "</div>"
        )
    if not panels:
        return ""
    return (
        '<section id="score-composition"><div class="section-heading"><h2>各得分的题目主要属于哪些类别</h2>'
        '<p class="note">选择得分，看这组样本的类别构成；点击类别查看对应样例。</p></div>'
        '<div class="score-tabs" role="tablist" aria-label="选择得分">'
        + "".join(tabs)
        + "</div>"
        + "".join(panels)
        + "</section>"
    )


def render_charts(
    report: dict[str, Any],
    cases: Sequence[dict[str, Any]],
    filters: Mapping[str, Any],
    labels: Mapping[str, str],
) -> tuple[str, str, str, str]:
    """Return overview, dimensions, retrieval and behavior cards for the export scope."""
    unit = "轮" if filters.get("content_unit") == "turn" else "题"
    prefix = "turn_score_" if unit == "轮" else "score_"
    counts = report["counts"]
    groups = report.get("turn_groups", {}) if unit == "轮" else report["groups"]
    quality = donut(
        "任务质量分布",
        [
            (name, counts.get(prefix + score, 0), score)
            for name, score in (
                ("正确", "2"),
                ("部分正确／集合未满分", "1"),
                ("错误", "0"),
                ("未评分", "None"),
            )
            if score in score_labels(cases)
        ],
        unit,
        "score" if unit == "题" else "",
    )
    execution = Counter(c["execution"] for c in cases)
    overview = card("质量：做得怎么样", quality, f"按{unit}统计；满分率与均分仅使用有效原评分。")
    overview += card(
        "执行：有没有跑完",
        donut(
            "执行结果", [(labels.get(k, k), n, k) for k, n in sorted(execution.items())], "题", "execution"
        ),
        "按题统计。执行终态与任务质量分别呈现。",
    )
    dimensions = ""
    for dim, title in (
        ("ability_family", "一级能力 · 样本量与质量"),
        ("ability_type", "二级能力 · 具体短板"),
        ("benchmark_group", "普通能力与安全场景"),
        ("difficulty", "不同难度的任务表现"),
        ("media_type", "图片、视频与混合媒体"),
        ("conversation_type", "单轮与多轮任务"),
        ("answerability", "不同证据条件下的表现"),
        ("content_type", "内容题型"),
        ("content_subtype", "细分题型"),
        ("scoring_question_type", "ATM原生题型 · 样本量与质量"),
    ):
        rows = groups.get(dim, [])
        if any(r["value"] not in UNLABELED and r["total"] for r in rows):
            chart = quality_chart(rows, labels, unit, dim)
            if dim == "ability_family":
                low_scores = sorted(
                    [
                        r
                        for r in groups.get("ability_type", [])
                        if r["value"] not in UNLABELED and r.get("score_0", 0)
                    ],
                    key=lambda r: -r.get("score_0", 0),
                )[:4]
                if low_scores:
                    chart += '<h3 class="review-heading">优先复核 · 0分数量最多的能力</h3>'
                    chart += "".join(
                        '<div class="signal-row">'
                        + jump(labels.get(r["value"], r["value"]), "ability_type", r["value"])
                        + f"<b>{r['score_0']} / {r['total']}{unit}</b></div>"
                        for r in low_scores
                    )
                    chart += '<p class="note">右侧为0分数 / 全部样本数。数量排序受样本规模影响，'
                    chart += "多标签样本可重叠，点击后可在明细中继续选0分。</p>"
            dimensions += card(
                title,
                chart,
                f"按{unit}统计；多标签可重叠。点击类别筛选样例。",
            )
    behavior = multiturn(cases)
    scenarios = ""
    for row in report.get("behavior_scenarios", []):
        if row["value"] == "unknown":
            continue
        scenarios += (
            '<div class="scenario-row"><div><b>'
            + esc(labels.get(row["value"], row["value"]))
            + f"</b><small>{row['total']}轮 · 有效判定{row['valid']}轮</small></div>"
            + f"<strong>{rate(row['correct'], row['valid'])}</strong></div>"
        )
    behavior += card(
        "特殊场景 · 行为符合率",
        scenarios or '<p class="empty">未记录特殊场景标签。</p>',
        "逐轮行为Judge原判定；同一轮可属于多个场景。",
    )
    return overview, dimensions, retrieval(cases, report.get("source", {})), behavior

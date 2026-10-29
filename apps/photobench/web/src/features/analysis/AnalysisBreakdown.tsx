import { useState } from "react";
import type { EChartsOption } from "echarts";
import type { AnalysisFilters, AnalysisReport } from "../../api/analysis.ts";
import { AnalysisChart } from "./AnalysisCharts.tsx";
import {
  analysisValue,
  dimensions,
  displayWithLabels,
  percent,
  reportAbilityLabels,
  scores,
} from "./presentation.ts";

type Row = AnalysisReport["groups"][string][number];
const colors = [
  "#4f7eae",
  "#70a691",
  "#d7a858",
  "#9a82b4",
  "#bf7d89",
  "#6ca8b3",
  "#849bb5",
  "#a8b279",
  "#bc9870",
  "#8295a1",
];
const groupChoices = [
  "ability_family",
  "ability_type",
  "benchmark_group",
  "difficulty",
  "answerability",
  "media_type",
  "conversation_type",
  "content_type",
  "content_subtype",
];
const count = (r: Row, key: string) => Number(r[key] || 0);

export function QualityRing({
  report,
  filter,
  unit = "qa",
}: {
  report: AnalysisReport;
  filter: (f: AnalysisFilters) => void;
  unit?: string;
}) {
  const turn = unit === "turn";
  const total = turn ? report.denominators.turns || 0 : report.item_count;
  const rows = scores.map((s) => ({
    name: s.value,
    value: report.counts[`${turn ? "turn_" : ""}score_${s.value}`] || 0,
    itemStyle: { color: s.color },
  }));
  const option: EChartsOption = {
    tooltip: {
      trigger: "item",
      renderMode: "richText",
      formatter: (p) => {
        const x = Array.isArray(p) ? p[0] : p;
        return `${scores.find((s) => s.value === x.name)?.label}\n${x.value} ${turn ? "轮" : "题"} · ${percent(Number(x.value), total)}`;
      },
    },
    series: [
      {
        type: "pie",
        radius: ["53%", "74%"],
        center: ["50%", "48%"],
        label: { show: false },
        data: rows,
        stillShowZeroSum: false,
      },
    ],
    graphic: [
      {
        type: "text",
        left: "center",
        top: "40%",
        style: {
          text: `${total}`,
          fontSize: 28,
          fontWeight: 600,
          fill: "#344b61",
        },
      },
      {
        type: "text",
        left: "center",
        top: "55%",
        style: {
          text: turn ? "对话轮" : "道题",
          fontSize: 13,
          fill: "#718096",
        },
      },
    ],
  };
  return (
    <article className="analysis-chart-card">
      <h4>完成质量构成</h4>
      <AnalysisChart
        label="完成质量环形图"
        height={220}
        option={option}
        onClick={(name) => filter({ score: name, content_unit: unit })}
      />
      <div className="analysis-chart-legend">
        {rows.map((x, i) => (
          <button
            key={x.name}
            onClick={() => filter({ score: x.name, content_unit: unit })}
          >
            <i style={{ background: scores[i].color }} />
            {scores[i].short} <b>{x.value}</b>
          </button>
        ))}
      </div>
    </article>
  );
}

export function TagAnalysis({
  report: r,
  filter,
  unit = "qa",
}: {
  report: AnalysisReport;
  filter: (f: AnalysisFilters) => void;
  unit?: string;
}) {
  const [dimension, setDimension] = useState("ability_family");
  const [view, setView] = useState("quality");
  const [shape, setShape] = useState("pie");
  const [metric, setMetric] = useState("count");
  const [score, setScore] = useState("all");
  const u = unit === "turn" ? "turn" : "qa";
  const noun = u === "turn" ? "轮" : "题";
  const label = (v: string) => displayWithLabels(v, reportAbilityLabels(r));
  const groups = u === "turn" ? r.turn_groups || {} : r.groups;
  const recorded = groups[dimension] || [];
  const catalog = Object.keys(r.ability_labels || {}).filter((k) =>
    dimension === "ability_family"
      ? /^[PRSHI]$/.test(k)
      : dimension === "ability_type"
        ? /^(?:[PRS][1-4]|[HI]1)$/.test(k)
        : false,
  );
  const rows = [
    ...recorded,
    ...catalog
      .filter((k) => !recorded.some((x) => x.value === k))
      .map((value) => ({ value, total: 0 })),
  ].sort((a, b) => b.total - a.total || a.value.localeCompare(b.value));
  const partition = r.group_partitions?.[u]?.[dimension];
  const mixed =
    dimension.startsWith("ability_") &&
    (r.ability_taxonomy_versions?.length || 0) > 1;
  const canPie = !!partition?.exclusive && partition.exhaustive && !mixed;
  const total = u === "turn" ? r.denominators.turns || 0 : r.item_count;
  const denominator =
    score === "all"
      ? total
      : r.counts[`${u === "turn" ? "turn_" : ""}score_${score}`] || 0;
  const focusCount = (x: Row) =>
    score === "all" ? x.total : count(x, `score_${score}`);
  const drill = (value: string, extra: AnalysisFilters = {}) =>
    filter({
      content_unit: u,
      ...(dimension === "ability_family" ? { ability_type: "" } : {}),
      [dimension]: value,
      ...extra,
    });
  const errors = (r.group_errors || []).filter(
    (x) => x.unit === u && x.dimension === dimension,
  );
  const codes = r.errors.filter((x) => errors.some((e) => e.code === x.code));
  const quality: EChartsOption = {
    tooltip: {
      trigger: "item",
      renderMode: "richText",
      formatter: (p) => {
        const x = Array.isArray(p) ? p[0] : p;
        const [id, s] = JSON.parse(x.name);
        const row = rows.find((v) => v.value === id)!;
        return `${label(id)} · ${scores.find((v) => v.value === s)?.label}\n${count(row, `score_${s}`)} ${noun} · 类内占比 ${percent(count(row, `score_${s}`), row.total)}`;
      },
    },
    legend: { bottom: 0, data: scores.map((s) => s.short) },
    grid: { left: 4, right: 25, top: 12, bottom: 50, containLabel: true },
    xAxis: {
      type: "value",
      max: metric === "ratio" ? 100 : undefined,
      axisLabel: { formatter: metric === "ratio" ? "{value}%" : "{value}" },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: rows.map((x) => label(x.value)),
      axisLabel: { width: 150, overflow: "truncate" },
    },
    series: scores.map((s) => ({
      type: "bar",
      name: s.short,
      stack: "quality",
      barMaxWidth: 28,
      itemStyle: { color: s.color },
      data: rows.map((x) => ({
        name: JSON.stringify([x.value, s.value]),
        value:
          metric === "ratio"
            ? x.total
              ? (100 * count(x, `score_${s.value}`)) / x.total
              : 0
            : count(x, `score_${s.value}`),
      })),
    })),
  };
  const composition: EChartsOption = {
    color: colors,
    tooltip: {
      trigger: "item",
      renderMode: "richText",
      formatter: (p) => {
        const x = Array.isArray(p) ? p[0] : p;
        const row = rows.find((v) => v.value === x.name)!;
        return `${label(row.value)}\n${focusCount(row)} ${noun} · ${percent(focusCount(row), denominator)}`;
      },
    },
    ...(canPie && shape === "pie"
      ? {
          legend: { type: "scroll", bottom: 0, formatter: label },
          series: [
            {
              type: "pie" as const,
              radius: ["42%", "68%"],
              center: ["50%", "43%"],
              label: { show: false },
              stillShowZeroSum: false,
              data: rows.map((x) => ({ name: x.value, value: focusCount(x) })),
            },
          ],
        }
      : {
          grid: { left: 4, right: 35, top: 12, bottom: 30, containLabel: true },
          xAxis: { type: "value" as const },
          yAxis: {
            type: "category" as const,
            inverse: true,
            data: rows.map((x) => label(x.value)),
            axisLabel: { width: 150, overflow: "truncate" as const },
          },
          series: [
            {
              type: "bar" as const,
              barMaxWidth: 28,
              data: rows.map((x) => ({ name: x.value, value: focusCount(x) })),
              itemStyle: { color: colors[0] },
              label: { show: true, position: "right" as const },
            },
          ],
        }),
  };
  const heatmap: EChartsOption = {
    tooltip: {
      trigger: "item",
      renderMode: "richText",
      formatter: (p) => {
        const x = Array.isArray(p) ? p[0] : p;
        const v = x.value as number[];
        return `${label(rows[v[1]].value)}\n${codes[v[0]].label} · ${v[2]} ${noun}`;
      },
    },
    grid: { left: 4, top: 12, right: 24, bottom: 90, containLabel: true },
    xAxis: {
      type: "category",
      data: codes.map((x) => x.label),
      axisLabel: { rotate: 30, width: 100, overflow: "truncate" },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: rows.map((x) => label(x.value)),
      axisLabel: { width: 150, overflow: "truncate" },
    },
    visualMap: {
      min: 0,
      max: Math.max(1, ...errors.map((x) => x.count)),
      calculable: false,
      orient: "horizontal",
      bottom: 0,
      left: "center",
      inRange: { color: ["#edf3f9", "#a2bfdc", "#356ba0"] },
    },
    series: [
      {
        type: "heatmap",
        label: { show: true },
        data: errors.map((x) => [
          codes.findIndex((c) => c.code === x.code),
          rows.findIndex((row) => row.value === x.value),
          x.count,
        ]),
      },
    ],
  };
  return (
    <section className="analysis-tag-section">
      <div className="analysis-section-heading">
        <div>
          <h4>按题型与标签分析</h4>
          <p>主能力按题目要求标注，支撑能力不重复计入。</p>
        </div>
        <span className="analysis-badge">
          {total} {noun}
        </span>
      </div>
      <div className="analysis-toolbar">
        <label>
          分组
          <select
            aria-label="标签分析分组"
            value={dimension}
            onChange={(e) => setDimension(e.target.value)}
          >
            {groupChoices
              .filter((k) => u === "qa" || k in groups)
              .map((k) => (
                <option value={k} key={k}>
                  {dimensions[k]}
                  {k.startsWith("content_") ? "（历史标签）" : ""}
                </option>
              ))}
          </select>
        </label>
        <div className="analysis-segmented" aria-label="标签分析视角">
          {[
            ["quality", "得分表现"],
            ["composition", "题目构成"],
            ["errors", "执行问题"],
          ].map(([id, text]) => (
            <button
              key={id}
              aria-pressed={view === id}
              onClick={() => setView(id)}
            >
              {text}
            </button>
          ))}
        </div>
        {view === "quality" && (
          <label>
            显示
            <select
              aria-label="得分显示方式"
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
            >
              <option value="count">数量</option>
              <option value="ratio">类内比例</option>
            </select>
          </label>
        )}
        {view === "composition" && (
          <>
            <label>
              得分范围
              <select
                aria-label="构成得分范围"
                value={score}
                onChange={(e) => setScore(e.target.value)}
              >
                <option value="all">全部得分</option>
                {scores.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            {canPie && (
              <label>
                图形
                <select
                  aria-label="构成图形"
                  value={shape}
                  onChange={(e) => setShape(e.target.value)}
                >
                  <option value="pie">环形图</option>
                  <option value="bar">条形图</option>
                </select>
              </label>
            )}
          </>
        )}
      </div>
      <p className="analysis-hint">
        {partition
          ? `已标注 ${partition.labeled || 0} · 未标注 ${partition.unknown || 0} · 不适用 ${partition.not_applicable || 0} · 多标签 ${partition.multi_labeled || 0} ${noun}`
          : "旧报告未保存逐项分组检查，请重新生成规则分析。"}
        {dimension === "ability_type" &&
          (r.ability_taxonomy_versions?.includes("ability-2026.10.29-v3")
            ? "；v3二级包含抗幻觉和抗注入攻击；普通澄清轮不适用，历史版本按原标签统计。"
            : "；抗幻觉、抗注入及普通澄清轮无二级能力。")}
        {view === "composition" &&
          !canPie &&
          " 分组重叠或完整性未确认，使用条形图。"}
      </p>
      {mixed ? (
        <p role="status" className="analysis-annotation-notice">
          存在不同版本的能力标签，请先用“标签版本”筛选后查看能力分组。
        </p>
      ) : !rows.length ? (
        <p>当前范围没有该分组记录。</p>
      ) : (
        <>
          {view === "errors" && !codes.length ? (
            <p>当前范围没有记录到执行问题。</p>
          ) : (
            <AnalysisChart
              label={
                view === "quality"
                  ? "标签得分分布图"
                  : view === "composition"
                    ? canPie && shape === "pie"
                      ? "标签构成环形图"
                      : "标签构成条形图"
                    : "标签与执行问题矩阵"
              }
              height={
                view === "composition" && canPie && shape === "pie"
                  ? 310
                  : Math.max(
                      270,
                      rows.length * 42 + (view === "errors" ? 110 : 65),
                    )
              }
              option={
                view === "quality"
                  ? quality
                  : view === "composition"
                    ? composition
                    : heatmap
              }
              onClick={(name, value) => {
                if (view === "quality") {
                  const [id, s] = JSON.parse(name);
                  drill(id, { score: s });
                } else if (view === "composition")
                  drill(name, { score: score === "all" ? "" : score });
                else {
                  const v = value as number[];
                  drill(rows[v[1]].value, { code: codes[v[0]].code });
                }
              }}
            />
          )}
          <div className="analysis-table-scroll">
            <table>
              <caption>
                {dimensions[dimension]} · {noun}级统计；点击数字查看题目。
              </caption>
              <thead>
                <tr>
                  <th>分类</th>
                  <th>{noun}数</th>
                  {scores.map((s) => (
                    <th key={s.value}>{s.short}</th>
                  ))}
                  <th>满分率</th>
                  <th>有执行问题</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((x) => (
                  <tr key={x.value}>
                    <td>
                      <button
                        className="analysis-link"
                        onClick={() => drill(x.value)}
                      >
                        {label(x.value)}
                      </button>
                    </td>
                    <td>{x.total}</td>
                    {scores.map((s) => (
                      <td key={s.value}>
                        <button
                          className="analysis-number"
                          aria-label={`${label(x.value)} ${s.label}`}
                          onClick={() => drill(x.value, { score: s.value })}
                        >
                          {count(x, `score_${s.value}`)}
                        </button>
                      </td>
                    ))}
                    <td>
                      {percent(
                        count(x, "score_2"),
                        x.total - count(x, "score_None"),
                      )}
                    </td>
                    <td>
                      <button
                        className="analysis-number"
                        aria-label={`${label(x.value)} 执行问题`}
                        onClick={() => drill(x.value, { anomaly: "yes" })}
                      >
                        {count(x, "affected")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {view === "errors" && !!errors.length && (
            <details>
              <summary>查看矩阵数值</summary>
              <div className="analysis-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>分类</th>
                      <th>执行问题</th>
                      <th>{noun}数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {errors.map((x) => (
                      <tr key={`${x.value}/${x.code}`}>
                        <td>{label(x.value)}</td>
                        <td>
                          {codes.find((c) => c.code === x.code)?.label ||
                            analysisValue(x.code)}
                        </td>
                        <td>
                          <button
                            className="analysis-number"
                            onClick={() => drill(x.value, { code: x.code })}
                          >
                            {x.count}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
      <p className="analysis-hint">
        {view === "errors"
          ? "同类问题按题或轮去重；一题可有多类问题，记录关联不等于确认低分原因。"
          : view === "composition"
            ? `占比以当前得分范围的全部${noun}为分母${canPie ? "，各组互斥且覆盖当前范围。" : "；多标签占比合计可能超过100%。"}`
            : "满分率以类内有效评分为分母；未评分单列，多标签分别计入各类。"}
      </p>
    </section>
  );
}

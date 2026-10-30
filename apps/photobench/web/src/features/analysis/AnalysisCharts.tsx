import { qaTaxonomy, subtypeParent, abilityParent } from "../../lib/qaTaxonomy.ts";
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import { dimensions, displayWithLabels, reportAbilityLabels, percent, scores } from "./presentation.ts";
import type { AnalysisFilters, AnalysisReport } from "../../api/analysis.ts";

export function AnalysisChart({
  option,
  onClick,
  label,
  height,
}: {
  option: EChartsOption;
  onClick?: (name: string, value: unknown) => void;
  label: string;
  height?: number;
}) {
  const element = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  const renderedOption = useRef<string | null>(null);
  const clickHandler = useRef(onClick);
  useEffect(() => {
    clickHandler.current = onClick;
  }, [onClick]);
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, undefined, { renderer: "svg" });
    instance.current = chart;
    chart.on("click", (event) => {
      clickHandler.current?.(event.name, event.value);
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      instance.current = null;
      renderedOption.current = null;
    };
  }, []);
  useEffect(() => {
    // Polling returns fresh objects for the same saved report. Heatmap renders
    // recreate labels, so submitting identical options restarts their fade-in.
    const nextOption = JSON.stringify(option);
    if (!instance.current || renderedOption.current === nextOption) return;
    instance.current.setOption({ ...option, animation: false }, { replaceMerge: ["series", "xAxis", "yAxis", "visualMap", "legend", "graphic", "grid"] });
    renderedOption.current = nextOption;
  }, [option]);
  return (
    <div
      role="img"
      aria-label={label}
      ref={element}
      className="analysis-chart"
      style={height ? { height } : undefined}
    />
  );
}

export function AnalysisCharts({ report: r, filter }: {
  report: AnalysisReport;
  filter: (value: AnalysisFilters) => void;
}) {
  const displayLabel = (value: string) => displayWithLabels(value, reportAbilityLabels(r));
  const [dimension, setDimension] = useState(r.rule_version >= 7 ? "ability_family" : "content_type");
  const [parent, setParent] = useState("");
  const [unit, setUnit] = useState("qa");
  const [ratio, setRatio] = useState(false);
  const [perspective, setPerspective] = useState("category");
  const [score, setScore] = useState("2");
  if (r.rule_version < 4) return <p>此报告使用旧分类口径，请重新生成分析以查看内容题型与回答行为。</p>;
  const selectedScore = scores.find(s => s.value === score)!;
  const isAbility = ["ability_family", "ability_type"].includes(dimension);
  const isContent = isAbility || ["content_type", "content_subtype"].includes(dimension);
  const groups = unit === "turn" ? r.turn_groups || {} : r.groups;
  const allRows = groups[dimension] || [];
  const child = ["content_subtype", "ability_type"].includes(dimension);
  const parents = dimension === "ability_type" ? abilityParent : subtypeParent;
  const rows = child && parent ? allRows.filter(row => parents[row.value] === parent) : allRows;
  const unitKey = unit === "turn" ? "turn" : "qa";
  const scoreTotal = child && parent
    ? (isAbility ? r.counts[`${unitKey}_ability_family_${parent}_score_${score}`] || 0 : rows.reduce((n, row) => n + Number(row[`score_${score}`] || 0), 0))
    : r.counts[`${unit === "turn" ? "turn_" : ""}score_${score}`] || 0;
  const excluded = ["unknown", "not_applicable", "pending_review"];
  const missingType = isContent ? rows.filter(row => excluded.includes(row.value)).reduce((n, row) => n + Number(row[`score_${score}`] || 0), 0) : 0;
  const scoreBase = isAbility
    ? (parent && child ? scoreTotal : r.counts[`${unitKey}_ability${dimension === "ability_family" && r.rule_version >= 8 ? "_family" : ""}_score_${score}`] || 0)
    : scoreTotal - missingType;
  const groupLabel = (value: string) => dimension === "content_type" && value === "unknown" ? "未标注内容题型" : displayLabel(value);
  const scoreRows = rows.filter(row => !isContent || !excluded.includes(row.value)).map(row => ({
    value: row.value, count: Number(row[`score_${score}`] || 0),
  })).filter(row => row.count > 0).sort((a, b) => b.count - a.count);
  const scoreOverlap = scoreRows.reduce((total, row) => total + row.count, 0) > scoreBase;
  const errors = r.errors;
  const heatDimension = r.rule_version >= 7 ? "ability_family" : "content_type";
  const heatRows = r.rule_version >= 7
    ? (r.ability_heatmap || []).map(x => ({value: x.ability_family, code: x.code, count: x.count}))
    : r.heatmap.map(x => ({value: x.content_type, code: x.code, count: x.count}));
  const qs = (r.groups[heatDimension] || []).map(x => x.value);
  const es = errors.map(x => x.code);
  const actions = ["answer", "refuse", "clarify", "answer_and_refuse", "none", "unknown"];
  const selectGroup = (value: AnalysisFilters) => filter({
    ...Object.fromEntries(Object.keys(dimensions).map(key => [key, ""])),
    code: "", anomaly: "", recovery: "", execution: "", expected_action: "", actual_action: "", behavior_result: "", test_scenario: "", task_type: "", question_type: "",
    content_unit: unit,
    ...value,
  });
  const distribution: EChartsOption = {
    color: scores.map(x => x.color),
    tooltip: { trigger: "axis" },
    legend: { show: true, top: 0, type: "scroll" },
    grid: { containLabel: true, top: 55, left: 10, right: 45, bottom: 25 },
    xAxis: { type: "value", minInterval: ratio ? undefined : 1, max: ratio ? 100 : undefined,
      axisLabel: { formatter: ratio ? "{value}%" : "{value}" } },
    yAxis: { type: "category", inverse: true, data: rows.map(x => groupLabel(x.value)) },
    series: scores.map(s => ({
      labelLayout: { hideOverlap: true },
      name: s.label, type: "bar", stack: "scores", label: { show: true, formatter: "{c}" },
      data: rows.map(x => ({ name: JSON.stringify([x.value, s.value]),
        value: ratio ? Number((Number(x[`score_${s.value}`] || 0) / Math.max(1, x.total) * 100).toFixed(1)) : Number(x[`score_${s.value}`] || 0), label: { show: Number(x[`score_${s.value}`] || 0) / Math.max(1, ratio ? x.total : Math.max(...rows.map(row => row.total))) >= 0.035, formatter: ratio ? "{c}%" : "{c}" } })),
    })),
  };
  const scoreDistribution: EChartsOption = {
    color: [selectedScore.color],
    tooltip: { trigger: "axis" },
    grid: { containLabel: true, top: 20, left: 10, right: 55, bottom: 25 },
    xAxis: { type: "value", minInterval: ratio ? undefined : 1, max: ratio ? 100 : undefined,
      axisLabel: { formatter: ratio ? "{value}%" : "{value}" } },
    yAxis: { type: "category", inverse: true, data: scoreRows.map(row => groupLabel(row.value)) },
    legend: { show: false },
    series: [{ name: selectedScore.label, type: "bar", label: { show: true, position: "right", formatter: ratio ? "{c}%" : "{c}" },
      data: scoreRows.map(row => ({ name: row.value,
        value: ratio ? Number((row.count / scoreBase * 100).toFixed(1)) : row.count })) }],
  };
  const errorOption: EChartsOption = {
    tooltip: { trigger: "axis" }, legend: { top: 0, type: "scroll" },
    grid: { containLabel: true, top: 60, left: 10, right: 35, bottom: 25 },
    xAxis: { type: "value", minInterval: 1 },
    yAxis: { type: "category", inverse: true, data: errors.map(x => x.label) },
    series: [
      { name: "发生异常的题", type: "bar", data: errors.map(x => x.affected_items), itemStyle: { color: "#638eb4" } },
      { name: "最终执行失败", type: "bar", data: errors.map(x => x.final_failed_items), itemStyle: { color: "#c35550" } },
      { name: "异常已恢复", type: "bar", data: errors.map(x => x.recovered_items), itemStyle: { color: "#287c65" } },
    ],
  };
  const heatmap: EChartsOption = {
    tooltip: {}, grid: { containLabel: true, bottom: 90, left: 10 },
    visualMap: { min: 0, max: Math.max(1, ...heatRows.map(x => x.count)), orient: "horizontal", left: "center", bottom: 0 },
    xAxis: { type: "category", data: errors.map(x => x.label), axisLabel: { rotate: 30 } },
    yAxis: { type: "category", data: qs.map(v => v === "unknown" && heatDimension === "content_type" ? "未标注内容题型" : displayLabel(v)) },
    series: [{ type: "heatmap", label: { show: true }, data: heatRows.map(x => [es.indexOf(x.code), qs.indexOf(x.value), x.count]) }],
  };
  const matrix: EChartsOption = {
    tooltip: {}, grid: { containLabel: true, bottom: 55 },
    visualMap: { min: 0, max: Math.max(1, ...r.behavior_matrix.map(x => x.count)), orient: "horizontal", left: "center", bottom: 0 },
    xAxis: { type: "category", name: "实际", data: actions.map(displayLabel) },
    yAxis: { type: "category", name: "预期", data: actions.map(displayLabel) },
    series: [{ type: "heatmap", label: { show: true }, data: r.behavior_matrix.map(x => [actions.indexOf(x.actual), actions.indexOf(x.expected), x.count]) }],
  };
  return <>
    <article className="analysis-distribution">
      <h4>任务完成质量 · 按内容与题目属性分组</h4>
      <p>{unit === "turn" ? "按轮统计；点击后展示对应题目，同题多轮只列一次。" : "按题统计；能力取回答轮并集，旧内容题型采用最终任务，得分仍为题级最终分。"}</p>
      <div className="analysis-toolbar" role="group" aria-label="统计角度">
        <button className="btn compact" aria-pressed={perspective === "category"} onClick={() => setPerspective("category")}>按题型看得分</button>
        <button className="btn compact" aria-pressed={perspective === "score"} onClick={() => setPerspective("score")}>按得分看题型</button>
      </div>
      <div className="analysis-toolbar">
        {r.rule_version >= 5 && <label>统计单位<select aria-label="内容统计单位" value={unit} onChange={e => { setUnit(e.target.value); setDimension(r.rule_version >= 7 ? "ability_family" : "content_type"); setParent(""); }}>
          <option value="qa">题</option><option value="turn">对话轮</option>
        </select></label>}
        {perspective === "score" && <label>得分<select aria-label="分布得分" value={score} onChange={e => setScore(e.target.value)}>
          {scores.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select></label>}
        <label>分组方式<select aria-label="分布分组方式" value={dimension} onChange={e => { setDimension(e.target.value); setParent(""); }}>
          {Object.entries(dimensions).filter(([key]) => r.rule_version >= 7 || !["ability_family", "ability_type", "benchmark_group"].includes(key)).filter(([key]) => unit !== "turn" || ["ability_family", "ability_type", "benchmark_group", "content_type", "content_subtype", "difficulty", "answerability"].includes(key)).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        {dimension === "content_subtype" && <label>内容大类<select aria-label="分析内容大类" value={parent} onChange={e => setParent(e.target.value)}>
          <option value="">全部大类</option>{Object.entries(qaTaxonomy.tasks).map(([key, task]) => <option key={key} value={key}>{task.label}</option>)}
        </select></label>}
        {dimension === "ability_type" && <label>一级能力<select aria-label="分析一级能力" value={parent} onChange={e => setParent(e.target.value)}>
          <option value="">全部能力</option>{Object.keys(qaTaxonomy.abilities.families).filter(key => allRows.some(row => abilityParent[row.value] === key)).map(key => <option key={key} value={key}>{displayLabel(key)}</option>)}
        </select></label>}
        {dimension === "ability_type" && r.rule_version >= 8 && <span className="analysis-hint">{r.ability_labels?.H1 || r.ability_labels?.I1 ? "抗幻觉、抗注入攻击的二级名称与一级一致。" : "此历史报告的安全题二级标签未记录。"}</span>}
        {dimension === "ability_family" && <button className="btn compact" onClick={() => setDimension("ability_type")}>展开二级能力</button>}
        {dimension === "content_type" && r.rule_version >= 5 && <button className="btn compact" onClick={() => setDimension("content_subtype")}>展开细分题型</button>}
        <label>显示方式<select aria-label="分布显示方式" value={ratio ? "ratio" : "count"} onChange={e => setRatio(e.target.value === "ratio")}>
          <option value="count">{unit === "turn" ? "轮数" : "题数"}</option><option value="ratio">{perspective === "score" ? (isContent ? "有效题型占比" : "占该得分全部题目") : "组内占比"}</option>
        </select></label>
      </div>
      {perspective === "score" ? <>
        <p>{selectedScore.label} · 共{scoreTotal}{unit === "turn" ? "轮" : "题"}{isContent && <> · 有效题型 {scoreBase} · 排除 {isAbility ? scoreTotal - scoreBase : missingType}</>}</p>
        {isContent && rows.filter(row => excluded.includes(row.value) && Number(row[`score_${score}`] || 0) > 0).map(row => <button key={row.value} className="analysis-number" onClick={() => selectGroup({[dimension]: row.value, score})}>{groupLabel(row.value)} {row[`score_${score}`]}{unit === "turn" ? "轮" : "题"}</button>)}
        {scoreOverlap && <p className="analysis-hint">一题可属于多类，各类占比合计可能超过100%。</p>}
        {scoreRows.length ? <>
          <AnalysisChart label="所选得分的分类分布" option={scoreDistribution} height={Math.max(270, scoreRows.length * 42 + 65)} onClick={value => selectGroup({ [dimension]: value, score })} />
          <div className="analysis-table-scroll"><table>
            <caption>{selectedScore.label}的{dimensions[dimension]}分布 · 点击数字查看案例</caption>
            <thead><tr><th>类别</th><th>{unit === "turn" ? "轮数" : "题数"}</th><th>{isContent ? "占有效题型的比例" : `占这${scoreTotal}题的比例`}</th></tr></thead>
            <tbody>{scoreRows.map(row => <tr key={row.value}>
              <th title={row.value}>{dimension === "content_type" && row.value in qaTaxonomy.tasks ? <button className="analysis-number" onClick={() => { setParent(row.value); setDimension("content_subtype"); }}>{groupLabel(row.value)} ›</button> : groupLabel(row.value)}</th>
              <td><button className="analysis-number" aria-label={`${groupLabel(row.value)} ${selectedScore.label}案例`} onClick={() => selectGroup({ [dimension]: row.value, score })}>{row.count}</button></td>
              <td>{percent(row.count, scoreBase)}</td>
            </tr>)}</tbody>
          </table></div>
        </> : <p>{scoreTotal ? `这些题未标注${dimensions[dimension]}。` : `当前范围没有${selectedScore.label}的题目。`}</p>}
      </> : <>
      {rows.reduce((n, row) => n + row.total, 0) > (unit === "turn" ? r.denominators.turns : r.item_count) && <p className="analysis-hint">一题可属于多类，各类题数有重叠。</p>}
      {rows.length ? <>
        <AnalysisChart label="分类质量分布" option={distribution} height={Math.max(270, rows.length * 42 + 95)} onClick={name => {
          const value: unknown = JSON.parse(name);
          if (Array.isArray(value) && value.length === 2) selectGroup({ [dimension]: String(value[0]), score: String(value[1]) });
        }} />
        <div className="analysis-table-scroll"><table>
          <caption>{dimensions[dimension]}表现 · {unit === "turn" ? "轮数" : "题数"} · 点击数字筛选案例</caption>
          <thead><tr><th>类别</th><th>{unit === "turn" ? "总轮数" : "总题数"}</th>{scores.map(s => <th key={s.value}>{s.label}</th>)}<th>满分率 / 有效评分</th></tr></thead>
          <tbody>{rows.map(row => { const valid = row.total - Number(row.score_None || 0); return <tr key={row.value}>
            <th title={row.value}>{dimension === "content_type" && row.value in qaTaxonomy.tasks ? <button className="analysis-number" onClick={() => { setParent(row.value); setDimension("content_subtype"); }}>{groupLabel(row.value)} ›</button> : groupLabel(row.value)}</th>
            <td><button className="analysis-number" onClick={() => selectGroup({ [dimension]: row.value, score: "" })}>{row.total}</button></td>
            {scores.map(s => <td key={s.value}><button className="analysis-number" aria-label={`${groupLabel(row.value)} ${s.label}`} onClick={() => selectGroup({ [dimension]: row.value, score: s.value })}>{Number(row[`score_${s.value}`] || 0)}</button></td>)}
            <td>{percent(Number(row.score_2 || 0), valid)} / {valid}{unit === "turn" ? "轮" : "题"}</td>
          </tr>})}</tbody>
        </table></div>
      </> : <p>暂无{dimensions[dimension]}分类。</p>}
      </>}
    </article>
    <details className="analysis-advanced">
      <summary>运行异常与轮级质量</summary>
      <p>运行异常独立于任务完成质量。</p>
      <div className="analysis-grid">
        <article><h4>运行异常分布 · 点击筛选</h4><p>全部 {r.item_count} 题中，{r.counts.affected_items || 0}题发生过异常，共 {r.counts.events || 0} 次；类别可重叠。</p>
          {!errors.length && <p>未记录到运行异常。</p>}
          <AnalysisChart label="错误分布" option={errorOption} onClick={name => selectGroup({ code: errors.find(x => x.label === name)?.code || "", score: "" })} />
        </article>
        <article><h4>{r.rule_version >= 7 ? "一级能力与运行异常（按题）" : "内容题型与运行异常（按题）"}</h4><p>缺轨迹 {r.counts.missing_trace || 0}题。</p>
          <AnalysisChart label="题型错误热力图" option={heatmap} onClick={(_, value) => {
            if (Array.isArray(value)) selectGroup({ code: es[Number(value[0])], [heatDimension]: qs[Number(value[1])], score: "", content_unit: "qa" });
          }} />
        </article>
      </div>
      <h4>对话轮级评分</h4>
      <p>共 {r.denominators.turns} 轮，有效评分 {r.denominators.turn_quality_valid} 轮，未评分 {r.denominators.turns - r.denominators.turn_quality_valid} 轮。</p>
      <table><thead><tr>{scores.map(s => <th key={s.value}>{s.label}</th>)}</tr></thead><tbody><tr>{scores.map(s => <td key={s.value}>{r.counts[`turn_score_${s.value}`] || 0}轮</td>)}</tr></tbody></table>
    </details>
    <article className="analysis-behavior">
      <h4>回答行为</h4>
      <p>判断回答、澄清、拒答及混合行为是否符合预期；内容是否正确见质量分。按轮统计，案例按题去重。</p>
      <div className="analysis-behavior-bar" aria-label="预期行为占比" style={{display: "flex", height: 34, margin: "16px 0"}}>
        {(r.behavior_groups || []).filter(row => row.total > 0).map(row => <button key={row.value} className="btn compact"
          style={{width: `${row.total / r.denominators.turns * 100}%`, minWidth: 0, padding: 0, whiteSpace: "nowrap", overflow: "hidden", borderRadius: 0, color: "white", background: ({answer: "#426f95", clarify: "#a97a35", refuse: "#47816a", answer_and_refuse: "#8162a3", unknown: "#929baa"} as Record<string, string>)[row.value]}}
          title={`${displayLabel(row.value)} ${row.total}轮 · ${percent(row.total, r.denominators.turns)}`}
          onClick={() => selectGroup({expected_action: row.value, score: "", content_unit: "qa"})}>{row.total / r.denominators.turns >= 0.07 ? `${displayLabel(row.value)} ${percent(row.total, r.denominators.turns)}` : ""}</button>)}
      </div>
      <table>
        <caption>预期行为分布 · 共{r.denominators.turns}轮</caption>
        <thead><tr><th>预期行为</th><th>轮数</th><th>占全部轮数</th><th>符合</th><th>不符合</th><th>未判定</th><th>符合率 / 有效判定</th></tr></thead>
        <tbody>{["answer", "clarify", "refuse", "answer_and_refuse", "unknown"].map(action => {
          const row = r.behavior_groups?.find(x => x.value === action);
          const total = row?.total || 0;
          return <tr key={action}><th>{displayLabel(action)}</th>
            <td>{total ? <button className="analysis-number" onClick={() => selectGroup({expected_action: action, score: "", content_unit: "qa"})}>{total}</button> : "未覆盖"}</td>
            <td>{total ? percent(total, r.denominators.turns) : "—"}</td>
            {(["correct", "incorrect", "missing"] as const).map(result => <td key={result}><button className="analysis-number" disabled={!row?.[result]} onClick={() => selectGroup({expected_action: action, behavior_result: result, score: "", content_unit: "qa"})}>{row?.[result] || 0}</button></td>)}
            <td>{percent(row?.correct || 0, row?.valid || 0)} / {row?.valid || 0}轮</td>
          </tr>;
        })}</tbody>
      </table>
      <details><summary>测试场景（可重叠）</summary>
        <table><thead><tr><th>场景</th><th>轮数</th><th>符合率</th><th>有效判定</th><th>未判定</th></tr></thead>
          <tbody>{(r.behavior_scenarios || []).map(row => <tr key={row.value}><th>{row.value === "unknown" ? "未标注专项场景" : displayLabel(row.value)}</th>
            <td><button className="analysis-number" onClick={() => selectGroup({test_scenario: row.value, score: "", content_unit: "qa"})}>{row.total}</button></td>
            <td>{percent(row.correct, row.valid)}</td><td>{row.valid}</td><td>{row.missing}</td></tr>)}</tbody>
        </table>
      </details>
      <h4>预期 × 实际行为 · 点击筛选</h4>
      <p>共{r.denominators.turns}轮 · 有效判定 {r.denominators.behavior_valid}轮 · 未判定 {r.denominators.behavior_missing}轮；矩阵包含未判定轮。</p>
      <AnalysisChart label="行为混淆矩阵" option={matrix} onClick={(_, value) => {
        if (Array.isArray(value)) selectGroup({ actual_action: actions[Number(value[0])], expected_action: actions[Number(value[1])], score: "", content_unit: "qa" });
      }} />
    </article>
  </>;
}

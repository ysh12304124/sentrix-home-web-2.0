import { QualityRing, TagAnalysis } from "./AnalysisBreakdown.tsx";
import type { AnalysisFilters, AnalysisView } from "../../api/analysis.ts";
import {
  displayLabel,
  displayWithLabels,
  dimensions,
  percent,
  scores,
  toolLabel,
  analysisValue,
} from "./presentation.ts";

export type AnalysisTab = "overview" | "ability" | "execution" | "behavior";
const labels: Record<string, string> = {
  ...dimensions,
  score: "完成质量",
  tool_name: "工具",
  failure_reason: "返回原因",
  issue_kind: "问题类型",
  code: "具体问题",
  recovery: "恢复情况",
  component: "执行环节",
  expected_action: "预期行为",
  actual_action: "实际行为",
  behavior_result: "行为判定",
  content_unit: "统计单位",
  q: "搜索",
  index: "题目位置",
  signal: "检索与交付",
  anomaly: "执行问题",
  execution: "结束状态",
};

export function AnalysisFiltersBar({
  data,
  filters,
  change,
}: {
  data: AnalysisView;
  filters: AnalysisFilters;
  change: (f: AnalysisFilters, replace?: boolean) => void;
}) {
  const labelValue = (v: string) =>
    displayWithLabels(v, data.summary.ability_labels || {});
  const filterValue = (key: string, value: string) =>
    key === "tool_name"
      ? toolLabel(value)
      : key.startsWith("ability")
        ? labelValue(value)
        : key === "code"
          ? data.summary.errors.find((e) => e.code === value)?.label ||
            analysisValue(value)
          : analysisValue(value);
  function select(key: string, values?: { value: string; label?: string }[]) {
    let rows: { value: string; label?: string }[] =
      values || data.facets[key] || [];
    if (key === "ability_type" && filters.ability_family)
      rows = (data.ability_tree[filters.ability_family] || []).map((value) => ({
        value,
      }));
    return (
      <label key={key}>
        {labels[key] || key}
        <select
          aria-label={labels[key] || key}
          value={filters[key] || ""}
          onChange={(e) =>
            change(
              key === "ability_family"
                ? { [key]: e.target.value, ability_type: "" }
                : { [key]: e.target.value },
            )
          }
        >
          <option value="">全部</option>
          {rows.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label ||
                (key === "tool_name"
                  ? toolLabel(r.value)
                  : key.startsWith("ability")
                    ? labelValue(r.value)
                    : analysisValue(r.value))}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <div className="analysis-filter-box">
      <div className="analysis-filter-row">
        <label className="analysis-search">
          搜索题目
          <input
            aria-label="搜索题目"
            placeholder="题号、问题或回答"
            value={filters.q || ""}
            onChange={(e) => change({ q: e.target.value })}
          />
        </label>
        {select("score", [
          ...scores.map((s) => ({ value: s.value, label: s.short })),
          { value: "low", label: "未得满分" },
        ])}
        {select("ability_family")}
        {select("ability_type")}
        {select("benchmark_group")}
      </div>
      <div className="analysis-filter-row">
        <span className="analysis-filter-title">执行</span>
        {select("tool_name")}
        {select("issue_kind")}
        {select("failure_reason")}
        {select(
          "recovery",
          ["recovered", "not_recovered", "unknown"].map((value) => ({ value })),
        )}
      </div>
      <details className="analysis-extra-filters">
        <summary>属性与回答行为</summary>
        <div className="analysis-filter-row">
          {[
            "ability_taxonomy_version",
            "difficulty",
            "answerability",
            "media_type",
            "conversation_type",
          ].map((k) => select(k))}
        </div>
        <div className="analysis-filter-row">
          {select("content_unit", [{ value: "qa" }, { value: "turn" }])}
          {select(
            "expected_action",
            ["answer", "refuse", "clarify", "answer_and_refuse", "unknown"].map(
              (value) => ({ value }),
            ),
          )}
          {select(
            "actual_action",
            [
              "answer",
              "refuse",
              "clarify",
              "answer_and_refuse",
              "none",
              "unknown",
            ].map((value) => ({ value })),
          )}
          {select(
            "behavior_result",
            ["correct", "incorrect", "missing"].map((value) => ({ value })),
          )}
        </div>
      </details>
      {Object.values(filters).some(Boolean) && (
        <div className="analysis-chips">
          {Object.entries(filters)
            .filter(([, v]) => v)
            .map(([key, value]) => (
              <button
                className="analysis-chip"
                key={key}
                onClick={() =>
                  change(
                    key === "ability_family"
                      ? { ability_family: "", ability_type: "" }
                      : { [key]: "" },
                  )
                }
              >
                {labels[key] || key}：{filterValue(key, value)}{" "}
                <span aria-hidden>×</span>
                <span className="sr-only">移除筛选</span>
              </button>
            ))}
          <button className="btn compact" onClick={() => change({}, true)}>
            清空筛选
          </button>
        </div>
      )}
    </div>
  );
}

export function AnalysisWorkspace({
  data,
  tab,
  setTab,
  filter,
  unit,
}: {
  data: AnalysisView;
  tab: AnalysisTab;
  setTab: (t: AnalysisTab) => void;
  filter: (f: AnalysisFilters) => void;
  unit?: string;
}) {
  const r = data.summary;
  const currentUnit = unit === "turn" ? "turn" : "qa";
  const noun = currentUnit === "turn" ? "轮" : "题";
  const total =
    currentUnit === "turn" ? r.denominators.turns || 0 : r.item_count;
  const scoreCount = (s: string) =>
    r.counts[`${currentUnit === "turn" ? "turn_" : ""}score_${s}`] || 0;
  const qualityValid =
    currentUnit === "turn"
      ? r.denominators.turn_quality_valid
      : r.denominators.quality_valid;
  const coverage = r.group_partitions?.[currentUnit]?.ability_family;
  const versions = r.ability_taxonomy_versions || [];
  return (
    <>
      <div role="tablist" aria-label="分析视图" className="analysis-tabs">
        {(
          [
            ["overview", "结果概览"],
            ["ability", "题型与标签"],
            ["execution", "执行问题"],
            ["behavior", "回答行为"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="analysis-annotation-notice">
        <span>标签来源：本次运行保存的题目及冻结题库快照</span>
        <span>版本：{versions.length ? versions.join("、") : "未记录"}</span>
        {coverage && (
          <span>
            一级能力已标注 {coverage.labeled || 0}/{coverage.total} {noun}
          </span>
        )}
        {!!coverage?.unknown && (
          <span>
            有 {coverage.unknown} {noun}缺少标签，单列为未知。
          </span>
        )}
        {!versions.length && <span>旧结果不能仅凭题号套用最新标签。</span>}
      </div>
      <div role="tabpanel" className="analysis-view">
        {tab === "overview" && (
          <>
            <div className="analysis-stats">
              {scores.map((s) => (
                <button
                  className={`analysis-stat outcome-${s.value}`}
                  key={s.value}
                  onClick={() => filter({ score: s.value })}
                >
                  <span>{s.short}</span>
                  <strong>{scoreCount(s.value)}</strong>
                  <small>{percent(scoreCount(s.value), total)}</small>
                </button>
              ))}
            </div>
            <p className="analysis-lead">
              满分率 <b>{percent(scoreCount("2"), qualityValid)}</b> · 有效评分{" "}
              {qualityValid || 0}/{total} {noun}
            </p>
            <div className="analysis-overview-charts">
              <QualityRing report={r} filter={filter} unit={currentUnit} />
              <article className="analysis-chart-card">
                <h4>题型覆盖</h4>
                <div className="analysis-family-cards">
                  {(r.groups.ability_family || []).map((x) => (
                    <button
                      key={x.value}
                      onClick={() => {
                        setTab("ability");
                        filter({
                          ability_family: x.value,
                          ability_type: "",
                          content_unit: "qa",
                        });
                      }}
                    >
                      <span>
                        {displayWithLabels(x.value, r.ability_labels || {})}
                      </span>
                      <b>{x.total} 题</b>
                      <small>
                        满分{" "}
                        {percent(
                          Number(x.score_2 || 0),
                          x.total - Number(x.score_None || 0),
                        )}
                      </small>
                    </button>
                  ))}
                </div>
                <button
                  className="analysis-link"
                  onClick={() => setTab("ability")}
                >
                  查看二级题型、标签表现与问题矩阵 →
                </button>
              </article>
            </div>
            <div className="analysis-runtime-summary">
              <button
                className="btn compact"
                onClick={() => filter({ anomaly: "yes" })}
              >
                记录到执行问题 {r.counts.affected_items || 0} 题
              </button>
              <button
                className="btn compact"
                onClick={() => filter({ execution: "failed" })}
              >
                最终执行失败 {r.counts.execution_failed || 0} 题
              </button>
              <span>轨迹缺失 {r.counts.missing_trace || 0} 题</span>
            </div>
            <div className="analysis-issue-grid">
              {data.issues.slice(0, 4).map((g) => (
                <button
                  key={g.group_id}
                  className="analysis-issue-card"
                  onClick={() => {
                    setTab("execution");
                    filter({
                      tool_name: g.tool_name,
                      code: g.code,
                      failure_reason: g.failure_reason,
                      ...(g.component ? { component: g.component } : {}),
                    });
                  }}
                >
                  <b>{g.label}</b>
                  <span>
                    {g.tool_name === "unknown"
                      ? analysisValue(g.component || "unknown")
                      : toolLabel(g.tool_name)}
                  </span>
                  <small>
                    {g.affected_items} 题 · {g.affected_calls} 次关联调用 · 其中{" "}
                    {g.low_score_items} 题未满分
                  </small>
                </button>
              ))}
            </div>
            <div className="analysis-toolbar">
              {[
                "candidate_miss",
                "candidate_hit_not_delivered",
                "delivered_low_score",
              ].map((s) => (
                <button
                  key={s}
                  className="btn compact"
                  onClick={() => filter({ signal: s })}
                >
                  {displayLabel(s)} {r.counts[s] || 0}
                </button>
              ))}
            </div>
          </>
        )}
        {tab === "ability" && (
          <TagAnalysis report={r} filter={filter} unit={currentUnit} />
        )}
        {tab === "execution" && (
          <>
            <h4>按工具定位</h4>
            <div className="analysis-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>工具</th>
                    <th>已记录调用</th>
                    <th>有关联问题的调用</th>
                    <th>受影响题数</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tools.map((t) => (
                    <tr key={t.tool_name}>
                      <td>
                        <button
                          className="analysis-link"
                          onClick={() => filter({ tool_name: t.tool_name })}
                        >
                          {toolLabel(t.tool_name)}
                        </button>
                      </td>
                      <td>{t.recorded_calls}</td>
                      <td>{t.affected_calls}</td>
                      <td>{t.affected_items}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h4>问题与返回原因</h4>
            <div className="analysis-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>工具／环节</th>
                    <th>问题</th>
                    <th>返回原因</th>
                    <th>题数</th>
                    <th>关联调用</th>
                    <th>未满分题数</th>
                    <th>查看</th>
                  </tr>
                </thead>
                <tbody>
                  {data.issues.map((g) => (
                    <tr key={g.group_id}>
                      <td>
                        {g.tool_name === "unknown"
                          ? analysisValue(g.component || "unknown")
                          : toolLabel(g.tool_name)}
                      </td>
                      <td>{g.label}</td>
                      <td>
                        <span>{analysisValue(g.failure_reason)}</span>
                        {g.failure_reason !== "unknown" && (
                          <code>{g.failure_reason}</code>
                        )}
                      </td>
                      <td>{g.affected_items}</td>
                      <td>{g.affected_calls}</td>
                      <td>{g.low_score_items}</td>
                      <td>
                        <button
                          className="btn compact"
                          onClick={() =>
                            filter({
                              tool_name: g.tool_name,
                              code: g.code,
                              failure_reason: g.failure_reason,
                              ...(g.component
                                ? { component: g.component }
                                : {}),
                            })
                          }
                        >
                          相关题
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.issues.length && <p>当前范围没有记录到执行问题。</p>}
            <p className="analysis-hint">
              同一次外层工具与内部模型问题按调用去重。关联问题不直接等同于低分原因。
            </p>
          </>
        )}
        {tab === "behavior" && (
          <>
            <h4>预期行为 → 实际行为</h4>
            <div className="analysis-behavior-grid">
              {r.behavior_matrix.map((x) => (
                <button
                  className="analysis-issue-card"
                  key={`${x.expected}/${x.actual}`}
                  onClick={() =>
                    filter({
                      expected_action: x.expected,
                      actual_action: x.actual,
                    })
                  }
                >
                  <b>
                    {analysisValue(x.expected)} → {analysisValue(x.actual)}
                  </b>
                  <strong>{x.count} 轮</strong>
                </button>
              ))}
            </div>
            <p>
              符合预期 {r.counts.behavior_correct || 0} 轮 · 不符合预期{" "}
              {r.counts.behavior_incorrect || 0} 轮 · 有效判定{" "}
              {r.denominators.behavior_valid || 0} 轮
            </p>
          </>
        )}
      </div>
    </>
  );
}

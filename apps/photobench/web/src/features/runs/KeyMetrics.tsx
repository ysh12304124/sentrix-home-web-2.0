/**
 * 「关键指标」卡组 + 折叠的「召回指标」。逐行照搬 1.0
 * `services/photobench/frontend/src/App.vue:3130-3146`（只读权威）：
 *
 *   <h3 class="result-heading">关键指标</h3>
 *   <div class="phase-list key-metric-list"> … keyMetricRows(activeRun) … </div>
 *   <details v-if="recallMetricRows(activeRun).length" class="phase-card result-phase-card recall-metric-card">
 *     … recallMetricRows(activeRun) …
 *   </details>
 *
 * 与 1.0 的唯一形状差异：`keyMetricRows` / `recallMetricRows` 在 2.0 把 `summary`
 * 显式化（1.0 在函数体内现算 `effectiveRunSummary(activeRun)`），所以这里先算
 * `effectiveRunSummary(run)` 再传。1.0 的「召回指标」是**折叠的**（`<details>`），照搬。
 *
 * 1.0 该区块排在 `PhaseList`（Pipeline 执行阶段）之后、记忆面板之前，由 `RunDetail` 摆放。
 */
import { EvaluationSummary } from "../../components/EvaluationResult.tsx";
import { ScoringResults } from "../../components/ScoringResults.tsx";
import type { Run } from "../../api/types.ts";
import { effectiveRunSummary } from "../../lib/runSummary.ts";
import { keyMetricRows, recallMetricRows } from "../../lib/telemetry.ts";

export interface KeyMetricsProps {
  run: Run | null | undefined;
}

export function KeyMetrics({ run }: KeyMetricsProps) {
  const summary = effectiveRunSummary(run);
  const recallRows = recallMetricRows(summary);
  return (
    <>
      <ScoringResults summary={summary} />
      {summary?.legacy_scoring_label && <details className="historical-score"><summary>历史评分记录</summary><EvaluationSummary value={summary.evaluation_summary} /></details>}
      <h3 className="result-heading">运行与公共诊断指标</h3>
      <div className="phase-list key-metric-list">
        {keyMetricRows(run, summary).slice(1).map((row) => (
          <article className="phase-card key-metric-card" key={row.label}>
            <div className="phase-title">
              <b>{row.label}</b>
            </div>
            <p className="key-metric-value">{["-", "—", "未记录", "未计算", "未采集"].includes(row.value) ? "未运行采集" : row.value}</p>
            <p className="metric-calc-time">{row.note}</p>
          </article>
        ))}
      </div>
      {recallRows.length ? (
        <details className="phase-card result-phase-card recall-metric-card">
          <summary className="phase-title">
            <b>历史图片召回指标</b>
          </summary>
          <p className="metric-calc-time">
            此处保留原图片候选口径；图视频联合排序的工具媒体R@5见上方指标卡。
          </p>
          <div className="phase-metrics">
            {recallRows.map((row) => (
              <div className="phase-metric" key={row[0]}>
                <span>{row[0]}</span>
                <strong>{row[1]}</strong>
                <small>{row[2]}</small>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </>
  );
}

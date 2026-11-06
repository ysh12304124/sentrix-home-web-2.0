import { scoreChoices } from "../analysis/presentation.ts";
import { OPEN_PROFILE, PROFILE_LABELS } from "../../lib/scoring.ts";
/**
 * 运行详情外壳。逐行照搬 1.0
 * `services/photobench/frontend/src/App.vue:3039-3058`（只读权威）：标题 / 状态 /
 * 导出轨迹控制（0/1/2 分勾选）/ `run-meta` / `fatal_error` 横幅，并装配
 * `RejudgeCard`、`PhaseList`。
 *
 * **`runDetailRef` 不在这里**：`useRuns` 的 `runDetailRef` 在 1.0 对应的锚点是
 * `#qa-results`（`App.vue:2372`，`scrollToRunDetail` 的目标）。组合层已把它传给
 * `QaResults` 的 `resultRef`，所以详情壳不再挂任何滚动 ref。
 *
 * **「导出 SFT JSON」按钮**：当前 1.0 删了它、最老备份（`App.vue.bak.codex-macro-*`）
 * 有，后端端点一直在（见 `docs/refactor/s10-deviations.md` §4.3）。按裁决加回，与
 * 「导出轨迹 JSON」并排；URL 生成在 `useRunDetail.exportSftTraces`。备份里这个按钮
 * 没有 `title`（当前 `导出轨迹 JSON` 的 `title` 保留原样）。
 *
 * **阶段 3d 的三块**（1.0 `:3147-3431`）：
 *   1. 记忆层级与有效证据链 —— `MemoryLayerPanel`（1.0 `:3147-3284`）；
 *   2. 结果指标 —— 标题 + `result-phase-list`（1.0 `:3285-3431`），第一张卡是
 *      `TelemetryCard`（1.0 `:3289-3330`），其后是 `ResultMetrics`（1.0 `:3331-3430`）。
 *
 * 数据来源：`activeRun` 已有；记忆面板那批（`memoryEffectiveness` / `keyframeAnalysis` /
 * `qaDetails` / `qaPage` / `manifests` / `summary`）由组合层按 `MemoryLayerPanelProps`
 * 传入（`useRunDetail` + 逐题 hook 的产出）——本组件不自己发请求。缺省（组合层尚未接入）
 * 时不渲染该面板，其余两块只依赖 `activeRun`，照常渲染。
 */
import type { ReactNode } from "react";

import type { GraphQuality, Run } from "../../api/types.ts";
import { albumName, duration, fmtDate, modelName, qaName } from "../../lib/format.ts";
import { MemoryLayerPanel } from "../memory/MemoryLayerPanel.tsx";
import type { MemoryLayerPanelProps } from "../memory/MemoryLayerPanel.tsx";
import { ErrorAnalysisPanel } from "../analysis/ErrorAnalysisPanel.tsx";
import { KeyMetrics } from "./KeyMetrics.tsx";
import { ExecutionHistory } from "./ExecutionHistory.tsx";
import { RunTaskStatus } from "./RunTaskStatus.tsx";
import { RejudgeCard } from "./RejudgeCard.tsx";
import type { RejudgeCardProps } from "./RejudgeCard.tsx";
import { ResultMetrics } from "./ResultMetrics.tsx";
import { GraphQualityPanel } from "./GraphQualityPanel.tsx";
import { TelemetryCard } from "./telemetry/TelemetryCard.tsx";

/** 1.0 `:3057` 的 `<p class="run-meta">` 里 reuse/build 两段模板。 */
function runScopeMeta(run: Run): ReactNode {
  if (run.mode === "reuse") {
    const reused = Array.isArray(run.scope_reused_from_runs) ? (run.scope_reused_from_runs as unknown[]) : [];
    const album = run.scope_name || run.scope_id || (run.existing_scope_id ? String(run.existing_scope_id) : "");
    return (
      <>
        {" · 复用相册 "}
        {album}
        {reused.length > 0 ? (
          <span>（源自 run {reused.join("、")}）</span>
        ) : (
          <span>（外部创建，非编排器产物）</span>
        )}
      </>
    );
  }
  if (run.mode === "build") {
    return (
      <>
        {" · 产出相册 "}
        {run.scope_id || "-"}（已保留，可在复用测评中使用）
      </>
    );
  }
  return null;
}

/** 1.0 `:3049-3052` 的 checkbox group（值就是字符串 "0"/"1"/"2"）。 */
function toggleScore(current: string[], value: string, checked: boolean): string[] {
  const next = new Set(current);
  if (checked) next.add(value);
  else next.delete(value);
  return [...next];
}

export interface RunDetailProps {
  readProfile?: string;
  onReadProfileChange?: (value:string) => void;
  recovery?: ReactNode;
  activeRun: Run | null;
  exportScores: string[];
  onExportScoresChange: (scores: string[]) => void;
  onExportTraces: () => void;
  onExportSftTraces: () => void;
  rejudge: RejudgeCardProps;
  /** 1.0 `:3147-3284` 记忆层级面板的数据；缺省（组合层尚未接入）时不渲染该面板。 */
  memory?: MemoryLayerPanelProps;
  graphQuality?: GraphQuality | null;
}

export function RunDetail({
  activeRun, recovery, readProfile = OPEN_PROFILE, onReadProfileChange,
  exportScores,
  onExportScoresChange,
  onExportTraces,
  onExportSftTraces,
  rejudge,
  memory,
  graphQuality,
}: RunDetailProps) {
  return (
    <section id="detail-region">
      {!activeRun ? (
        <div className="section muted">点击上方列表中的某条记录查看详情</div>
      ) : (
        <section className="section detail-section">
          <div className="section-head detail-section-head">
            <h2>
              {modelName(activeRun)} · {albumName(activeRun)} · {qaName(activeRun)}
            </h2>
            <div className="detail-head-tools">
              <RunTaskStatus run={activeRun} />
              <details className="export-control"><summary>导出轨迹</summary><div className="export-control-body">
                <label>筛分标准 <select aria-label="导出筛分标准" value={readProfile} onChange={e => onReadProfileChange?.(e.target.value)}>{Object.entries(PROFILE_LABELS).map(([id,label]) => <option key={id} value={id}>{label}</option>)}<option value="">历史评分</option></select></label>
                <span className="field-label">导出轨迹</span>
                <span className="export-score-filter">
                  {scoreChoices({evaluation_summary: readProfile ? {scorer:readProfile === OPEN_PROFILE ? "answer_quality" : "choice", max_score:readProfile === OPEN_PROFILE ? 2 : 1} : activeRun.summary?.evaluation_summary}).filter(s => s.value !== "None").slice().reverse().map(({value, label}) => (
                    <label className="checkbox-inline" key={value}>
                      <input
                        type="checkbox"
                        value={value}
                        checked={exportScores.includes(value)}
                        onChange={(event) => onExportScoresChange(toggleScore(exportScores, value, event.target.checked))}
                      />
                      {label}
                    </label>
                  ))}
                </span>
                <button className="btn compact" onClick={onExportTraces} title="每题导出 planner 完整输入/输出 + 完整轨迹">
                  导出轨迹 JSON
                </button>
                <button className="btn compact" onClick={onExportSftTraces}>
                  导出 SFT JSON
                </button>
              </div></details>
            </div>
          </div>
          <p className="run-meta">
            最近答题开始 {fmtDate(activeRun.started_at)} · 本次执行耗时 {duration(activeRun)}
          </p>
          <details className="run-scope-source"><summary>相册来源</summary><p className="run-meta">{runScopeMeta(activeRun)}</p></details>
          {activeRun.mode !== "build" ? <details className="recovery-panel" aria-label="续跑与评分" key="recovery-actions">
            <summary className="recovery-panel-head"><h3>续跑与评分</h3><span>展开后可继续答题或为已有回答评分</span></summary>
            <div className="recovery-grid">
              <div className="recovery-column" aria-label="断点续跑">{recovery ?? <><h4>断点续跑</h4><p className="recovery-note">正在读取续跑条件。</p></>}</div>
              <RejudgeCard {...rejudge} embedded />
            </div>
          </details> : null}
          <KeyMetrics run={activeRun} />
          <ExecutionHistory run={activeRun} />
          <ErrorAnalysisPanel key={[activeRun.run_id,activeRun.selected_scoring_profile,activeRun.selected_scoring_batch].join(":")} scoringProfile={OPEN_PROFILE} scoringBatch={String(activeRun.selected_scoring_batch || "")} runId={activeRun.run_id} terminal={["completed", "completed_with_errors", "failed", "cancelled", "interrupted"].includes(activeRun.status || "")} />
          {memory ? <MemoryLayerPanel key={`memory-${activeRun.run_id}`} {...memory} /> : null}
          <GraphQualityPanel quality={graphQuality ?? null} />
          <h3 className="result-heading">结果指标</h3>
          {activeRun.persistence?.status ? <p className="error">结果保存异常：{String(activeRun.persistence.status)} · {String(activeRun.persistence.error || "")}</p> : null}
          <div className="result-phase-list">
            <TelemetryCard run={activeRun} />
            <ResultMetrics run={activeRun} />
          </div>
        </section>
      )}
    </section>
  );
}

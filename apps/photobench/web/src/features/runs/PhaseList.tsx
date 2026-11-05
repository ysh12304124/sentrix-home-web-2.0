/** 测评阶段、流水线处理进度及已有计时的只读展示。 */
import { useEffect, useState } from "react";
import type { Phase, Run } from "../../api/types.ts";
import { EXECUTION_PHASES } from "../../lib/constants.ts";
import { phaseErrorDetails, phaseErrorSummary, phaseSummary } from "../../lib/details.ts";
import { phaseElapsed, pipelineDuration, pipelineTimingView } from "../../lib/pipelineTiming.ts";
import type { PipelineTimingGroup } from "../../lib/pipelineTiming.ts";
import { statusLabel } from "../../lib/format.ts";
import { pipelineProgressView } from "../../lib/pipelineProgress.ts";

/** 1.0 `App.vue:2195-2198`。 */
function phasePercent(phase: Phase | undefined): number {
  const progress = phase?.progress;
  return progress?.total
    ? Math.min(
        100,
        Math.round(
          (((Number(progress.processed) || 0) +
            (Number(progress.failed) || 0) +
            (Number(progress.skipped) || 0)) /
            progress.total) *
            100,
        ),
      )
    : 0;
}

export interface PhaseListProps {
  activeRun: Run;
}

const importStageLabels: Record<string, string> = {
  scanning: "扫描目录", media_processing: "分析媒体", media_staging: "读取/处理媒体",
  event_assignment: "事件归属", event_summarization: "正在总结事件",
  participant_refresh: "刷新事件参与者", family_graph: "推断家庭图谱",
  person_insights: "生成人物画像", completed: "处理结束",
};

function elapsedSeconds(start: unknown, end: number): string {
  const time = typeof start === "string" ? Date.parse(start) : NaN;
  return Number.isFinite(time) && Number.isFinite(end)
    ? String(Math.max(0, Math.floor((end - time) / 1000))) : "?";
}

function TimingTable({ group }: { group: PipelineTimingGroup }) {
  return <div className="pipeline-timing-table-wrap">
    <table className="pipeline-timing-table" aria-label={group.title}>
      <thead><tr><th>处理环节</th><th>耗时</th><th>计时口径</th></tr></thead>
      <tbody>{group.rows.map(row => <tr key={row.key} className={row.nested ? "timing-included" : undefined}>
        <th scope="row">{row.label}<small>{row.description}</small></th>
        <td className="timing-value"><strong>{row.value}</strong>{row.share ? <small>占后端 {row.share}</small> : null}</td>
        <td><span className="timing-basis">{row.basis}</span></td>
      </tr>)}</tbody>
    </table>
  </div>;
}

export function PhaseList({ activeRun }: PhaseListProps) {
  const [now, setNow] = useState(() => Date.now());
  const terminal = ["completed", "completed_with_errors", "failed", "cancelled", "interrupted"].includes(activeRun.status ?? "");
  const running = !terminal && Object.values(activeRun.phases ?? {}).some((phase) => phase?.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return (
    <>
      <h3>Pipeline 执行阶段</h3>
      {activeRun.mode === "build" ? <p>仅构建记忆空间；QA、Judge 和评分有效性不适用。</p> : null}
      <div className="phase-list execution-phase-list">
        {EXECUTION_PHASES.filter((p) => activeRun.mode !== "build" || p.key !== "qa_eval").map((phaseDef, index) => {
          const phase = activeRun.phases?.[phaseDef.key];
          const start = typeof phase?.started_at === "string" ? Date.parse(phase.started_at) : NaN;
          const displayPhase = !terminal && phase?.status === "running" && Number.isFinite(start)
            ? { ...phase, total_seconds: Math.max(0, (now - start) / 1000) } : phase;
          const legacyImport = activeRun.phases?.photo_import;
          const processingDetails = phaseDef.key === "pipeline_processing" &&
            legacyImport?.execution_mode !== "explicit" && legacyImport?.progress?.current_directory
            ? legacyImport : undefined;
          const progress = processingDetails?.progress ?? phase?.progress;
          const observation = progress?.observation;
          const finishedAt = (processingDetails ?? phase)?.finished_at ?? (terminal ? activeRun.finished_at : null);
          const end = typeof finishedAt === "string" ? Date.parse(finishedAt) : now;
          const errors = phaseErrorDetails(phase);
          const displayImport = !terminal && legacyImport?.status === "running" && typeof legacyImport.started_at === "string"
            ? { ...legacyImport, total_seconds: Math.max(0, (now - Date.parse(legacyImport.started_at)) / 1000) }
            : legacyImport;
          const flow = phaseDef.key === "pipeline_processing" ? pipelineProgressView(activeRun, now) : null;
          const timing = flow ? pipelineTimingView(displayPhase, displayImport) : null;
          const clock = flow ? phaseElapsed(flow.combined ? displayImport : displayPhase) : null;
          const status = flow?.status ?? phase?.status ?? "pending";
          return (
            <article className={phaseDef.key === "pipeline_processing" ? "phase-card pipeline-phase-card" : "phase-card"} key={phaseDef.key}>
              <div className="phase-title">
                <div className="phase-name">
                  <span className="phase-step">{index + 1}</span>
                  <b>{phaseDef.label}</b>
                </div>
                <span className={"phase-status " + status}>
                  {statusLabel(status)}
                </span>
              </div>
              {flow ? <div className="pipeline-overall">
                <div className="pipeline-overall-heading">
                  <b>{flow.label}</b>
                  <span>{clock?.seconds != null ? `${flow.combined ? "合并请求" : clock.label} ${pipelineDuration(clock.seconds)}` : ""}
                    {flow.status === "done" ? " · 100%" : ""}</span>
                </div>
                <div role="progressbar" aria-label="流水线整体进度" aria-valuemin={0} aria-valuemax={100}
                  aria-valuenow={flow.status === "done" ? 100 : undefined} aria-valuetext={flow.label}
                  className={"pipeline-step-track " + flow.status}>
                  {flow.steps.map(step => <div key={step.label} className={"pipeline-step " + step.state}>
                    <div className="pipeline-step-bar"><i
                      className={step.state === "running" && step.fraction === null ? "indeterminate" : undefined}
                      style={{ width: `${(step.fraction ?? 0) * 100}%` }} /></div>
                    <span>{step.state === "done" ? "✓ " : ""}{step.label}</span>
                  </div>)}
                </div>
                <p className="pipeline-counts">{flow.detail}</p>
                {flow.warning ? <p className="import-progress-warning">{flow.warning}</p> : null}
              </div> : phaseDef.key === "photo_import" ? <details className="phase-import-details">
                <summary><span>{phaseSummary(phaseDef.key, displayPhase, activeRun.item_count, true)}</span><span className="phase-import-toggle">登记明细</span></summary>
                <p>{phaseSummary(phaseDef.key, displayPhase, activeRun.item_count)}</p>
                <p>文件登记、去重、元数据读取与预览准备；媒体处理见流水线。</p>
              </details> : <p className="phase-summary">{phaseSummary(phaseDef.key, displayPhase, activeRun.item_count)}</p>}
              {errors.length > 0 ? (
                <details className="phase-errors">
                  <summary>{phaseErrorSummary(phase)}</summary>
                  <ul>
                    {errors.slice(0, 20).map((raw, detailIndex) => {
                      const detail = raw as Record<string, unknown>;
                      return (
                        <li key={`${String(detail.sample_id || "error")}-${detailIndex}`}>
                          <b>{String(detail.sample_id || "未知样本")}</b>
                          <span>{String(detail.reason || detail.error || "未提供原因")}</span>
                          <small>
                            {String(detail.error_type || detail.status || "错误")}
                            {detail.asset_id ? ` · ${String(detail.asset_id)}` : ""}
                          </small>
                        </li>
                      );
                    })}
                  </ul>
                  {errors.length > 20 ? <p>仅展示前 20 条，完整记录已保存在 run.json。</p> : null}
                </details>
              ) : null}
              {phaseDef.key === "photo_import" && phase?.status === "running" && phase?.progress ? (
                <div className="phase-bar">
                  <div className="phase-bar-fill" style={{ width: `${phasePercent(phase)}%` }} />
                </div>
              ) : null}
              {processingDetails || (timing && timing.groups.length > 0) ? (
                <details className="pipeline-details" key={`${activeRun.run_id}-pipeline-details`}>
                  <summary>处理步骤与耗时详情</summary>
              {processingDetails && progress?.current_directory ? (
                <div className="import-progress-panel" data-testid="import-waiting">
                  <p className="muted">历史合并请求中的处理进度；计时保留原记录。</p>
                  <div className="import-progress-head">
                    <div className="import-progress-location">
                      <span className="import-progress-eyebrow">当前目录</span>
                      <strong title={String(progress.current_directory)}>{String(progress.current_directory)}</strong>
                    </div>
                    <span className="import-progress-clock">本目录请求已用 {elapsedSeconds(progress?.request_started_at, end)} 秒</span>
                  </div>
                  <div className="import-progress-stats">
                    <span><b>{observation?.completed ?? "?"}</b><small>已处理</small></span>
                    <span><b>{observation?.total ?? "?"}</b><small>媒体总数</small></span>
                    <span><b>{observation?.imported ?? "?"}</b><small>新增</small></span>
                    <span><b>{observation?.deduplicated ?? "?"}</b><small>去重</small></span>
                    <span><b>{observation?.failed ?? "?"}</b><small>失败</small></span>
                  </div>
                  <div className="import-progress-meta">
                    <span className="import-progress-stage">
                      {importStageLabels[observation?.stage ?? ""] ?? observation?.stage ?? "逐媒体进度尚不可用"}
                      {observation?.current_file ? ` · ${observation.current_file}` : ""}
                    </span>
                    <span className="import-progress-freshness">
                      {observation ? `后端状态：${observation.status ?? "未知"} · 更新于 ${observation.updated_at ?? "未记录"}` : "等待进度接口"}
                    </span>
                  </div>
                  <div className="import-progress-directory">
                    已完成目录 {Number(progress.directories_done ?? 0)}/{Number(progress.directories_total ?? 0)}
                  </div>
                </div>
              ) : null}
              {processingDetails && observation &&
                typeof observation.total === "number" && observation.total > 0 &&
                typeof observation.completed === "number" ? (
                  <div className="import-progress-bar-wrap">
                    <progress aria-label="当前目录媒体处理进度" max={observation.total} value={observation.completed} />
                    <small>媒体处理进度；事件总结和人物画像另行收尾。</small>
                  </div>
                ) : null}
                  {phase?.progress ? <p className="muted">媒体处理后还需完成事件总结、图谱和人物信息更新。</p> : null}
                  {timing ? <div className="pipeline-timing">
                    {timing.overview.map(row => <p className="muted" key={row.label}>{row.label} <strong>{row.value}</strong> · {row.note}</p>)}
                    {timing.groups.map(group => group.collapsed
                      ? <details className="pipeline-timing-group" key={group.key}>
                        <summary>{group.title}</summary><p className="muted">{group.note}</p><TimingTable group={group} />
                      </details>
                      : <section className="pipeline-timing-group" key={group.key} aria-label={group.title}>
                        <div className="pipeline-timing-heading"><h4>{group.title}</h4>{group.summary ? <strong>{group.summary}</strong> : null}</div>
                        <p className="muted">{group.note}</p><TimingTable group={group} />
                      </section>)}
                    <p className="muted">未记录的独立计时保持空缺；细分时延以已保存的调用统计为准。</p>
                  </div> : null}
                </details>
              ) : null}
            </article>
          );
        })}
      </div>
    </>
  );
}

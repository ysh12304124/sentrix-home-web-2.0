/**
 * 评测记录表。逐行照搬 1.0 `services/photobench/frontend/src/App.vue:2989-3037`（只读权威）。
 *
 * 纯展示：运行列表与分页状态全部来自 `useRuns`（运行列表 hook，见 `hooks/useRuns.ts`），
 * 本组件不发请求、不持数据。
 *
 * `data-testid="runs-view"` 是 2.0 视图外壳的测试锚点（`App.test.tsx`）。挂在
 * `#runs-region` 上而不是另造一层包装节点——1.0 没有那层节点，多一个会偏离 DOM。
 *
 */
import { qaProgress } from "../../lib/qaProgress.ts";
import { OPEN_PROFILE, PROFILE_LABELS } from '../../lib/scoring.ts';
import { type RefObject } from "react";

import type { Run } from "../../api/types.ts";
import type { RunPageState } from "../../hooks/useRuns.ts";
import { albumName, duration, fmtDate, fmtPct, modelName } from "../../lib/format.ts";
import { modeBadgeClass, modeLabel } from "../../lib/qaBrowser.ts";
import { RunTaskStatus } from "./RunTaskStatus.tsx";

function RunProgress({ run }: { run: Run }) {
  const progress = qaProgress(run);
  const showOriginal = progress.applicable && progress.judgeEnabled;
  if (!showOriginal) return <span className="muted">—</span>;
  return <div className="run-progress-count">
    {showOriginal && progress.agent !== null ? <div>{progress.copiedAnswers ? "答卷" : "答题"} <span>{progress.agentRatio}</span></div> : null}
    {showOriginal && <div aria-label={`有效评分 ${progress.validRatio}`}>评分 <span>{progress.validRatio}</span></div>}
  </div>;
}

export interface RunsTableProps {
  runs: Run[];
  runPage: RunPageState;
  activeRunId: string | null;
  onSelectRun: (run: Run) => void;
  onDeleteRun: (run: Run) => void;
  onChangeRunPage: (page: number) => void;
  /** 1.0 `#runs-region` 的滚动落点，由 `useRuns` 提供，不要自己 querySelector。 */
  regionRef: RefObject<HTMLElement | null>;
}

export function RunsTable({
  runs,
  runPage,
  activeRunId,
  onSelectRun,
  onDeleteRun,
  onChangeRunPage,
  regionRef,
}: RunsTableProps) {
  return (
    <section id="runs-region" className="section" ref={regionRef} data-testid="runs-view">
      <div className="section-head">
        <h2>评测记录</h2>
        {runPage.pages > 1 ? (
          <div className="pager">
            <button
              className="btn ghost compact"
              disabled={!runPage.has_previous}
              onClick={() => onChangeRunPage(runPage.page - 1)}
            >
              上一页
            </button>
            <span>
              {runPage.page} / {runPage.pages}
            </span>
            <button
              className="btn ghost compact"
              disabled={!runPage.has_next}
              onClick={() => onChangeRunPage(runPage.page + 1)}
            >
              下一页
            </button>
          </div>
        ) : null}
        <span className="muted">共 {runPage.total} 条</span>
      </div>
      {["pending", "running"].includes(String(runPage.scan?.state)) ? (
        <p className="muted">历史记录正在加载（已读取 {Number(runPage.scan?.scanned || 0)} 条），当前列表尚未完整。</p>
      ) : null}
      {runPage.scan?.state === "failed" ? <p className="error">历史记录加载失败：{String(runPage.scan.error || "未知原因")}</p> : null}
      <div className="runs-list">
        <table>
          <thead>
            <tr>
              <th>模型</th>
              <th>相册</th>
              <th>最近答题</th>
              <th>本次耗时</th>
              <th>状态</th>
              <th>累计题数</th>
              <th>媒体召回率</th>
              <th>答案得分</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (

              <tr
                key={run.run_id}
                className={"run-row" + (activeRunId === run.run_id || run.task_run_ids?.includes(activeRunId || "") ? " selected" : "")}
                onClick={() => onSelectRun(run)}
              >
                <td>
                  <b>{modelName(run)}</b>
                  <span className={"mode-badge " + modeBadgeClass(run.mode)}>{modeLabel(run.mode)}</span>
                  {run.execution_versions && <small className="muted">{run.execution_versions.length} 次执行</small>}
                </td>
                <td className="muted small">{albumName(run)}</td>
                <td className="muted small">{fmtDate(run.started_at)}</td>
                <td className="muted small">{duration(run)}</td>
                <td>
                  <RunTaskStatus run={run} />
                </td>
                <td><RunProgress run={run} /></td>
                <td>
                  {run.summary?.media_retrieval_recall_macro == null && run.summary?.retrieval_recall_macro == null && run.summary?.retrieval_recall_mean == null ? "未运行采集" : fmtPct(
                    run.summary?.media_retrieval_recall_macro ?? run.summary?.retrieval_recall_macro ?? run.summary?.retrieval_recall_mean,
                  )}
                </td>
                <td className='run-scoring-cell'>
                  {Object.entries(PROFILE_LABELS).map(([profile,label]) => {
                    const scoring=run.summary?.scoring as {profiles?:Record<string,{total_score?:number|null;scored_mean?:number|null;normalized_mean?:number|null;scored?:number;total?:number;status?:string}>}|undefined;
                    const result=scoring?.profiles?.[profile];
                    const score=profile === OPEN_PROFILE
                      ? typeof result?.scored_mean === 'number' ? result.scored_mean.toFixed(2)+' / 2.00 分' : '—'
                      : fmtPct(result?.total_score ?? result?.normalized_mean);
                    return <div className="run-score-line" key={profile}><span title={label}>{profile === OPEN_PROFILE ? '开放式0/1/2' : label}</span><span>{!result || result.status==='not_collected' ? '未运行采集' : result.total_score == null ? '均值 '+score+'（未完成）' : score}{result?.scored != null ? ' · '+result.scored+'/'+result.total : ''}</span></div>;
                  })}

                </td>
                <td>
                  <button
                    className="btn danger compact"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteRun(run);
                    }}
                  >
                    删除
                  </button>
                </td>
              </tr>

            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

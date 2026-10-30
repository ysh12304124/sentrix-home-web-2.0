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
import type { RefObject } from "react";

import type { Run } from "../../api/types.ts";
import type { RunPageState } from "../../hooks/useRuns.ts";
import { albumName, duration, fmtDate, fmtPct, modelName, statusLabel } from "../../lib/format.ts";
import { modeBadgeClass, modeLabel } from "../../lib/qaBrowser.ts";

/** A sample completes after a valid Judge score; Agent and Judge attempt counts are not substitutes. */
function RunProgress({ run }: { run: Run }) {
  const phase = run.phases?.qa_eval;
  const progress = phase?.progress;
  if (run.mode === "build" || progress?.judge_concurrency === 0) return <span className="muted">—</span>;
  const count = (value: unknown): number | null =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
  const completed = count(run.summary?.judge_valid_count);
  const total = run.qa_count === null ? null :
    count(run.qa_count) ?? count(run.summary?.total) ?? count(progress?.total) ?? count(progress?.judge_total) ?? count(phase?.judge_total);
  const label = completed === null && total === null ? "—" : `${completed ?? "—"}/${total ?? "—"}`;
  return <span className="run-progress-count" aria-label={`评分完成 ${label}`}>{label}</span>;
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
              <th>开始时间</th>
              <th>耗时</th>
              <th>状态</th>
              <th>进度</th>
              <th>媒体召回率</th>
              <th>质量均分</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.run_id}
                className={"run-row" + (activeRunId === run.run_id ? " selected" : "")}
                onClick={() => onSelectRun(run)}
              >
                <td>
                  <b>{modelName(run)}</b>
                  <span className={"mode-badge " + modeBadgeClass(run.mode)}>{modeLabel(run.mode)}</span>
                </td>
                <td className="muted small">{albumName(run)}</td>
                <td className="muted small">{fmtDate(run.started_at)}</td>
                <td className="muted small">{duration(run)}</td>
                <td>
                  <span className={"phase-status " + (run.status || "")}>{statusLabel(run.status)}</span>
                </td>
                <td><RunProgress run={run} /></td>
                <td>
                  {fmtPct(
                    run.summary?.media_retrieval_recall_macro ??
                      run.summary?.retrieval_recall_macro ??
                      run.summary?.retrieval_recall_mean,
                  )}
                </td>
                <td>{run.summary?.answer_quality_mean ?? "-"}</td>
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

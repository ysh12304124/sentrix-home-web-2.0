import type { RejudgeTask } from "../../api/types.ts";
import { statusLabel } from "../../lib/format.ts";

export function ScoringTaskLogs({ tasks }: { tasks: RejudgeTask[] }) {
  return <section aria-label="评分任务日志">
    <h3>评分任务日志</h3>
    {[...tasks].reverse().map((task, index) => <details className="scoring-task-log phase-card" open={index === 0}
      key={task.rejudge_id || `legacy-${index}`}>
      <summary className="phase-title">
        <b>{index === 0 ? "最近评分任务" : "历史评分任务"} · {task.started_at || "时间未记录"}</b>
        <span className={`phase-status ${task.status || "pending"}`}>{statusLabel(task.status)}</span>
      </summary>
      <p className="small muted">任务 ID：{task.rejudge_id || "未记录"}</p>
      <p>本轮评分 {task.completed ?? 0}/{task.total ?? "—"} 项 · 失败 {task.failed ?? "未记录"} 项</p>
      <p className="small muted">复用已保存的回答；本轮状态和错误独立记录。每题、每轮、每套标准各算一项，累计题数按题目去重。</p>
      {task.error ? <p className="error">{task.error}</p> : null}
      {!!task.error_details?.length && <details className="phase-errors">
        <summary>本轮 {task.error_details.length} 条错误记录</summary>
        <ul>{task.error_details.map((error, i) => <li key={i}>
          <b>{error.sample_id}</b><span>{error.reason}</span>
          <small>{error.profile_id} · 第 {(error.turn_index ?? 0) + 1} 轮</small>
        </li>)}</ul>
      </details>}
      {task.results ? <details>
        <summary>本轮逐项日志 · {task.results.length} 项</summary>
        <div className="pipeline-timing-table-wrap"><table className="pipeline-timing-table">
          <thead><tr><th>题目</th><th>标准 / 对话轮次</th><th>结果</th></tr></thead>
          <tbody>{task.results.map((result, i) => <tr key={i}>
            <th scope="row">{result.sample_id}</th>
            <td>{result.profile_id} / {(result.turn_index ?? 0) + 1}</td>
            <td>{statusLabel(result.status)}{result.score != null ? ` · ${result.score}/${result.max_score}` : ""}</td>
          </tr>)}</tbody>
        </table></div>
      </details> : <p className="small muted">此历史任务未记录逐项执行日志。</p>}
    </details>)}
  </section>;
}

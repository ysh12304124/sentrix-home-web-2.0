import type { Run } from "../../api/types.ts";
import { statusLabel } from "../../lib/format.ts";

export function RunTaskStatus({ run }: { run: Run }) {
  const latest = run.execution_versions?.at(-1);
  const task = latest ? latest.kind === 'scoring' ? latest : null : run.rejudge;
  if (!task) return <span className={`phase-status ${run.status || ""}`}>{statusLabel(run.status)}</span>;
  return <div className="run-status-stack">
    <span className={`phase-status ${task.status || ""}`}>
      {task.status === "running" ? "补评运行中" : `补评${statusLabel(task.status)}`}
    </span>
    <small>最近评分{task.status === "running" ? ` · ${task.completed ?? 0}/${task.total ?? "—"} 项` : ""}</small>
  </div>;
}

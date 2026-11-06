import { useEffect, useState } from "react";
import { api } from "../../api/client.ts";
import type { ActiveWorkStatus } from "../../api/types.ts";

/** Show the same live ownership that gates the analysis supervisor. */
export function AnalysisScheduling({ jobId, status }: { jobId?: string; status: string }) {
  const [work, setWork] = useState<ActiveWorkStatus>();
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    let timer: number;
    async function refresh() {
      try {
        const value = await api<ActiveWorkStatus>("/active-work", { signal: abort.signal });
        if (!abort.signal.aborted) { setWork(value); setError(""); }
      } catch (e) {
        if (!abort.signal.aborted) setError(`等待原因读取失败：${String(e)}`);
      } finally {
        if (!abort.signal.aborted) timer = window.setTimeout(() => void refresh(), 3000);
      }
    }
    void refresh();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [jobId]);
  const blockers = work?.work.filter(entry => entry.kind !== "analysis") ?? [];
  const analyses = work?.work.filter(entry => entry.kind === "analysis") ?? [];
  const queued = analyses.filter(entry => !entry.worker_alive);
  const position = queued.findIndex(entry => entry.id === jobId);
  const ahead = Math.max(0, position) + analyses.filter(entry => entry.worker_alive && entry.id !== jobId).length;
  return <div className="analysis-hint" role="status">
    {error ? <p role="alert">{error}</p> : blockers.length > 0 ? <>
      <p>等待测评结束。测评优先，AI 分析会在测评及后台任务结束后自动继续，无需重复启动。</p>
      <p>当前任务：{blockers.map(entry => entry.id).join("、")}</p>
      {status === "queued" && <p>本次分析尚未调用模型。</p>}
    </> : status === "queued" && work ? <p>{ahead > 0
      ? `等待前面的 ${ahead} 个分析任务结束，随后自动开始。`
      : "分析已排队，等待工作进程启动。"}</p> : null}
  </div>;
}

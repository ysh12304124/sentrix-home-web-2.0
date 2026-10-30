import { useEffect, useState } from "react";
import { api } from "../../api/client.ts";
import type { Run, RunPage } from "../../api/types.ts";

const terminalStates = ["completed", "completed_with_errors", "failed", "cancelled", "interrupted"];
export function ComparisonPicker({ runId, value, onChange, disabled }: {
  runId: string; value: string; onChange: (value: string) => void; disabled: boolean;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [more, setMore] = useState(false);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const abort = new AbortController();
    setLoading(true);
    setError("");
    void api<RunPage>(`/runs?page=${page}&page_size=100`, { signal: abort.signal }).then(result => {
      if (!alive) return;
      setRuns(previous => {
        const unique = new Map((page === 1 ? [] : previous).map(run => [run.run_id, run]));
        (result.runs || []).forEach(run => unique.set(run.run_id, run));
        return [...unique.values()];
      });
      setMore(result.has_next);
    }).catch(e => { if (alive) setError(String(e)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; abort.abort(); };
  }, [page, runId, retry]);
  const candidates = runs.filter(run => run.run_id !== runId && run.mode !== "build" && terminalStates.includes(run.status || ""));
  const caption = (run: Run) => `${(run.started_at || run.created_at) ? new Date((run.started_at || run.created_at)!).toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}) : "日期未记录"} · ${run.model_name || run.model_profile || "模型未记录"} · ${run.qa_name || run.qa_set || "题集未记录"} · ${run.run_id}`;
  const visible = candidates.filter(run => run.run_id === value || caption(run).toLowerCase().includes(search.toLowerCase()));
  return <div className="analysis-toolbar analysis-run-picker">
    <label>查找历史运行<input aria-label="搜索对比运行" placeholder="搜索已加载的日期、模型或题集" value={search} onChange={e => setSearch(e.target.value)} /></label>
    <label>选择对比运行<select aria-label="选择对比运行" value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
      <option value="">请选择一轮历史测评</option>
      {visible.map(run => <option key={run.run_id} value={run.run_id}>{caption(run)}</option>)}
    </select></label>
    {more && <button className="btn compact" disabled={loading} onClick={() => setPage(p => p + 1)}>加载更早记录</button>}
    {loading && <span role="status">读取历史运行…</span>}
    {error && <p role="alert">历史运行读取失败：{error} <button className="btn compact" onClick={() => setRetry(n => n + 1)}>重试</button></p>}
    {!loading && !error && <small>可选 {candidates.length} 轮测评{more ? "，可加载更早记录" : ""}</small>}
  </div>;
}

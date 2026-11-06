import { useEffect, useRef, useState } from "react";
import { api, post } from "../../api/client.ts";
import { activeAnalysis, type AnalysisFilters, type AnalysisJob } from "../../api/analysis.ts";
import { ComparisonPicker } from "./ComparisonPicker.tsx";
import { AnalysisScheduling } from "./AnalysisScheduling.tsx";

import { ComparisonJourney, ComparisonIdentity } from "./ComparisonJourney.tsx";
import { ComparisonFindings } from "./ComparisonFindings.tsx";
import { comparisonRef, type AIComparisonReport } from "./comparison.ts";
const states: Record<string, string> = { queued: "等待运行", running: "对比中", paused: "等待测评结束", completed: "对比完成", failed: "对比失败", cancelled: "已取消", interrupted: "已中断", cancelling: "正在取消" };
type Props={scoringProfile?:string;runId:string;jobId:string;scope:string;filters:AnalysisFilters;modelRole:string;disabled:boolean};
export function AIComparison(props:Props){return <ComparisonPanel key={`${props.runId}:${props.jobId}`} {...props}/>;}
function ComparisonPanel({scoringProfile='',runId,jobId,scope,filters,modelRole,disabled}:Props){
  const [comparisonFilter, setComparisonFilter] = useState("all");
  const [otherRun, setOtherRun] = useState("");
  const [job, setJob] = useState<AnalysisJob<AIComparisonReport> | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const lock = useRef(false);
  const revision = useRef(0);
  const [focus,setFocus]=useState<{index:number;refs:string[]}>();
  const [casesOpen,setCasesOpen]=useState(false);
  const lifecycle = useRef<AbortController | null>(null);
  useEffect(() => { const controller = new AbortController(); lifecycle.current = controller; return () => controller.abort(); }, []);
  useEffect(()=>{
    const abort=new AbortController(),version=revision.current;
    void (async()=>{
      const history=await api<AnalysisJob<AIComparisonReport>[]>(`/runs/${encodeURIComponent(runId)}/analyses`,{signal:abort.signal});
      if(abort.signal.aborted || version!==revision.current)return;
      const saved=history.find(entry=>entry.kind==="ai_compare" && entry.options?.left_job===jobId);
      if(!saved?.job_id)return;
      const restored=await api<AnalysisJob<AIComparisonReport>>(`/analyses/${saved.job_id}`,{signal:abort.signal});
      if(!abort.signal.aborted && version===revision.current)setJob(restored);
    })().catch(e=>{if(!abort.signal.aborted && version===revision.current)setError(`已有对比读取失败：${String(e)}`);});
    return ()=>abort.abort();
  },[runId,jobId]);

  useEffect(() => {
    if (!activeAnalysis(job)) return;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      void api<AnalysisJob<AIComparisonReport>>(`/analyses/${job?.job_id}`, { signal: abort.signal }).then(setJob).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    }, 1500);
    return () => { window.clearTimeout(timer); abort.abort(); };
  }, [job]);
  async function start() {
    if (lock.current) return;
    revision.current++;
    lock.current = true; setPending(true); setError("");
    const signal = lifecycle.current!.signal;
    // Freeze shared controls at the click, including while the other rules snapshot is prepared.
    const options = { kind: "ai_compare", left_job: jobId, comparison_filter: comparisonFilter, scope, filters: { ...filters }, model_role: modelRole };
    try {
      const path = `/runs/${encodeURIComponent(otherRun)}/analysis`;
      let other = await api<AnalysisJob>(path + (scoringProfile ? '?' + new URLSearchParams({scoring_profile:scoringProfile}) : ''), { signal });
      if (!other.job_id || other.stale || (!activeAnalysis(other) && other.status !== "completed")) other = await post(path, { kind: "rules", ...(scoringProfile ? {scoring_profile:scoringProfile} : {}) });
      while (activeAnalysis(other)) {
        await new Promise<void>(resolve => window.setTimeout(resolve, 1500));
        if (signal.aborted) return;
        other = await api<AnalysisJob>(`/analyses/${other.job_id}`, { signal });
      }
      if (signal.aborted) return;
      if (other.status !== "completed" || other.stale) throw new Error("另一轮的分析快照未就绪，请重新生成后再对比。");
      setJob(await post(`/runs/${encodeURIComponent(runId)}/analysis`, { ...options, right_job: other.job_id }));
      setPage(1);
    } catch (e) { if (!signal.aborted) setError(String(e)); }
    finally { lock.current = false; if (!signal.aborted) setPending(false); }
  }
  const report = job?.report;
  function locate(refs:string[]){
    const parsed=refs.map(comparisonRef).find(Boolean);
    if(!parsed || !report)return;
    const pair=report.cases.find(c=>parsed.side==="left" ? c.index===parsed.index : c.right_index===parsed.index);
    if(!pair)return;
    const position=report.cases.indexOf(pair);
    setPage(Math.floor(position/20)+1);setCasesOpen(true);setFocus({index:pair.index,refs});
  }
  return <section className="analysis-comparison-card">
    <h4>两次测评 AI 对比</h4>
    <p className="analysis-hint">沿用上方分析范围和模型。逐题对比 QA、文字 GT、GT 图片与两边完整保存的执行轨迹，归纳一致点和分叉点。此处对全部符合条件的配对题目进行对比。</p>
    <label className="analysis-toolbar">对比题目<select aria-label="对比题目" value={comparisonFilter} disabled={pending || activeAnalysis(job)} onChange={e => setComparisonFilter(e.target.value)}><option value="all">对比全部</option><option value="score_changed">仅对比得分不同项</option></select></label>
    {comparisonFilter === "score_changed" && <p className="analysis-hint">只纳入两边均有有效评分且得分不同的题目，未评分题不纳入。</p>}
    <ComparisonPicker runId={runId} value={otherRun} onChange={setOtherRun} disabled={pending || activeAnalysis(job)} />
    <div className="analysis-toolbar">
      <button className="btn primary" disabled={disabled || pending || activeAnalysis(job) || !otherRun} onClick={() => void start()}>开始 AI 对比</button>
      {pending && <span role="status">正在准备另一轮的分析快照…</span>}
      {activeAnalysis(job) && <button className="btn compact" onClick={() => void post<AnalysisJob<AIComparisonReport>>(`/analyses/${job?.job_id}/cancel`, {}).then(setJob).catch(e => setError(String(e)))}>取消对比</button>}
    </div>
    {(error || job?.error) && <p role="alert">{error || job?.error}</p>}
    {job && <p role="status">{states[job.status] || job.status} · 已处理 {job.processed}/{report?.requested ?? "…"} 题{job.stale ? " · 来源或提示词已更新" : ""}</p>}
    {activeAnalysis(job) && <AnalysisScheduling jobId={job?.job_id} status={job!.status} />}
    {report && <>
      <p>固定范围 {report.scope?.scope_count ?? report.requested} 题 · {report.scope?.comparison_filter === "score_changed" ? "仅得分不同" : "对比全部"} · 纳入 {report.requested} 题</p>
      <p>引用合格 {report.coverage.valid} · 失败或引用无效 {report.coverage.failed} · 未匹配 {report.coverage.unmatched} · 未处理 {report.coverage.unprocessed}</p>
      <div className="comparison-sides">{(["left","right"] as const).map(side=><ComparisonIdentity key={side} side={side} source={report.sources[side]}/>)}</div>
      <details className="comparison-boundaries"><summary>对比依据与分析范围</summary><p className="analysis-hint">{report.note}</p></details>
      {report.overview && <><h5>整体对比结论</h5><ComparisonFindings value={report.overview} onLocate={locate} overview/></>}
      {report.summary_status !== "completed" && <p>总结尚未完成{report.summary_error ? `：${report.summary_error}` : ""}，已完成的逐题结果保留如下。</p>}
      <details open={casesOpen} onToggle={e=>{if(e.target===e.currentTarget)setCasesOpen(e.currentTarget.open);}}><summary>逐题轨迹对比（{report.cases.length}题）</summary>
        {report.cases.slice((page-1)*20,page*20).map(c=><ComparisonJourney key={`${job?.job_id}:${c.index}`} item={c} jobId={job!.job_id!} focus={focus}/>)}
        <div className="analysis-toolbar"><button className="btn compact" disabled={page === 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>第{page}页</span><button className="btn compact" disabled={page * 20 >= report.cases.length} onClick={() => setPage(p => p + 1)}>下一页</button></div>
      </details>
      <details><summary>模型与来源</summary><p>{report.model_role === "main" ? "主模型" : "Judge"} · {report.model} · {report.prompt_version}</p><pre>{JSON.stringify(report.sources, null, 2)}</pre></details>
    </>}
  </section>;
}

import { useEffect, useState } from 'react';
import { api } from '../../api/client.ts';
import type { ExecutionVersion, Run } from '../../api/types.ts';
import { fmtDate, statusLabel } from '../../lib/format.ts';
import { InitialExecutionPhases } from './PhaseList.tsx';
import { ScoringTaskLogs } from './ScoringTaskLogs.tsx';

export function ExecutionHistory({ run }: { run: Run }) {
  const versions = run.execution_versions?.length ? run.execution_versions : [
    {execution_id:run.run_id, run_id:run.run_id, kind:'answers', status:run.status, started_at:run.started_at},
    ...[...(run.rejudge_history || []), ...(run.rejudge ? [run.rejudge] : [])].map(t => ({...t, execution_id:t.rejudge_id || run.run_id+'-scoring', run_id:run.run_id, kind:'scoring'})),
  ] as ExecutionVersion[];
  const [selection, setSelection] = useState('');
  const selected = versions.find(v => v.execution_id === selection) || versions.at(-1)!;
  const [historical, setHistorical] = useState<Run | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setHistorical(null); setError('');
    if (selected.run_id === run.run_id) return;
    const controller = new AbortController();
    void api<Run>(`/runs/${encodeURIComponent(selected.run_id)}?include_history=false`, {signal:controller.signal, timeoutMs:60000})
      .then(value => {if (!controller.signal.aborted) setHistorical(value);})
      .catch(e => {if (!controller.signal.aborted) setError(String(e.message));});
    return () => controller.abort();
  }, [selected.run_id, run.run_id]);
  const source = selected.run_id === run.run_id ? run : historical;
  const task = [...(source?.rejudge_history || []), ...(source?.rejudge ? [source.rejudge] : [])].find(t => t.rejudge_id === selected.execution_id);
  return <section className="execution-history" aria-label="执行版本与日志">
    <div className="execution-history-head"><div><h3>执行记录</h3><p>累计结果展示当前答卷；下拉框查看每次执行的来源和日志。</p></div>
      <label>执行版本 <select aria-label="执行版本" value={selected.execution_id} onChange={e => setSelection(e.target.value)}>
        {[...versions].reverse().map((v, i) => <option key={v.execution_id} value={v.execution_id}>{i === 0 ? '最新 · ' : ''}{v.kind === 'scoring' ? '评分' : v.source_run_id ? '续跑答题' : '首次答题'} · {fmtDate(v.started_at)} · {statusLabel(v.status)}</option>)}
      </select></label></div>
    <p className="small muted">当前累计保存 {run.item_count ?? run.summary?.completed ?? "未记录"}/{run.qa_count ?? "未记录"} 题答卷（按题目去重）</p>
    <div className="execution-provenance">
      <span>本轮状态 <b className={'phase-status '+selected.status}>{statusLabel(selected.status)}</b></span>
      {selected.kind === 'scoring' ? <><span>范围 <b>{selected.qa_count ?? '未记录'} 题 / {selected.turn_count ?? '未记录'} 轮</b></span><span>评分项 <b>{selected.completed ?? 0}/{selected.total ?? '未记录'}</b> · 失败 {selected.failed ?? '未记录'}</span><span>Agent调用 <b>0</b></span></>
        : <><span>继承答案 <b>{selected.inherited_count ?? (selected.source_run_id ? '未记录' : 0)}</b></span><span>本轮新增保存 <b>{selected.answer_count != null && (!selected.source_run_id || selected.inherited_count != null) ? Math.max(0, selected.answer_count - (selected.inherited_count || 0)) : '未记录'}</b></span></>}
    </div>
    <details className="execution-source"><summary>来源与版本标识</summary><dl><dt>执行ID</dt><dd><code>{selected.execution_id}</code></dd><dt>答卷run</dt><dd><code>{selected.run_id}</code></dd><dt>来源run</dt><dd><code>{selected.source_run_id || (selected.kind === 'scoring' ? selected.run_id : '首次执行')}</code></dd>{selected.build_revision && <><dt>代码版本</dt><dd><code>{selected.build_revision}</code></dd></>}{selected.batch_ids && <><dt>评分批次</dt><dd>{selected.batch_ids.join(' · ')}</dd></>}</dl></details>
    <details className="execution-log" key={selected.execution_id} open><summary>查看本轮日志</summary>
      {error ? <p className="error">{error}</p> : !source ? <p>正在读取执行日志…</p> : selected.kind === 'scoring' ? task ? <ScoringTaskLogs tasks={[task]} /> : <p>此执行未保存详细日志。</p> : <>{source.fatal_error ? <p className="error">{String(source.fatal_error)}</p> : null}<InitialExecutionPhases activeRun={source}/></>}
    </details>
  </section>;
}

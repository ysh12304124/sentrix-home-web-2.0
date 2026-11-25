import { useEffect, useRef, useState } from 'react';
import { api, post } from '../../api/client.ts';
import type { InteractiveModel, Run, RunItemSummary, RunItemDetail } from '../../api/types.ts';
import { fmtMs, fmtPct, fmtTimestamp, modelName, statusLabel } from '../../lib/format.ts';
import { InteractiveModal } from './InteractiveModal.tsx';
import { ExecutionView } from './ExecutionView.tsx';
import { ModelSelect, type ModelOptions } from './ModelSelect.tsx';

function scoringLabel(run: Run): string {
  const summaries = (run.summary?.scoring as { profiles?: Record<string, unknown> } | undefined)?.profiles;
  const rows = Array.isArray(summaries) ? summaries : Object.values((summaries || {}) as Record<string, unknown>);
  return rows.map(value => {
    const row = value as Record<string, unknown>;
    return row.max_score === 2 && typeof row.scored_mean === 'number' ? `${row.scored_mean.toFixed(2)} / 2` : typeof row.total_score === 'number' ? `${(row.total_score * 100).toFixed(1)}%` : '待评分';
  }).join(' / ') || (typeof run.summary?.answer_quality_mean === 'number' ? `${run.summary.answer_quality_mean.toFixed(2)} / 2` : '未评分');
}

const isActive = (status?: string) => ['pending', 'running', 'cancelling'].includes(status || '');
type Progress = { status: string; current_phase?: string; item?: RunItemDetail; phases?: Record<string, { status?: string; error?: string }>; model?: Run };

export function RetestDialog({ run, question, models, busy, onClose }: {
  run: Run; question: RunItemSummary; models: ModelOptions; busy: boolean; onClose: () => void;
}) {
  const [model, setModel] = useState<InteractiveModel>({});
  const [modelReady, setModelReady] = useState(true);
  const [reasoning, setReasoning] = useState(true);
  const [history, setHistory] = useState<Run[]>([]);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false), [closing, setClosing] = useState(false);
  const [selected, setSelected] = useState(''), [progress, setProgress] = useState<Progress | null>(null);
  const [result, setResult] = useState<RunItemDetail | null>(null);
  const [reload, setReload] = useState(0), [loadingResult, setLoadingResult] = useState(false), [resultError, setResultError] = useState('');
  const executionView = useRef<HTMLDivElement>(null);
  const owned = useRef(''), ownedActive = useRef(false), startPromise = useRef<Promise<void> | null>(null);
  const closingRef = useRef(false);
  const load = async () => {
    try { const data = await api<{ items: Run[] }>(`/runs/${encodeURIComponent(run.run_id!)}/retests?qa_id=${encodeURIComponent(String(question.qa_id))}`); setHistory(data.items); }
    catch (e) { setError((e as Error).message); }
  };
  // The effect loads remote comparison history.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { void load(); }, [run.run_id, question.qa_id]); // oxlint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      try {
        const data = await api<Progress>(`/runs/${encodeURIComponent(selected)}/progress`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setProgress(data);
        if (selected === owned.current) ownedActive.current = isActive(data.status);
        const items = await api<{ items: RunItemSummary[] }>(`/runs/${encodeURIComponent(selected)}/items?page_size=1`, { signal: controller.signal });
        if (items.items.length) {
          const detail = await api<{ item: RunItemDetail }>(`/runs/${encodeURIComponent(selected)}/items/0`, { signal: controller.signal });
          if (!controller.signal.aborted) setResult(detail.item);
        }
        if (!isActive(data.status)) { await load(); return; }
      } catch (e) { if (!controller.signal.aborted) setResultError((e as Error).message); }
      finally { if (!controller.signal.aborted) setLoadingResult(false); }
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 1200);
    };
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [selected, reload]); // oxlint-disable-line react-hooks/exhaustive-deps
  const showRecord = (runId: string) => {
    setResult(null); setProgress(null); setResultError(''); setLoadingResult(true);
    setSelected(runId); setReload(n => n + 1);
    window.requestAnimationFrame(() => {
      executionView.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      executionView.current?.focus({ preventScroll: true });
    });
  };
  const start = () => {
    if (starting || closingRef.current || ownedActive.current || !modelReady) return;
    setStarting(true); setError('');
    startPromise.current = (async () => {
      try {
        const response = await post<{ run_id: string }>(`/runs/${encodeURIComponent(run.run_id!)}/retest`, {
          qa_id: question.qa_id, model, reasoning,
          scoring_profile: run.selected_scoring_profile || undefined,
          scoring_batch: run.selected_scoring_batch || undefined,
        });
        owned.current = response.run_id; ownedActive.current = true;
        setResultError(''); setProgress({ status: 'pending' }); setResult(null); setSelected(response.run_id);
      } catch (e) { setError((e as Error).message); }
      finally { setStarting(false); }
    })();
  };
  const close = async () => {
    if (closingRef.current) return;
    closingRef.current = true; setClosing(true); setError('');
    try {
      // Let a submitted creation return its ID so closing cannot orphan a new task.
      await startPromise.current;
      if (owned.current && ownedActive.current) {
        await post(`/runs/${encodeURIComponent(owned.current)}/cancel`, {});
        ownedActive.current = false;
      }
      onClose();
    } catch (e) { setError(`取消失败：${(e as Error).message}`); closingRef.current = false; setClosing(false); }
  };
  const running = isActive(progress?.status);
  return <InteractiveModal title="单题重测与模型比较" onClose={() => void close()} closing={closing}>
    <p>第 {Number(question.index) + 1} 题 · {question.qa_id}</p><p>{String(question.display_question || question.question || '')}</p>
    <p className="muted small">复用原相册；评分沿用当前所选批次。关闭弹窗会取消本次未完成的重测，保留日志与已生成结果。</p>
    <ModelSelect options={models} value={model} onChange={setModel} onReadyChange={setModelReady} disabled={starting || running || closing}/>
    <label><input type="checkbox" aria-label="重测推理模式" checked={reasoning} disabled={starting || running || closing}
      onChange={e => setReasoning(e.target.checked)}/> 开启推理模式（默认开启）</label>
    {error && <p role="alert" className="error">{error}</p>}
    <button className="btn" disabled={busy || starting || running || closing || !modelReady} onClick={start}>{closing ? '正在取消并关闭…' : starting ? '正在启动…' : running ? '重测进行中…' : busy ? '等待当前任务结束' : '开始重测'}</button>
    {selected && <div ref={executionView} tabIndex={-1} className="retest-execution" aria-label="重测回答与轨迹"><p className="muted small">{selected} · {progress ? statusLabel(progress.status) : '正在加载'}</p>
      {loadingResult && <p role="status">正在加载回答与轨迹…</p>}
      {resultError && <p role="alert" className="error">{resultError}</p>}
      <ExecutionView key={selected} item={result?.runtime_turns?.length ? result : progress?.item || result} albumId={run.album_id || ''} modelLabel={progress?.model ? modelName(progress.model) : '模型准备中'} running={running} phases={progress?.phases}/>
    </div>}
    <h4>比较记录</h4>
    <div className="interactive-table"><table><thead><tr><th>记录</th><th>模型</th><th>得分</th><th>交付召回</th><th>耗时</th><th>状态</th><th></th></tr></thead><tbody>
      <tr><td>原结果</td><td>{modelName(run)}</td><td>{Object.values(question.profile_scores || {}).map(s => typeof s.score === 'number' ? String(s.score) : '未评分').join(' / ') || String(question.judge?.score ?? '未评分')}</td><td>{fmtPct(question.retrieval_recall)}</td><td>{fmtMs(question.wall_clock_ms)}</td><td>{String(question.execution_status || '')}</td><td></td></tr>
      {history.map(row => <tr key={row.run_id}><td><time dateTime={row.created_at} title={row.created_at}>{fmtTimestamp(row.created_at)}</time></td><td>{modelName(row)}</td><td>{scoringLabel(row)}</td><td>{fmtPct(row.retest_summary?.delivery_recall)}</td><td>{fmtMs(row.retest_summary?.wall_clock_ms)}</td><td>{statusLabel(row.status)}</td><td><button className="btn ghost compact" disabled={starting || running || closing || loadingResult} onClick={() => showRecord(row.run_id!)}>查看回答与轨迹</button></td></tr>)}
    </tbody></table></div>
  </InteractiveModal>;
}

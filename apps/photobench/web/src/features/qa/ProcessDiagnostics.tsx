import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client.ts';
import { PROFILE_LABELS } from '../../lib/scoring.ts';

type ScoringContext = { batches: { profile_id: string; batch_id: string }[] };
function Context({value}: {value?: ScoringContext}) {
  return <p className="muted">诊断评分来源：{value?.batches.length
    ? value.batches.map(b => `${PROFILE_LABELS[b.profile_id] || b.profile_id} · ${b.batch_id}`).join('；')
    : '未采集评分，仅分析答题记录'}。过程线索不代表执行失败。</p>;
}

type Finding = {
  category: string; label: string; status: string; message: string; sources: string[];
  [key: string]: unknown;
};
type Chain = {
  turn_index: number; purpose: string; target: string[]; hits: string[];
  missing: string[] | null; candidates: string[]; delivered: string[];
  candidate_status: string; delivery_status: string;
};
export type ProcessDiagnostic = {
  rule_version: string; qa_id: string; answer_fingerprint: string;
  original_answered_with_gap: boolean; findings: Finding[]; chain: Chain[];
  scoring_context?: ScoringContext;
};
const STATUS: Record<string, string> = {
  confirmed: '日志可确认', needs_review: '待复核', record_missing: '记录不足',
};

export function ProcessDiagnostics({ value }: { value: ProcessDiagnostic | undefined }) {
  if (!value) return null;
  return <details className="process-diagnostics">
    <summary>答题过程诊断</summary>
    <Context value={value.scoring_context}/>
    <p className="muted">
      原始执行标记：{value.original_answered_with_gap ? 'answered_with_gap' : '未标记 answered_with_gap'}。
      以下为保存日志的观测，语义结论需复核，不改正式评分。
    </p>
    <table className="data-table">
      <thead><tr><th>对话轮／集合用途</th><th>目标／候选命中</th><th>正式交付</th></tr></thead>
      <tbody>{value.chain.map((chain, index) => <tr key={index}>
        <td>第{chain.turn_index + 1}轮 · {chain.purpose === 'answer_target' ? '列表评分答案目标' : '公共检索证据'}</td>
        <td>{chain.target.length} / {chain.candidate_status === 'confirmed' ? chain.hits.length : '记录不足'}</td>
        <td>{chain.delivery_status === 'confirmed' ? chain.delivered.length : '记录不足'}</td>
      </tr>)}</tbody>
    </table>
    <details><summary>查看候选、命中与交付集合</summary>
      <pre className="json-preview">{JSON.stringify(value.chain, null, 2)}</pre>
    </details>
    {value.findings.length ? value.findings.map((finding, index) => <details key={index}>
      <summary>{STATUS[finding.status]} · {finding.label}</summary>
      <p>{finding.message}</p>
      <p>依据：{finding.sources.join('；')}</p>
      <pre className="json-preview">{JSON.stringify(finding, null, 2)}</pre>
      <a href={`#process-log-${value.qa_id}`}>跳到本题执行日志</a>
    </details>) : <p>本规则未确认缺口；答案质量评分另行展示。</p>}
    <small className="muted">规则 {value.rule_version} · 答卷指纹 {value.answer_fingerprint}</small>
  </details>;
}

type Report = {
  scoring_context?: ScoringContext;
  summary: {
    analyzed_qa: number; total_qa: number; original_answered_with_gap: number;
    categories: Record<string, number>; labels: Record<string, string>; statuses: Record<string, number>;
    tool_outcomes?: {status:string;reason:string;calls:number;qa_count:number}[];
  };
};
export function ProcessDiagnosticsOverview({ runId, scoringProfile = '', scoringBatch = '' }: { runId: string; scoringProfile?:string; scoringBatch?:string }) {
  const id = JSON.stringify([runId,scoringProfile,scoringBatch]);
  const latestId = useRef(id);
  useEffect(() => { latestId.current = id; }, [id]);
  const query = scoringProfile ? '?' + new URLSearchParams({scoring_profile:scoringProfile,scoring_batch:scoringBatch}) : '';
  const url = `/runs/${encodeURIComponent(runId)}/process-diagnostics${query}`;
  const [state, setState] = useState<{ id: string; report?: Report; error?: string; loading?: boolean }>({ id });
  const current = state.id === id ? state : undefined;
  const load = () => {
    if (current?.report || current?.loading) return;
    setState({ id, loading: true });
    api<Report>(url)
      .then(report => {if (latestId.current === id) setState({ id, report });})
      .catch(error => {if (latestId.current === id) setState({ id, error: String(error) });});
  };
  const summary = current?.report?.summary;
  return <details className="phase-card process-diagnostics-overview" onToggle={event => { if (event.currentTarget.open) load(); }}>
    <summary>答题过程诊断汇总</summary>
    {current?.loading ? <p>正在分析已保存答卷…</p> : null}
    {current?.error ? <p role="alert">{current.error}<button onClick={load}>重试</button></p> : null}
    {summary ? <>
      <Context value={current?.report?.scoring_context}/>
      <p>分析覆盖 {summary.analyzed_qa}/{summary.total_qa} · 原始 answered_with_gap {summary.original_answered_with_gap}题 · 记录不足 {summary.statuses.record_missing || 0}题。</p>
      <p className="muted">按唯一QA计数，同题可出现多类，各类不能相加为失败题总数；诊断读取不调用Agent或Judge。</p>
      <table className="data-table">
        <thead><tr><th>日志观测类别</th><th>涉及QA</th></tr></thead>
        <tbody>{Object.entries(summary.categories).map(([key, count]) =>
          <tr key={key}><td>{summary.labels[key]}</td><td>{count}</td></tr>
        )}</tbody>
      </table>
      {!!summary.tool_outcomes?.length && <details><summary>工具限制与异常原始原因</summary>
        <p className="muted">denied包含正常无文字返回；以下按原因计数，不能全部视为系统故障。</p>
        <table className="data-table"><thead><tr><th>状态／原因</th><th>调用数</th><th>涉及QA</th></tr></thead>
          <tbody>{summary.tool_outcomes.map((e,i) => <tr key={i}><td>{e.status} · {e.reason}</td><td>{e.calls}</td><td>{e.qa_count}</td></tr>)}</tbody>
        </table></details>}
      <a href={`/api/v1${url}`} target="_blank" rel="noreferrer">查看完整诊断JSON</a>
    </> : null}
  </details>;
}

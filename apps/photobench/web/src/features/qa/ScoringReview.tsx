import { useState } from 'react';
import { post } from '../../api/client.ts';
import { PROFILE_LABELS } from '../../lib/scoring.ts';

type Row = Record<string, unknown>;
const obj = (v:unknown):Row => v && typeof v === 'object' ? v as Row : {};
const LABELS:Record<string,string> = {needs_review:'待复核',confirmed_misgrade:'确认误扣',reason_error_score_unchanged:'理由错误，分数可维持',dataset_review:'题库待核',confirmed_score:'确认原判'};

export function ScoringReview({runId, results, reviews}: {runId:string;results:unknown;reviews:unknown}) {
  const rows = (Array.isArray(results) ? results.map(obj) : []).filter(r => r.answer_fingerprint);
  const [key,setKey] = useState('');
  const [verdict,setVerdict] = useState('needs_review');
  const [reviewer,setReviewer] = useState('');
  const [note,setNote] = useState('');
  const [state,setState] = useState<{busy?:boolean;error?:string;saved?:Row[]}>({});
  if (!rows.length || !runId) return null;
  const selected = rows.find(r => r.key === key) || rows[rows.length-1]!;
  const saved = state.saved || (Array.isArray(reviews) ? reviews.map(obj) : []);
  const save = async () => {
    if (state.busy) return;
    setState({busy:true,saved});
    try {
      const value = await post<{scoring_reviews:Row[]}>(`/runs/${encodeURIComponent(runId)}/reviews`,{scoring_reviews:[{
        qa_id:selected.qa_id,profile_id:selected.profile_id,batch_id:selected.batch_id,turn_index:selected.turn_index,
        answer_fingerprint:selected.answer_fingerprint,verdict,reviewer,note,
      }]});
      setState({saved:value.scoring_reviews.filter(r => r.qa_id === selected.qa_id)});
    } catch (e) {setState({saved,error:String(e)});}
  };
  return <details className="phase-card scoring-review"><summary>评分人工复核（{saved.length}条）</summary>
    <p className="muted">记录复核依据，保留原分；正式修正通过新批次评分。</p>
    <div className="scoring-review-fields"><label>评分记录<select aria-label="复核评分记录" value={String(selected.key)} onChange={e=>setKey(e.target.value)}>{rows.map(r=><option key={String(r.key)} value={String(r.key)}>{PROFILE_LABELS[String(r.profile_id)]} · {String(r.batch_id)} · 第{Number(r.turn_index)+1}轮 · {String(r.score ?? '未评分')}</option>)}</select></label>
    <label>结论<select aria-label="复核结论" value={verdict} onChange={e=>setVerdict(e.target.value)}>{Object.entries(LABELS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <label>审核人<input aria-label="审核人" value={reviewer} maxLength={200} onChange={e=>setReviewer(e.target.value)}/></label>
    <label>依据<textarea aria-label="复核依据" value={note} maxLength={10000} onChange={e=>setNote(e.target.value)}/></label>
    </div><button className="btn" disabled={state.busy || !note.trim() || !reviewer.trim()} onClick={save}>{state.busy?'保存中…':'保存复核记录'}</button>
    <details><summary>新旧批次对照</summary>
      <div className="table-scroll"><table className="data-table"><thead><tr><th>标准／批次／轮次</th><th>分数及理由</th><th>冻结配置</th></tr></thead><tbody>{rows.map(r => {
        const definition = obj(r.batch_definition), judge = obj(definition.judge), checks = obj(r.checks);
        return <tr key={String(r.key)}><td>{PROFILE_LABELS[String(r.profile_id)]}<br/><code>{String(r.batch_id)}</code><br/>第{Number(r.turn_index)+1}轮</td>
          <td>{String(r.score ?? '未评分')} / {String(r.max_score ?? '—')} · {String(r.status ?? '')}<p>{String(r.reason ?? '未记录理由')}</p>{Object.keys(checks).length > 0 && <dl><dt>核心匹配</dt><dd>{checks.core_match ? '是' : '否'}</dd>{[['missing','必答遗漏'],['contradictions','事实矛盾'],['unverified_additions','未验证补充']].map(([key,label])=><div key={key}><dt>{label}</dt><dd>{Array.isArray(checks[key!]) ? (checks[key!] as string[]).join('；') || '无' : '未记录'}</dd></div>)}</dl>}</td>
          <td>{String(definition.rule_version || '规则未记录')}<br/>Judge：{String(judge.model || '未记录')}<br/>提示词：<code>{String(definition.prompts_sha256 || '未记录')}</code><br/>QA：<code>{String(definition.qa_definition_sha256 || '未记录')}</code><details><summary>GT与输入配置</summary><pre className="json-preview">{JSON.stringify(definition.config || {},null,2)}</pre></details></td></tr>;
      })}</tbody></table></div>
    </details>
    {state.error && <p role="alert">{state.error}</p>}
    {saved.map((r,i)=><details key={String(r.review_id || i)}><summary>{LABELS[String(r.verdict)] || String(r.verdict)} · {String(r.reviewer)} · {String(r.updated_at)}</summary><p>{String(r.note)}</p><p>批次 {String(r.batch_id)} · 第{Number(r.turn_index)+1}轮 · 答卷 {String(r.answer_fingerprint)}</p></details>)}
  </details>;
}

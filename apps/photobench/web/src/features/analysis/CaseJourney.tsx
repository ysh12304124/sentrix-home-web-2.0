import { CaseDiagnosis } from "./CaseDiagnosis.tsx";
import { useEffect, useState } from "react";
import { api } from "../../api/client.ts";
import type { AnalysisClaim, DiagnosisReport } from "../../api/analysis.ts";
import type { RunItemDetail } from "../../api/types.ts";
import { runtimeTurns } from "../../lib/agentTrace.ts";
import { actionLabel } from "../../lib/format.ts";
import { atPath, cleanNotes, evidenceName, journeyNodes, object, text, type JourneyNode } from "./journey.ts";

type Diagnosis = DiagnosisReport["cases"][number];
type Evidence = { item: RunItemDetail; evidence_locations?: Record<string,string> };
const pretty = (v: unknown) => typeof v === "string" ? v : JSON.stringify(v, null, 2);
function Claim({ claim }: {claim:AnalysisClaim}) {
  return <div className={`journey-finding ${claim.kind}`}><span>{claim.kind === "hypothesis" ? "可能原因 · 待核实" : claim.kind === "recommendation" ? "核查建议" : "AI 观察"}</span><p>{claim.text}</p>{cleanNotes(claim.limitations).map((note,i)=><small key={i}>{note}</small>)}</div>;
}
export function NodeRecord({ node }: {node:JourneyNode}) {
  const e=node.entry, raw=e.call || e.tool || {};
  if(e.kind === "event" && e.event?._recorded_execution) return <><p>{text(e.event.error)}</p><p className="analysis-hint">来自本题保存的错误；内部调用轨迹未记录时无法进一步拆解。</p></>;
  if(e.kind === "answer") return <p className="journey-answer">{e.answer?.trim() || "未保存回答正文"}</p>;
  const output = e.tool ? raw.result ?? raw.observation : raw.response;
  return <><p className="analysis-hint">{node.status}{raw.step_id ? ` · 步骤 ${raw.step_id}` : ""}{raw.attempt != null ? ` · 尝试 ${raw.attempt}` : ""}{e.unbound ? " · 父调用未确定" : ""}</p>
    <details><summary>{e.tool ? "工具输入" : "模型输入"}</summary><pre>{pretty(e.tool ? raw.arguments ?? "未记录" : raw.messages ?? "未记录")}</pre></details>
    <details><summary>{e.tool ? "工具输出" : "模型输出"}</summary><pre>{pretty(output ?? "未记录")}</pre></details>
    {!!raw.error && <details><summary>错误原文</summary><pre>{pretty(raw.error)}</pre></details>}
  </>;
}
function SourceRecord({item,refId,path}:{item:RunItemDetail|undefined;refId:string;path:string|undefined}) {
  if (!item) return <p>来源尚未读取</p>;
  if (refId.endsWith(":execution")) return <><h5>答题执行状态</h5><p>{text(item.execution_status) || "未记录"}</p><h5>错误原文</h5><p>{text(item.error) || "未记录错误"}</p></>;
  if (refId.endsWith(":task_contract") || refId.endsWith(":outcome")) {
    const turns=runtimeTurns(item);
    return <>{turns.map((turn,i)=><article className="journey-source-turn" key={i}><h5>第{i+1}轮</h5>{refId.endsWith(":task_contract") ? <><p>预期行为：{actionLabel(turn.expected_action)}</p><p>参考答案：{text(turn.reference_answer) || text(item.reference_answer) || "未记录"}</p></> : <><p>结束状态：{text(turn.termination_reason) || "未记录"}</p><p>回答：{text(turn.answer) || "未记录"}</p></>}</article>)}</>;
  }
  if (refId.endsWith(":case")) return <><h5>题目</h5><p>{text(item.question) || "未记录"}</p><h5>参考答案</h5><p>{text(item.reference_answer) || "未记录"}</p><h5>实际回答</h5><p>{text(item.answer) || "未记录"}</p></>;
  return <details open><summary>引用原文</summary><pre>{pretty(path !== undefined ? atPath(item,path) ?? "此路径未记录" : "此引用无独立来源位置")}</pre></details>;
}
export function CaseJourney({diagnosis,jobId,navigate}:{diagnosis:Diagnosis;jobId:string;navigate:(ref:string)=>void}) {
  const [open,setOpen]=useState(false),[data,setData]=useState<Evidence|null>(null),[error,setError]=useState("");
  const [selection,setSelection]=useState<string|null>(null),[source,setSource]=useState<string|null>(null);
  useEffect(()=>{
    if(!open)return;
    const abort=new AbortController();
    void api<Evidence>(`/analyses/${jobId}/evidence/${diagnosis.index}`,{signal:abort.signal}).then(setData).catch(e=>{if(!abort.signal.aborted)setError(String(e));});
    return ()=>abort.abort();
  },[open,jobId,diagnosis.index]);
  const claims=diagnosis.result.claims || [], notes=cleanNotes(diagnosis.result.missing_evidence);
  const nodes=data ? journeyNodes(data.item,data.evidence_locations || {}) : [];
  const selected=nodes.find(n=>n.id===selection) || nodes.find(n=>n.entry.event?._recorded_execution) || nodes.find(n=>n.entry.kind === "answer") || nodes[0];
  const associated=selected ? claims.filter(c=>c.evidence_refs.some(r=>selected.refs.includes(r))) : [];
  const item=data?.item, rawScore=object(item?.judge).score;
  function locate(ref:string) {
    const node=nodes.find(n=>n.refs.includes(ref));
    if(node){setSelection(node.id);setSource(null);}else{setSource(ref);}
  }
  function references(rows:AnalysisClaim[]) {
    return [...new Set(rows.flatMap(c=>c.evidence_refs))].map(ref=><button className="journey-ref" key={ref} onClick={()=>locate(ref)}>{evidenceName(ref,nodes)}</button>);
  }
  const failure = diagnosis.error === "gt_images_unavailable_or_changed"
    ? "GT 图片读取或校验失败，未请求分析模型"
    : diagnosis.status === "invalid" ? "AI 输出格式或引用未通过校验"
    : diagnosis.error ? "分析请求失败，展开查看原因" : "尚未完成分析";
  const image=diagnosis.gt_images?.find(img=>img.id===source);
  const path=source ? data?.evidence_locations?.[source] : undefined;
  return <details className="case-journey" open={open} onToggle={e=>{if(e.target===e.currentTarget)setOpen(e.currentTarget.open);}}>
    <summary><span className="journey-case-id">{diagnosis.qa_id || `第${diagnosis.index+1}题`}</span><span className="journey-case-headline">{diagnosis.result.diagnosis?.summary || (diagnosis.valid ? "查看执行轨迹与分析" : failure)}</span><span className="journey-status">{diagnosis.valid ? "AI 已分析" : diagnosis.error || diagnosis.status === "invalid" ? "分析失败" : "未完成"}</span></summary>
    {open && <div className="journey-body">
      {error && <p role="alert">轨迹读取失败：{error} <button className="analysis-link" onClick={()=>navigate(`i${diagnosis.index}:case`)}>打开原题记录</button></p>}
      {!data && !error && <p role="status">正在读取本题已保存的轨迹…</p>}
      {item && <header className="journey-question"><h4>{text(item.question) || text(item.message) || diagnosis.qa_id}</h4><div className="journey-meta"><span>原质量分 {typeof rawScore === "number" ? `${rawScore}/2` : "未评分"}</span>{text(item.expected_action) && <span>预期：{actionLabel(item.expected_action)}</span>}<span>{nodes.length} 个记录节点</span></div></header>}
      {diagnosis.gt_images && <p className="analysis-hint">{diagnosis.gt_images.length ? `GT 图片：${diagnosis.gt_images.filter(img=>img.status==="attached").length} 张已冻结` : "本题无标注 GT 图片，按文字与轨迹分析"}{diagnosis.error==="gt_images_unavailable_or_changed" ? " · 图片不可用或校验失败，本题未请求分析模型" : ""}。图片仅供分析核对，未证明答题模型见过。{diagnosis.gt_missing_evidence?.length ? `另有 ${diagnosis.gt_missing_evidence.length} 项证据缺口，见“GT 图片来源与缺口”。` : ""}</p>}
      {diagnosis.result.diagnosis && <CaseDiagnosis diagnosis={diagnosis.result.diagnosis} locate={locate}/>}
      <div className="journey-workspace">
        <section className="journey-route"><h5>执行路径 <small>点击节点查看</small></h5><p className="analysis-hint">按已保存调用及父步骤关联展示；原始事件见完整记录。</p>
          <ol>{nodes.map((n,i)=><li key={n.id}><button aria-pressed={!source && selected?.id===n.id} className={n.failed ? "has-error" : ""} onClick={()=>{setSelection(n.id);setSource(null);}}><span className="journey-number">{i+1}</span><span><strong>{n.title}</strong><small>第{n.turn+1}轮 · {n.status}</small></span>{n.refs.some(r=>claims.some(c=>c.evidence_refs.includes(r))) && <em>AI 注解</em>}</button></li>)}</ol>
          {data && !nodes.length && <p>此存档没有可展示的调用轨迹。</p>}
        </section>
        <section className="journey-detail" aria-label="节点详情" aria-live="polite">
          {source ? <><h5>{evidenceName(source,nodes)}</h5>{image ? <><p>第{image.turn+1}轮 · {image.media_id}</p><p>来源：{image.source} · {image.status === "attached" ? "创建分析时已冻结" : "图片不可用"}</p><p className="analysis-hint">这是标注参考图片，不是 Agent 调用节点；未核实与原测评图片字节一致。</p><details><summary>图片来源记录</summary><pre>{pretty(image)}</pre></details></> : <><p className="analysis-hint">此引用未唯一关联到调用节点，保留原始来源供核对。</p><SourceRecord item={item} refId={source} path={path}/></>}</> : selected && <><h5>{selected.title} <small>第{selected.turn+1}轮</small></h5><NodeRecord node={selected}/>{associated.length ? <div className="journey-node-analysis"><h5>AI 对此节点的分析</h5>{associated.map((c,i)=><Claim key={i} claim={c}/>)}</div> : <p className="analysis-hint">AI 未单独引用此节点；上方结论与下方完整分析保留。</p>}</>}
        </section>
      </div>
      <details className="journey-answer-pair"><summary>对照参考答案与最终回答</summary><div><article><h5>参考答案（GT）</h5><p>{text(item?.reference_answer) || "题级参考答案未记录，逐轮内容见完整记录"}</p></article><article><h5>最终回答</h5><p>{text(item?.answer) || "题级回答未记录，逐轮内容见执行节点"}</p></article></div></details>
      <details className="journey-all-findings"><summary>关键分析依据（{claims.length}条）</summary><p className="analysis-hint">原分析逐条保留，点击来源可在本题内定位。</p>{claims.map((c,i)=><div key={i}><Claim claim={c}/><div className="journey-refs">{references([c])}</div></div>)}</details>
      {!!notes.length && <details><summary>待核实与缺失信息（{notes.length}项）</summary><ul>{notes.map((n,i)=><li key={i}>{n}</li>)}</ul></details>}
      {!diagnosis.valid && <details><summary>分析错误原文</summary><pre>{diagnosis.error || diagnosis.raw || "未记录"}</pre></details>}
      {diagnosis.gt_images && <details><summary>GT 图片来源与缺口</summary><pre>{pretty({images:diagnosis.gt_images,missing_evidence:diagnosis.gt_missing_evidence || []})}</pre></details>}
      <details><summary>完整执行记录</summary><p className="analysis-hint">包括原始事件、评分与多轮内容；此处不修改历史数据。</p><pre>{pretty(item)}</pre></details>
    </div>}
  </details>;
}

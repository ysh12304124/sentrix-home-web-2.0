import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client.ts";
import { NodeRecord } from "./CaseJourney.tsx";
import { atPath, journeyNodes, text, type JourneyNode } from "./journey.ts";
import { ComparisonFindings } from "./ComparisonFindings.tsx";
import { alignJourneys, comparisonRef, comparisonRefName, nodeLocations, quality, readableComparison, sideNames, sides,
  type ComparisonCase, type ComparisonEvidence, type ComparisonEvidenceSide, type ComparisonSource, type Side } from "./comparison.ts";
export function ComparisonIdentity({source,side,score}:{source?:ComparisonSource;side:Side;score?:string}){
  const id=source?.run_id || "";
  const date=/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-/.exec(id);
  const caption=date ? `${date[1]}-${date[2]}-${date[3]} ${date[4]}:${date[5]}` : id || "任务标识未记录";
  return <header className={`comparison-identity ${side}`}><div><strong>{sideNames[side]}</strong>{score && <span className="comparison-score">{score}</span>}</div><p>{source?.model || "模型未记录"}</p><small title={id}>{caption}</small>{source?.build_revision && <small>代码版本 {source.build_revision.slice(0,8)}</small>}</header>;
}
type Selection=Partial<Record<Side,{node?:JourneyNode;ref?:string}>>;
export function ComparisonJourney({item,jobId,focus}:{item:ComparisonCase;jobId:string;focus?:{index:number;refs:string[]}}){
  const [open,setOpen]=useState(false),[data,setData]=useState<ComparisonEvidence|null>(null),[error,setError]=useState("");
  const flow=useRef<HTMLElement>(null);
  const [dismissedFocus,setDismissedFocus]=useState<typeof focus>();
  const isFocused=focus?.index===item.index && focus!==dismissedFocus;
  const expanded=open || isFocused;
  const [choice,setChoice]=useState<{selection:Selection;marked:string[];focus?:typeof focus}>({selection:{},marked:[]});
  useEffect(()=>{
    if(!expanded || item.pairing!=="paired")return;
    const abort=new AbortController();
    void api<ComparisonEvidence>(`/analyses/${jobId}/evidence/${item.index}`,{signal:abort.signal}).then(setData).catch(e=>{if(!abort.signal.aborted)setError(String(e));});
    return ()=>abort.abort();
  },[expanded,jobId,item.index,item.pairing]);
  const nodes:Record<Side,JourneyNode[]>={left:data ? journeyNodes(data.sides.left.item,nodeLocations(data.sides.left)) : [],right:data ? journeyNodes(data.sides.right.item,nodeLocations(data.sides.right)) : []};
  const rows=alignJourneys(nodes.left,nodes.right);
  function selectReferences(refs:string[]):Selection{
    const next:Selection={};
    for(const ref of refs){const parsed=comparisonRef(ref);if(!parsed || next[parsed.side])continue;
      const node=nodes[parsed.side].find(n=>n.refs.includes(ref));
      next[parsed.side]=node ? {node} : {ref};
    }
    return next;
  }
  const fromFocus=isFocused && focus!==choice.focus;
  const selection=fromFocus ? selectReferences(focus!.refs) : choice.selection;
  const marked=fromFocus ? focus!.refs : choice.marked;
  useEffect(()=>{if(data && focus?.index===item.index)flow.current?.scrollIntoView({block:"center"});},[data,focus,item.index]);
  function locate(refs:string[]){
    setChoice({selection:selectReferences(refs),marked:refs,focus});
    flow.current?.scrollIntoView({behavior:"smooth",block:"center"});
  }
  function select(row:typeof rows[number]){
    setChoice({selection:Object.fromEntries(sides.filter(side=>row[side]).map(side=>[side,{node:row[side]}])),marked:[],focus});
  }
  function source(record:ComparisonEvidenceSide,ref:string,side:Side){
    const path=record.evidence_locations[ref];
    const image=record.gt_images.find(i=>i.id===ref);
    const value=image || (path==="qa" ? record.qa : path==="item" ? record.item : path?.startsWith("item.") ? atPath(record.item,path.slice(5)) : undefined);
    return <><h5>{comparisonRefName(ref,nodes[side])}</h5>{image && <p className="analysis-hint">标注参考图片，第{image.turn+1}轮 · {image.media_id} · {image.status==="attached" ? "已冻结" : "不可用"}；不代表答题模型见过。</p>}<details open><summary>引用的原始记录</summary><pre>{typeof value==="string" ? value : JSON.stringify(value ?? "此来源未保存独立记录",null,2)}</pre></details></>;
  }
  const result=item.valid ? item.result : undefined;
  const findings=result ? [...result.divergences,...result.similarities,...result.evaluation] : [];
  const failure=item.error==="invalid_references" ? "AI结论的引用校验未通过，执行轨迹仍可查看" : item.error;
  const headline=result?.divergences[0]?.text || result?.evaluation[0]?.text;
  return <details className="case-journey comparison-journey" open={expanded} onToggle={e=>{if(e.target!==e.currentTarget)return;setOpen(e.currentTarget.open);if(!e.currentTarget.open)setDismissedFocus(focus);}}>
    <summary><span className="journey-case-id">{item.qa_id || `第${item.index+1}题`}</span><span className="journey-case-headline">{headline ? readableComparison(headline) : failure || "展开查看两次执行轨迹"}</span><span className="journey-status">{item.valid ? "AI 已对比" : item.pairing!=="paired" ? "未配对" : "分析失败"}</span></summary>
    {expanded && <div className="journey-body">
      {error && <p role="alert">对比轨迹读取失败：{error}</p>}
      {!data && !error && item.pairing==="paired" && <p role="status">正在读取两次测评的冻结轨迹…</p>}
      {data && <>
        <header className="journey-question"><h4>{text(data.sides.left.item.question) || text(data.sides.left.qa?.question) || item.qa_id}</h4>{data.sides.right.item.question!==data.sides.left.item.question && <p>对照题目：{text(data.sides.right.item.question) || "未记录"}</p>}</header>
        <div className="comparison-sides">{sides.map(side=><ComparisonIdentity key={side} side={side} source={data.sides[side].source} score={quality(data.sides[side].item)}/>)}</div>
        <section className="comparison-flow" ref={flow}><h5>执行轨迹对比 <span>{nodes.left.length} / {nodes.right.length} 个记录节点</span></h5><p className="analysis-hint">按轮次与调用类型对齐；同类步骤不代表输入输出相同。点击节点并排查看记录，AI引用会高亮对应节点。</p>
          <div className="comparison-flow-scroll">{rows.map((row,i)=><div className="comparison-flow-row" key={i}>{sides.map(side=>{const node=row[side];return <div key={side} className="comparison-flow-cell">{node ? <button className={`comparison-node ${node.failed ? "has-error" : ""} ${node.refs.some(r=>marked.includes(r)) ? "is-cited" : ""}`} aria-pressed={selection[side]?.node?.id===node.id} onClick={()=>select(row)}><span className="journey-number">{nodes[side].indexOf(node)+1}</span><span><strong>{node.title}</strong><small>{sideNames[side]} · 第{node.turn+1}轮 · {node.status}</small></span>{node.refs.some(r=>Object.values(result || {}).some(claims=>Array.isArray(claims) && claims.some(c=>typeof c==="object" && c.evidence_refs?.includes(r)))) && <em>AI 引用</em>}</button> : <div className="comparison-node-gap">未记录对应步骤</div>}</div>;})}</div>)}</div>
          {!rows.length && <p>两次测评均未保存可展示的调用节点。</p>}
        </section>
        <div className="comparison-records">{sides.map(side=><section className="journey-detail" key={side} aria-label={`${sideNames[side]}节点详情`}><h5>{sideNames[side]} · 节点记录</h5>{selection[side]?.ref ? source(data.sides[side],selection[side]!.ref!,side) : selection[side]?.node ? <><h5>{selection[side]!.node!.title}</h5><NodeRecord node={selection[side]!.node!}/>{findings.filter(claim=>claim.evidence_refs.some(ref=>selection[side]!.node!.refs.includes(ref))).slice(0,2).map((claim,i)=><div className="journey-finding" key={i}><span>AI 对此节点的观察</span><p>{readableComparison(claim.text)}</p></div>)}</> : <p className="analysis-hint">点击上方节点或下方AI结论的来源，查看对应记录。</p>}</section>)}</div>
        <details className="journey-answer-pair"><summary>参考要求与最终回答</summary><div>{sides.map(side=><article key={side}><h5>{sideNames[side]}</h5><small>参考答案</small><p>{text(data.sides[side].item.reference_answer) || text(data.sides[side].qa?.answer) || "未记录"}</p><small>最终回答</small><p>{text(data.sides[side].item.answer) || "未记录题级回答，逐轮回答见执行节点"}</p></article>)}</div></details>
      </>}
      {result && <ComparisonFindings value={result} nodes={[...nodes.left,...nodes.right]} onLocate={locate}/>}
      {!item.valid && <p role="alert">{failure || "此题未取得有效对比结论"}</p>}
      <details><summary>完整对比记录</summary><pre>{JSON.stringify(item,null,2)}</pre></details>
    </div>}
  </details>;
}

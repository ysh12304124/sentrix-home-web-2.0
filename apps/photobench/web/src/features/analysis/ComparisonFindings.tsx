import { useState } from "react";
import { cleanNotes, type JourneyNode } from "./journey.ts";
import { comparisonRefName, readableComparison, type Findings } from "./comparison.ts";
const sections={divergences:"分叉点",similarities:"一致点",evaluation:"总结评价"} as const;
export function ComparisonFindings({value,onLocate,nodes=[],overview=false}:{
  value:Findings;onLocate:(refs:string[])=>void;nodes?:JourneyNode[];overview?:boolean;
}){
  const [section,setSection]=useState<keyof typeof sections>(overview ? "evaluation" : "divergences");
  const rows=value[section];
  function finding(claim:typeof rows[number],i:number){
    return <article className={`comparison-insight ${claim.kind}`} key={i}>
      <small>{claim.kind==="hypothesis" ? "可能原因 · 待核实" : claim.kind==="recommendation" ? "核查建议" : "AI 观察"}</small>
      <p>{readableComparison(claim.text)}</p>
      {cleanNotes(claim.limitations).map((note,n)=><small key={n}>{readableComparison(note)}</small>)}
      <div className="journey-refs">{claim.evidence_refs.map(ref=><button key={ref} className="journey-ref" onClick={()=>onLocate([ref])}>{comparisonRefName(ref,nodes)}</button>)}</div>
      {claim.evidence_refs.length>1 && <button className="analysis-link comparison-locate" onClick={()=>onLocate(claim.evidence_refs)}>定位这条结论的执行记录 →</button>}
    </article>;
  }
  return <section className="comparison-insights">
    <div role="tablist" aria-label={overview ? "整体对比结论" : "本题对比结论"}>{(Object.keys(sections) as (keyof typeof sections)[]).map(key=><button key={key} role="tab" aria-selected={section===key} onClick={()=>setSection(key)}>{sections[key]} <span>{value[key].length}</span></button>)}</div>
    <div role="tabpanel" aria-label={sections[section]}>{rows.slice(0,2).map(finding)}{rows.length>2 && <details><summary>展开其余{sections[section]}（{rows.length-2}条）</summary>{rows.slice(2).map(finding)}</details>}{!rows.length && <p className="analysis-hint">未形成有证据支持的{sections[section]}</p>}</div>
    {!!cleanNotes(value.missing_evidence).length && <details><summary>分析限制与缺失信息</summary><ul>{cleanNotes(value.missing_evidence).map((note,i)=><li key={i}>{readableComparison(note)}</li>)}</ul></details>}
  </section>;
}

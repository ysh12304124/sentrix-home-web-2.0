import type { AnalysisClaim } from "../../api/analysis.ts";
import type { RunItemDetail } from "../../api/types.ts";
import { evidenceName, object, type JourneyNode } from "./journey.ts";

export type Side = "left" | "right";
export const sides: Side[] = ["left", "right"];
export const sideNames: Record<Side,string> = {left:"本次测评",right:"对照测评"};
export type Findings = { similarities: AnalysisClaim[]; divergences: AnalysisClaim[]; evaluation: AnalysisClaim[]; missing_evidence: string[] };
export interface ComparisonCase {index:number;right_index?:number|null;qa_id:string;pairing:string;valid:boolean;error?:string;result?:Findings}
export interface ComparisonSource {run_id?:string;model?:string;started_at?:string;build_revision?:string}
export interface AIComparisonReport {
  requested:number;coverage:Record<string,number>;cases:ComparisonCase[];overview?:Findings;
  summary_status:string;summary_error?:string;model:string;model_role:string;prompt_version:string;
  sources:Record<Side,ComparisonSource>;scope:{comparison_filter:string;scope_count:number};note:string;
}
export interface ComparisonEvidenceSide {
  index:number;source:ComparisonSource;item:RunItemDetail;qa:Record<string,unknown>|null;
  gt_images:{id:string;media_id:string;turn:number;status:string}[];
  evidence_locations:Record<string,string>;
}
export interface ComparisonEvidence {job_id:string;index:number;sides:Record<Side,ComparisonEvidenceSide>}
export function readableComparison(value:string):string {
  return value.replace(/\bleft\b(?:轮|侧)?/gi,"本次测评").replace(/\bright\b(?:轮|侧)?/gi,"对照测评").replace(/左右两轮/g,"两次测评");
}
export function comparisonRef(ref:string):{side:Side;index:number;path:string}|null {
  const m=/^(left|right):i(\d+):(.+)$/.exec(ref);
  return m ? {side:m[1] as Side,index:Number(m[2]),path:m[3]} : null;
}
export function comparisonRefName(ref:string,nodes:JourneyNode[]=[]):string {
  const parsed=comparisonRef(ref);
  if(!parsed)return ref==="scope:statistics" ? "范围统计" : "查看对比来源";
  const path=parsed.path;
  const name=path==="qa" ? "题目与参考要求" : path==="item" ? "完整答题记录"
    : path==="item.answer" ? "最终回答" : path==="item.judge" ? "质量评分"
    : /^item.runtime_turns\[\d+\]$/.test(path) ? `第${Number(/\[(\d+)\]/.exec(path)![1])+1}轮完整记录`
    : path==="item.tool_trace" ? "全部工具记录" : path==="item.model_call_metrics" ? "全部模型调用"
    : evidenceName(ref,nodes);
  return `${sideNames[parsed.side]} · ${name}`;
}
export function nodeLocations(side:ComparisonEvidenceSide):Record<string,string> {
  return Object.fromEntries(Object.entries(side.evidence_locations)
    .filter(([,path])=>/(debug_calls|model_call_metrics|model_calls|tool_trace)\[\d+\]$/.test(path))
    .map(([ref,path])=>[ref,path.replace(/^item\./,"")]));
}
function nodeKind(node:JourneyNode):string {
  const raw=node.entry.call || node.entry.tool || {};
  return [node.turn,node.entry.kind,raw.call_type || "",raw.tool || raw.tool_name || ""].join(":");
}
/** Order-preserving alignment of call kinds. Matching does not assert equal input/output. */
export function alignJourneys(left:JourneyNode[],right:JourneyNode[]):{left?:JourneyNode;right?:JourneyNode}[] {
  const table=Array.from({length:left.length+1},()=>new Uint32Array(right.length+1));
  for(let a=left.length-1;a>=0;a--)for(let b=right.length-1;b>=0;b--)
    table[a][b]=nodeKind(left[a])===nodeKind(right[b]) ? table[a+1][b+1]+1 : Math.max(table[a+1][b],table[a][b+1]);
  const rows:{left?:JourneyNode;right?:JourneyNode}[]=[];
  let a=0,b=0;
  while(a<left.length || b<right.length){
    if(a<left.length && b<right.length && nodeKind(left[a])===nodeKind(right[b])) rows.push({left:left[a++],right:right[b++]});
    else if(a<left.length && (b===right.length || table[a+1][b]>=table[a][b+1]))rows.push({left:left[a++]});
    else rows.push({right:right[b++]});
  }
  return rows;
}
export function quality(item:RunItemDetail):string {
  const judge=object(item.judge), score=judge.score;
  if (judge.scorer) return typeof score === "number" ? `${score}/${judge.max_score} 分 · ${typeof judge.normalized_score === "number" ? (judge.normalized_score * 100).toFixed(1) + "%" : "未评分"}` : "未评分";
  return typeof score==="number" && [0,1,2].includes(score) ? `${score}/2 分` : "未评分";
}

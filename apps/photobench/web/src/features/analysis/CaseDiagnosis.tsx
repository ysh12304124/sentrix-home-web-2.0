import type { DiagnosisReport } from "../../api/analysis.ts";
import { cleanNotes } from "./journey.ts";

const types: Record<string, string> = {
  service_error: "服务请求失败", execution_failure: "答题执行失败", context_budget: "上下文或预算不足",
  format_error: "输出格式错误", tool_failure: "工具调用失败", retrieval_gap: "检索证据不足",
  incorrect_answer: "回答错误", unsupported_claim: "无依据推断", incomplete_answer: "回答不完整",
  gt_conflict: "GT 证据冲突", judge_failure: "评分失败", record_missing: "执行记录缺失", no_issue: "未发现明确问题", unknown: "类型待确认",
};
type Diagnosis = NonNullable<DiagnosisReport["cases"][number]["result"]["diagnosis"]>;
export function CaseDiagnosis({ diagnosis, locate }: {
  diagnosis: Diagnosis; locate: (ref: string) => void;
}) {
  return <section className="case-diagnosis" aria-label="诊断结论">
    <div className="diagnosis-types">{diagnosis.error_types.map(code => <span key={code}>{types[code] || code}</span>)}<small>AI 判断 · {({confirmed:"记录支持",likely:"原因推测",unknown:"原因未确认"})[diagnosis.certainty]}</small></div>
    <h5>{diagnosis.summary}</h5>
    <dl><dt>原因分析</dt><dd>{diagnosis.cause}</dd><dt>处理建议</dt><dd>{diagnosis.next_step}</dd></dl>
    {cleanNotes(diagnosis.limitations).map(note => <p className="analysis-hint" key={note}>{note}</p>)}
    <div className="journey-refs">{diagnosis.evidence_refs.map((ref, i) => <button className="journey-ref" onClick={() => locate(ref)} key={ref}>依据 {i + 1}</button>)}</div>
  </section>;
}

import { useEffect, useState } from "react";
import { api } from "../../api/client.ts";

type Question = {
  qa_id: string; question: string; reference_answer: string;
  old_answer: string; new_answer: string;
  old_keyframe_hit: boolean; new_keyframe_hit: boolean;
  old_correct_video_delivered: boolean; new_correct_video_delivered: boolean;
  old_candidate_snapshot: unknown; new_candidate_snapshot: unknown;
  old_returned_media: unknown; new_returned_media: unknown;
};
type Report = {
  status: string; reason?: string; baseline_run_id?: string;
  definition?: string; limitation?: string;
  metrics?: { name: string; old: number; new: number; total: number; gained: number; lost: number }[];
  questions?: Question[];
};
const rate = (n: number, total: number) => `${n}/${total}，${(100 * n / total).toFixed(1)}%`;
const yes = (value: boolean) => value ? "是" : "否";

export function VideoComparisonPanel({ runId }: { runId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setReport(null); setError(""); setQuery(""); setChangedOnly(false);
    void api<Report>(`/runs/${encodeURIComponent(runId)}/video-comparison`, {signal: controller.signal})
      .then(value => { if (!controller.signal.aborted) setReport(value); })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [runId]);
  if (error) return <p role="alert">视频历史对照读取失败：{error}</p>;
  if (report?.status === "stale") return <p role="alert">{report.reason}</p>;
  if (report?.status !== "ready") return null;
  const questions = (report.questions || []).filter(row => {
    const changed = row.old_keyframe_hit !== row.new_keyframe_hit ||
      row.old_correct_video_delivered !== row.new_correct_video_delivered;
    return (!changedOnly || changed) &&
      [row.qa_id, row.question, row.old_answer, row.new_answer, row.reference_answer]
        .join(" ").toLowerCase().includes(query.toLowerCase());
  });
  return <section className="video-comparison-panel" aria-label="视频链路历史对照">
    <h3 className="result-heading">视频链路历史对照（不使用 Judge 分数）</h3>
    <p className="muted">上次：{report.baseline_run_id} · 本次：{runId}</p>
    <p>{report.definition}</p>
    <div style={{overflowX: "auto"}}>
      <table aria-label="视频关键帧与交付对照" style={{width: "100%", borderCollapse: "collapse"}}>
        <thead><tr>{["指标", "上次", "本次", "变化"].map(name => <th key={name} style={{padding: 10, textAlign: "left"}}>{name}</th>)}</tr></thead>
        <tbody>{(report.metrics || []).map(metric => <tr key={metric.name}>
          <th scope="row" style={{padding: 10, textAlign: "left"}}>{metric.name}</th>
          <td>{rate(metric.old, metric.total)}</td><td>{rate(metric.new, metric.total)}</td>
          <td>新增 {metric.gained} 题 · 丢失 {metric.lost} 题 · 净增 {metric.new - metric.old} 题</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p className="muted">{report.limitation}</p>
    <details>
      <summary>逐题新旧回答与证据（共 {report.questions?.length || 0} 题）</summary>
      <div style={{display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", margin: "12px 0"}}>
        <input aria-label="搜索视频对照题" placeholder="搜索题号、问题或回答" value={query} onChange={e => setQuery(e.target.value)} />
        <label><input type="checkbox" checked={changedOnly} onChange={e => setChangedOnly(e.target.checked)} />只看链路变化题</label>
        <span>显示 {questions.length} 题</span>
      </div>
      {questions.map(row => <details key={row.qa_id} style={{padding: 12, borderBottom: "1px solid #dce2ed"}}>
        <summary>{row.qa_id} · {row.question}</summary>
        <p>对应关键帧出现在候选：{yes(row.old_keyframe_hit)} → {yes(row.new_keyframe_hit)}；正确视频交付：{yes(row.old_correct_video_delivered)} → {yes(row.new_correct_video_delivered)}</p>
        <h4>上次回答</h4><p style={{whiteSpace: "pre-wrap"}}>{row.old_answer}</p>
        <h4>本次回答</h4><p style={{whiteSpace: "pre-wrap"}}>{row.new_answer}</p>
        <h4>参考答案</h4><p style={{whiteSpace: "pre-wrap"}}>{row.reference_answer}</p>
        <details><summary>核对候选与交付记录</summary><pre style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{JSON.stringify({
          old_candidates: row.old_candidate_snapshot, new_candidates: row.new_candidate_snapshot,
          old_delivered: row.old_returned_media, new_delivered: row.new_returned_media,
        }, null, 2)}</pre></details>
      </details>)}
    </details>
  </section>;
}

import { CaseJourney } from "./CaseJourney.tsx";
import { AIComparison } from "./AIComparison.tsx";
import { AnalysisScheduling } from "./AnalysisScheduling.tsx";
import { useState } from "react";
import { BASE, post } from "../../api/client.ts";
import {
  activeAnalysis,
  type AnalysisClaim,
  type AnalysisFilters,
  type AnalysisJob,
  type DiagnosisReport,
} from "../../api/analysis.ts";

export function AnalysisAI({
  runId,
  jobId,
  scoringProfile = '',
  stale,
  filters,
  total,
  ai,
  setAi,
  navigate,
}: {
  runId: string;
  jobId: string;
  scoringProfile?: string;
  stale: boolean;
  filters: AnalysisFilters;
  total: number;
  ai: AnalysisJob<DiagnosisReport> | null;
  setAi: (job: AnalysisJob<DiagnosisReport>) => void;
  navigate: (ref: string) => void;
}) {
  const [provider, setProvider] = useState("judge");
  const [scope, setScope] = useState("filter");
  const [pending, setPending] = useState(false),
    [error, setError] = useState("");
  async function start(mode: string, resume = false, summaryOnly = false) {
    setPending(true);
    setError("");
    try {
      setAi(
        await post(`/runs/${encodeURIComponent(runId)}/analysis`, {
          kind: "ai",
          parent_job: jobId,
          model_role: provider,
          mode,
          scope: resume ? "selection" : scope,
          filters,
          ...(resume ? { indices: ai?.report?.scope?.indices } : {}),
          ...(resume
            ? { resume_job: ai?.job_id, summary_only: summaryOnly }
            : {}),
        }),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }
  const disabled =
    pending ||
    stale ||
    activeAnalysis(ai) ||
    (scope === "filter" && !total);
  function claims(rows: AnalysisClaim[]) {
    return rows.map((c, i) => (
      <div key={i} className={`analysis-claim claim-${c.kind}`}>
        <span className="analysis-claim-kind">
          {
            {
              observation: "记录观察",
              hypothesis: "可能原因 · 待验证",
              recommendation: "建议验证",
            }[c.kind]
          }
        </span>
        <p>{c.text}</p>
        {c.limitations && <small>{c.limitations}</small>}
        <div className="analysis-toolbar">
          {c.evidence_refs.map((ref) => (
            <button
              key={ref}
              className="analysis-link"
              onClick={() => navigate(ref)}
            >
              {ref}
            </button>
          ))}
        </div>
      </div>
    ));
  }
  const report = ai?.report?.schema_version === 3 ? ai.report : undefined;
  const remaining = report ? Math.max(0, report.requested - report.covered) : 0;
  const completion = report && report.requested > 0
    ? report.covered === 0 ? "分析失败"
      : report.covered < report.requested || report.summary_status === "failed" ? "部分完成" : "分析完成"
    : "处理结束";
  const states: Record<string, string> = {
    queued: "等待运行",
    running: "分析中",
    paused: "等待测评结束",
    cancelling: "正在取消",
    completed: completion,
    failed: "分析失败",
    cancelled: "已取消",
    interrupted: "已中断",
  };
  return (
    <details className="analysis-ai-card">
      <summary className="analysis-section-heading">
        <div>
          <h3>AI 辅助分析</h3>
          <p>逐题分析所选范围内的问题，或对比两次测评的执行差异。</p>
        </div>
        <span className="analysis-ai-toggle"><span className="analysis-badge">手动启动</span><span className="analysis-ai-chevron" aria-hidden="true">▸</span></span>
      </summary>
      <div className="analysis-toolbar">
        <label>
          分析范围
          <select
            aria-label="分析范围"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="filter">当前筛选（{total}题）</option>
            <option value="run">全部题目</option>
          </select>
        </label>
        <label>
          分析模型
          <select
            aria-label="分析模型"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="judge">Judge（默认）</option>
            <option value="main">主模型</option>
          </select>
        </label>
        <button
          className="btn primary"
          disabled={disabled}
          onClick={() => void start("overview")}
        >
          分析所选题目并汇总
        </button>
        <button
          className="btn"
          disabled={disabled}
          onClick={() => void start("cases")}
        >
          仅逐题分析
        </button>
      </div>
      <p className="analysis-hint">
        对所选范围内的每道题逐题分析，不再抽样。“分析所选题目并汇总”还会综合逐题诊断生成总结；“仅逐题分析”不生成总结。
      </p>
      <details className="analysis-hint">
        <summary>GT 图片与分析说明</summary>
        <p>逐题分析自动附带标注的 GT 图片，与文字参考答案、回答和执行轨迹一起核对。有图题需使用支持图片输入的分析模型。</p>
        <p>GT 图仅供分析员核对画面，不代表答题模型看过或检索到了图片。预期拒答、澄清要求保持；人物心理、身份、时间和错误原因须有对应证据。图片与文字 GT 冲突时明确说明，不自动改分。</p>
        <p>未标注图片时按文字与轨迹分析；已标注图片无法读取时，该题分析失败并保留原因。视频不作为图片发送。图片在创建分析时冻结，未核实与原测评图片字节一致。汇总使用逐题诊断，不重复传图。</p>
      </details>
      {(error || ai?.error) && <p role="alert">{error || ai?.error}</p>}
      {ai && (report || activeAnalysis(ai)) && (
        <>
          <p role="status">
            {states[ai.status] || ai.status} · 已处理题目 {ai.processed} 题
            {report?.stage === "overview" ? " · 正在汇总" : ""}
          </p>
          {ai.stale && <p>此分析的来源或提示词已更新，请重新生成分析。</p>}
          {activeAnalysis(ai) && (
            <AnalysisScheduling jobId={ai.job_id} status={ai.status} />
          )}
          {activeAnalysis(ai) && (
            <button
              className="btn compact"
              onClick={() =>
                void post<AnalysisJob<DiagnosisReport>>(
                  `/analyses/${ai.job_id}/cancel`,
                  {},
                )
                  .then(setAi)
                  .catch((e) => setError(String(e)))
              }
            >
              取消AI分析
            </button>
          )}
        </>
      )}
      {ai?.report && !report && <p role="status">此分析为旧版格式，请重新生成分析。</p>}
      {report && (
        <>
          <p>
            分析范围 {report.requested} 题 · 诊断引用校验通过 {report.covered} 题
            {report.coverage &&
              ` · 格式或引用不合格 ${report.coverage.invalid} · 分析失败 ${report.coverage.failed} · 未尝试分析 ${report.coverage.unprocessed}`}
          </p>
          <p className="analysis-hint">引用校验仅确认来源，AI 结论仍需复核。</p>
          {!activeAnalysis(ai) && report.requested > 0 && report.covered === 0 && <p role="alert">本次没有有效逐题诊断，无法形成分析结论。请查看下方失败原因，修复后重新生成分析。</p>}
          {report.covered > 0 && report.overview && <section className="journey-overview"><h4>分析总结</h4><p className="analysis-hint">结合范围统计与有效逐题诊断生成；失败或未完成题目不作为已完成诊断。</p>{claims(report.overview.claims.slice(0, 2))}{report.overview.claims.length > 2 && <details><summary>展开其余总结（{report.overview.claims.length - 2}条）</summary>{claims(report.overview.claims.slice(2))}</details>}</section>}
          {report.summary_status === "failed" && (
            <p role="alert">
              汇总生成失败：{report.summary_error}。逐题诊断已保留。
            </p>
          )}
          {!activeAnalysis(ai) && !ai?.stale && report.scope && (
            <div>
            <div className="analysis-toolbar">
              {remaining > 0 && <button
                className="btn compact"
                disabled={pending || stale || !provider}
                title="沿用本次固定范围，保留有效诊断，处理未分析、失败或输出不合格的题目后生成总结"
                onClick={() => void start("overview", true)}
              >
                继续分析未完成的 {remaining} 题并汇总
              </button>}
              {report.covered > 0 && <button
                className="btn compact"
                disabled={pending || stale || !provider}
                title="再次调用AI，使用已有有效逐题诊断与范围统计生成总结，不重新逐题分析"
                onClick={() => void start("overview", true, true)}
              >
                {report.overview ? "重新生成总结" : "生成总结"}
              </button>}
            </div>
            <p className="analysis-hint">{remaining > 0 ? `本次范围还有 ${remaining} 题未取得有效分析。继续分析会处理这些题，保留已成功的结果。` : "本次范围的逐题分析已全部完成。"}{report.covered > 0 && `生成总结会再次调用AI，综合已有 ${report.covered} 题的有效分析，不重新逐题分析。`}</p>
            </div>
          )}
          <details>
            <summary>逐题诊断（{report.cases.length}）</summary>
            {report.cases.map((c) => <CaseJourney key={`${report.parent_job || jobId}:${c.index}`} diagnosis={c} jobId={report.parent_job || jobId} navigate={navigate} />)}
          </details>
          {report.facts && (
            <details id="analysis-scope:statistics">
              <summary>所选范围统计</summary>
              <p>程序统计覆盖本次固定范围内的全部题目；下方计数记录已完成诊断与失败、未完成题目。</p>
              <pre>{JSON.stringify(report.facts, null, 2)}</pre>
            </details>
          )}
          <details>
            <summary>分析用量与来源</summary>
            <p>
              {report.model} · 提示词 {report.prompt_version}
            </p>
            <p>
              本次请求 {report.requests?.length ?? "未记录"} 次 · 请求耗时{" "}
              {report.requests
                ? (
                    report.requests.reduce((n, r) => n + r.duration_ms, 0) /
                    1000
                  ).toFixed(1) + "s"
                : "未记录"}
            </p>
            <pre>{JSON.stringify(report.requests, null, 2)}</pre>
          </details>
          <div className="analysis-toolbar">
            {["html", "json", "csv"].map((format) => (
              <a
                className="btn compact"
                key={format}
                download
                href={`${BASE}/analyses/${ai?.job_id}/export?format=${format}`}
              >
                导出 {format.toUpperCase()}
              </a>
            ))}
          </div>
        </>
      )}
      <AIComparison scoringProfile={scoringProfile} key={jobId} runId={runId} jobId={jobId} scope={scope} filters={filters} modelRole={provider} disabled={disabled} />
    </details>
  );
}

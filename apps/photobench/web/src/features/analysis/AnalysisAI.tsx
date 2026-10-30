import { useEffect, useState } from "react";
import { api, BASE, post } from "../../api/client.ts";
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
  stale,
  filters,
  total,
  selected,
  ai,
  setAi,
  navigate,
}: {
  runId: string;
  jobId: string;
  stale: boolean;
  filters: AnalysisFilters;
  total: number;
  selected: number[];
  ai: AnalysisJob<DiagnosisReport> | null;
  setAi: (job: AnalysisJob<DiagnosisReport>) => void;
  navigate: (ref: string) => void;
}) {
  const [providers, setProviders] = useState<
    { id: string; label?: string; model?: string }[]
  >([]);
  const [provider, setProvider] = useState("");
  const [scope, setScope] = useState("filter");
  const [budget, setBudget] = useState(12);
  const [pending, setPending] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    void api<{ judge_providers?: typeof providers }>("/config", {
      signal: abort.signal,
    })
      .then((x) => setProviders(x.judge_providers || []))
      .catch((e) => {
        if (!abort.signal.aborted) setError(String(e));
      });
    return () => abort.abort();
  }, []);
  async function start(mode: string, resume = false, summaryOnly = false) {
    setPending(true);
    setError("");
    try {
      setAi(
        await post(`/runs/${encodeURIComponent(runId)}/analysis`, {
          kind: "ai",
          parent_job: jobId,
          provider_id: provider,
          mode,
          scope: resume ? "selection" : scope,
          filters,
          indices: resume ? ai?.report?.scope?.indices : selected,
          max_cases: budget,
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
    !provider ||
    activeAnalysis(ai) ||
    (scope === "selection" && !selected.length);
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
  const report = ai?.report;
  const states: Record<string, string> = {
    queued: "等待运行",
    running: "分析中",
    completed: "分析完成",
    failed: "分析失败",
    cancelled: "已取消",
    interrupted: "已中断",
  };
  return (
    <section className="analysis-ai-card">
      <div className="analysis-section-heading">
        <div>
          <h3>AI 辅助分析</h3>
          <p>归纳共性问题，按需深查具体案例。</p>
        </div>
        <span className="analysis-badge">手动启动</span>
      </div>
      <div className="analysis-toolbar">
        <label>
          分析范围
          <select
            aria-label="分析范围"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="filter">当前筛选（{total}题）</option>
            <option value="run">本轮全部</option>
            <option value="selection">已选题（{selected.length}题）</option>
          </select>
        </label>
        <label>
          分析模型
          <select
            aria-label="分析模型"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">选择模型</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label || p.id} · {p.model}
              </option>
            ))}
          </select>
        </label>
        <label>
          深查上限
          <select
            aria-label="深查上限"
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
          >
            {[6, 12, 24, 50, 100].map((n) => (
              <option value={n} key={n}>
                {n}题
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn primary"
          disabled={disabled}
          onClick={() => void start("overview")}
        >
          生成问题总览
        </button>
        <button
          className="btn"
          disabled={disabled}
          onClick={() => void start("cases")}
        >
          逐题深查
        </button>
      </div>
      <p className="analysis-hint">
        启动后固定分析范围，将案例证据发送给所选模型；全范围统计，最多深查{" "}
        {budget} 题。
      </p>
      {(error || ai?.error) && <p role="alert">{error || ai?.error}</p>}
      {ai && (
        <>
          <p role="status">
            {states[ai.status] || ai.status} · 已处理 {ai.processed} 题
            {report?.stage === "overview" ? " · 正在归纳" : ""}
          </p>
          {ai.stale && <p>此结果来源已过期，请重新分析。</p>}
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
      {report && (
        <>
          <p>
            范围 {report.requested} 题 · 引用合格 {report.covered} 题
            {report.coverage &&
              ` · 无效 ${report.coverage.invalid} · 失败 ${report.coverage.failed} · 未深查 ${report.coverage.unprocessed}`}
          </p>
          <p className="analysis-hint">{report.note}</p>
          {report.overview && claims(report.overview.claims)}
          {report.summary_status === "failed" && (
            <p role="alert">
              总览生成失败：{report.summary_error}。逐题结果已保留。
            </p>
          )}
          {!activeAnalysis(ai) && report.scope && (
            <div className="analysis-toolbar">
              <button
                className="btn compact"
                disabled={pending || stale || !provider}
                onClick={() => void start("overview", true)}
              >
                继续深查并归纳
              </button>
              <button
                className="btn compact"
                disabled={pending || stale || !provider}
                onClick={() => void start("overview", true, true)}
              >
                只重新归纳
              </button>
            </div>
          )}
          <details>
            <summary>逐题诊断（{report.cases.length}）</summary>
            {report.cases.map((c) => (
              <details key={c.index}>
                <summary>
                  {c.qa_id} ·{" "}
                  {c.valid ? "引用校验通过" : "分析未完成／引用无效"}
                </summary>
                {c.valid ? (
                  <>
                    {c.result.claims ? (
                      claims(c.result.claims)
                    ) : (
                      <>
                        <p>{c.result.observations}</p>
                        <p>可能原因：{c.result.possible_causes}</p>
                        <p>{c.result.recommendations}</p>
                      </>
                    )}
                    <p>
                      待补证据：
                      {Array.isArray(c.result.missing_evidence)
                        ? c.result.missing_evidence.join("；")
                        : c.result.missing_evidence}
                    </p>
                    <button
                      className="analysis-link"
                      onClick={() => navigate(`i${c.index}:case`)}
                    >
                      查看本题与轨迹
                    </button>
                  </>
                ) : (
                  <pre>{c.error || c.raw}</pre>
                )}
              </details>
            ))}
          </details>
          {report.facts && (
            <details id="analysis-scope:statistics">
              <summary>全范围事实统计</summary>
              <p>覆盖固定分析范围的全部题目，与深查样本数分开。</p>
              <pre>{JSON.stringify(report.facts, null, 2)}</pre>
            </details>
          )}
          <details>
            <summary>分析用量与来源</summary>
            <p>
              {report.model} · 提示词 {report.prompt_version || "旧版未记录"}
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
    </section>
  );
}

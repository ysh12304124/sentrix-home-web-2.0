import { PROFILE_LABELS } from "../../lib/scoring.ts";
import { useEffect, useRef, useState } from "react";
import { api, BASE, post } from "../../api/client.ts";
import type {
  AnalysisCase,
  AnalysisEvent,
  AnalysisFilters,
  AnalysisJob,
  AnalysisView,
  DiagnosisReport,
} from "../../api/analysis.ts";
import { activeAnalysis } from "../../api/analysis.ts";
import {
  analysisValue,
  displayWithLabels,
  reportAbilityLabels,
  scoreLabel,
  toolLabel,
} from "./presentation.ts";
import {
  AnalysisWorkspace,
  AnalysisFiltersBar,
  type AnalysisTab,
} from "./AnalysisWorkspace.tsx";
import { ScoringResults } from "../../components/ScoringResults.tsx";
import { AnalysisAI } from "./AnalysisAI.tsx";
import { DebugTracePanel } from "../qa/DebugTracePanel.tsx";
import type { RunItemDetail } from "../../api/types.ts";
import "./analysis.css";

const status: Record<string, string> = {
  not_generated: "尚未生成",
  queued: "等待测评退出",
  running: "分析中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "重启中断",
};
const pretty = (x: unknown) => JSON.stringify(x, null, 2);
const runPath = (id: string) => `/runs/${encodeURIComponent(id)}/analysis`;
function Evidence({
  jobId,
  c,
  event,
  sourceRef,
}: {
  jobId: string;
  c: AnalysisCase;
  event?: AnalysisEvent;
  sourceRef?: string;
}) {
  const [data, setData] = useState<{
      item: RunItemDetail;
      evidence_locations?: Record<string, string>;
    } | null>(null),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load() {
    setLoading(true);
    setError("");
    try {
      setData(await api(`/analyses/${jobId}/evidence/${c.index}`));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (!sourceRef) return;
    const abort = new AbortController();
    void api<{
      item: RunItemDetail;
      evidence_locations?: Record<string, string>;
    }>(`/analyses/${jobId}/evidence/${c.index}`, { signal: abort.signal })
      .then(setData)
      .catch((e) => {
        if (!abort.signal.aborted) setError(String(e));
      });
    return () => abort.abort();
  }, [sourceRef, jobId, c.index]);
  const item = data?.item;
  const path = sourceRef
    ? data?.evidence_locations?.[sourceRef]
    : event?.evidence_refs?.[0]?.path;
  const located =
    path === "case"
      ? c
      : path === ""
        ? item
        : path && item
          ? path
              .replace(/\[(\d+)\]/g, ".$1")
              .split(".")
              .reduce<unknown>(
                (value, key) =>
                  value && typeof value === "object"
                    ? (value as Record<string, unknown>)[key]
                    : undefined,
                item,
              )
          : null;
  return (
    <div>
      <button
        className="btn compact"
        disabled={loading}
        onClick={() => void load()}
      >
        {loading
          ? "读取中…"
          : event
            ? `查看证据 · 第${event.turn + 1}轮 · ${event.step_id || event.source}`
            : sourceRef
              ? `引用证据 · ${sourceRef}`
              : "查看本题轨迹与完整记录"}
      </button>
      {error && <p role="alert">{error}</p>}
      {item && (
        <>
          {located != null && (
            <details open>
              <summary>
                关联调用 ·{" "}
                {event?.tool_name ||
                  event?.component ||
                  sourceRef ||
                  "执行记录"}
              </summary>
              <pre>{pretty(located)}</pre>
            </details>
          )}
          {sourceRef && !path && (
            <p>此引用没有独立记录位置，完整来源见下方。</p>
          )}
          <DebugTracePanel detail={item} />
          <details>
            <summary>完整来源记录</summary>
            <pre>{pretty(item)}</pre>
          </details>
        </>
      )}
    </div>
  );
}
function CaseCard({
  c,
  abilityLabels,
  focusRef,
  jobId,
}: {
  c: AnalysisCase;
  abilityLabels: Record<string, string>;
  focusRef?: string;
  jobId: string;
}) {
  const [showAllEvents, setShowAllEvents] = useState(false);
  const matchedEvents = c.matched_event_ids
    ? c.events.filter((e) => c.matched_event_ids!.includes(e.event_id))
    : c.events;
  const shownEvents = showAllEvents ? c.events : matchedEvents;
  const displayLabel = (value: string) =>
    displayWithLabels(value, c.ability_labels || abilityLabels);
  const abilities = [c.dimensions.ability_family, c.dimensions.ability_type]
    .filter((value) => value && !["unknown", "not_applicable"].includes(value))
    .map(displayLabel)
    .join(" · ");
  return (
    <details className="analysis-case" id={`analysis-i${c.index}:case`}>
      <summary>
        <span className={`analysis-outcome outcome-${c.score ?? "None"}`}>
          {c.evaluation_result ? String(c.evaluation_result.score ?? "未评分") + "/" + String(c.evaluation_result.max_score) : scoreLabel(c.score)}
        </span>{" "}
        {c.question || c.qa_id || c.index}
        <small>
          {c.qa_id} · {c.turns.length}轮对话 ·{" "}
          {c.dimensions.benchmark_group &&
          c.dimensions.benchmark_group !== "unknown"
            ? abilities || displayLabel(c.dimensions.benchmark_group)
            : `${displayLabel(c.dimensions.content_subtype || "unknown")} · ${c.dimensions.content_type === "unknown" ? "未标注内容题型" : displayLabel(c.dimensions.content_type || "unknown")}`}{" "}
          · {displayLabel(c.execution)} · {c.events.length}条执行问题
        </small>
      </summary>
      {focusRef?.startsWith(`i${c.index}:`) && (
        <Evidence jobId={jobId} c={c} sourceRef={focusRef} />
      )}
      <h5>问题</h5>
      <p>{c.question}</p>
      <h5>{c.turns.length > 1 ? "最终任务参考答案" : "参考答案"}</h5>
      <p>{c.reference_answer || "未记录"}</p>
      <h5>{c.turns.length > 1 ? "最终回答" : "模型回答"}</h5>
      <p>{c.answer || "未记录"}</p>
      {c.scoring_evidence && <details>
        <summary>题型评分与扣分依据 · {displayLabel(c.dimensions.scoring_question_type || "unknown")}</summary>
        <p>本次评分标准、批次、判分理由及已保存的媒体集合差异；缺失记录见判断限制。</p>
        <pre>{pretty(c.scoring_evidence)}</pre>
      </details>}
      {c.text_truncated && <p>当前为截短摘要，完整内容可读取原始轨迹。</p>}
      <p>
        数据缺口：{c.missing.map(displayLabel).join("、") || "未发现"}
        ；检索/交付定位信号：
        {c.signals.map(displayLabel).join("、") || "无已知信号"}
      </p>
      {c.turns.map((t) => (
        <div className="analysis-turn" key={t.turn}>
          第{t.turn + 1}轮：预期{" "}
          {t.expected ? displayLabel(t.expected) : "未记录"} → 实际{" "}
          {t.actual ? displayLabel(t.actual) : "未记录"}；
          {t.behavior_correct === null
            ? "行为未判定"
            : t.behavior_correct
              ? "行为符合预期"
              : "行为不符合预期"}
          ；结束原因{" "}
          {t.termination_reason ? displayLabel(t.termination_reason) : "未记录"}
          {c.turns.length > 1 && (
            <>
              <p>
                <b>用户：</b>
                {t.question || "未记录"}
              </p>
              <p>
                <b>回答：</b>
                {t.answer || "未记录"}
              </p>
              <p>
                <b>本轮GT：</b>
                {t.reference_answer || "未记录"}
              </p>
              {t.context_turn_count != null && (
                <small>已携带 {t.context_turn_count} 轮历史</small>
              )}
            </>
          )}
          <p>{t.judge_reason}</p>
        </div>
      ))}
      {matchedEvents.length < c.events.length && (
        <p>
          命中 {matchedEvents.length} / 共 {c.events.length} 条执行问题{" "}
          <button
            className="btn compact"
            onClick={() => setShowAllEvents(!showAllEvents)}
          >
            {showAllEvents ? "仅看命中问题" : "展开其余问题"}
          </button>
        </p>
      )}
      <ol>
        {shownEvents.map((e) => (
          <li key={e.event_id} id={`analysis-${e.event_id}`}>
            <b>{e.label}</b> ·{" "}
            {[
              e.tool_name ? toolLabel(e.tool_name) : null,
              e.failure_reason ? analysisValue(e.failure_reason) : null,
              e.recovery === "unknown"
                ? "恢复状态未记录"
                : displayLabel(e.recovery),
              e.role ? displayLabel(e.role) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
            <p>{e.message}</p>
            <small>
              证据 {e.event_id} · {e.source} · 分类依据 {e.basis}
            </small>
            <Evidence jobId={jobId} c={c} event={e} />
          </li>
        ))}
      </ol>
      <Evidence jobId={jobId} c={c} />
    </details>
  );
}
function HtmlExport({ job }: { job: AnalysisJob<unknown> }) {
  return (
    <a
      className="btn compact"
      href={`${BASE}/analyses/${job.job_id}/export?format=html`}
      title="导出全部题目，在 HTML 中筛选查看"
      download
    >
      导出 HTML
    </a>
  );
}

export function ErrorAnalysisPanel({
  runId,
  terminal,
  scoringProfile: initialProfile = '', scoringBatch = '',
}: {
  runId: string;
  terminal: boolean;
  scoringProfile?:string; scoringBatch?:string;
}) {
  const [scoringProfile, setScoringProfile] = useState(initialProfile);
  const [open, setOpen] = useState(false),
    [job, setJob] = useState<AnalysisJob | null>(null),
    [error, setError] = useState("");
  const [tab, setTab] = useState<AnalysisTab>("overview");
  const [focusRef, setFocusRef] = useState("");
  const [tick, setTick] = useState(0),
    [filters, setFilters] = useState<AnalysisFilters>(() =>
      Object.fromEntries(
        [...new URLSearchParams(window.location.search)]
          .filter(([k]) => k.startsWith("ea_"))
          .map(([k, v]) => [k.slice(3), v]),
      ),
    ),
    [page, setPage] = useState(1);
  const [data, setData] = useState<AnalysisView | null>(null);
  const caseHeading = useRef<HTMLElement>(null);
  const [casesOpen, setCasesOpen] = useState(false);
  const [casesLoading, setCasesLoading] = useState(false);
  useEffect(() => {
    const back = () => {
      setFilters(
        Object.fromEntries(
          [...new URLSearchParams(window.location.search)]
            .filter(([k]) => k.startsWith("ea_"))
            .map(([k, v]) => [k.slice(3), v]),
        ),
      );
      setPage(1);
    };
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, []);
  useEffect(() => {
    if (!focusRef || casesLoading || !data) return;
    const target =
      document.getElementById(`analysis-${focusRef}`) ||
      document.getElementById(`analysis-${focusRef.split(":")[0]}:case`);
    if (target) {
      let p: HTMLElement | null = target;
      while (p) {
        if (p instanceof HTMLDetailsElement) p.open = true;
        p = p.parentElement;
      }
      target.scrollIntoView({ block: "center" });
    }
  }, [focusRef, casesLoading, data]);
  const [ai, setAi] = useState<AnalysisJob<DiagnosisReport> | null>(null);
  const [pending, setPending] = useState(false);
  const aiRevision = useRef(0);
  const [savedAiError, setSavedAiError] = useState("");
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const abort = new AbortController();
    let timer: number | undefined;
    const load = async () => {
      try {
        const x = await api<AnalysisJob>(
          runPath(runId)+(scoringProfile ? '?' + new URLSearchParams({scoring_profile:scoringProfile,scoring_batch:scoringBatch}) : ''),
          {
            signal: abort.signal,
          },
        );
        if (alive) {
          setJob(x);
          if (activeAnalysis(x))
            timer = window.setTimeout(() => void load(), 1500);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void load();

    return () => {
      alive = false;
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [open, runId, tick, scoringProfile, scoringBatch]);
  useEffect(() => {
    if (!open || job?.status !== "completed" || !job.job_id) return;
    let alive = true;
    setCasesLoading(true);
    const abort = new AbortController();
    void api<AnalysisView>(
      `/analyses/${job.job_id}/view?${new URLSearchParams({ ...filters, page: String(page), page_size: "20" })}`,
      { signal: abort.signal },
    )
      .then((x) => {
        if (alive) {
          setData(x);
          setCasesLoading(false);
        }
      })
      .catch((e) => {
        if (alive) {
          setError(String(e));
          setData(null);
          setCasesLoading(false);
        }
      });
    return () => {
      alive = false;
      abort.abort();
    };
  }, [open, job?.status, job?.job_id, filters, page]);
  useEffect(() => {
    if (!open || !activeAnalysis(ai)) return;
    let alive = true;
    const poll = async () => {
      try {
        if (activeAnalysis(ai)) {
          const x = await api<AnalysisJob<DiagnosisReport>>(
            `/analyses/${ai?.job_id}`,
          );
          if (alive) setAi(x);
        }

      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [open, ai]);
  useEffect(() => {
    setCasesOpen(false);
    setAi(null);
  }, [job?.job_id]);
  useEffect(() => {
    if (!open || !job?.job_id || job.run_id !== runId) return;
    const abort = new AbortController();
    const revision = aiRevision.current;
    setSavedAiError("");
    void (async () => {
      const history = await api<AnalysisJob<DiagnosisReport>[]>(
        `/runs/${encodeURIComponent(runId)}/analyses`, { signal: abort.signal },
      );
      const saved = history.find((entry) => entry.kind === "ai" &&
        entry.options?.parent_job === job.job_id);
      if (!saved?.job_id) return;
      const result = await api<AnalysisJob<DiagnosisReport>>(
        `/analyses/${saved.job_id}`, { signal: abort.signal },
      );
      if (!abort.signal.aborted && revision === aiRevision.current) setAi(result);
    })().catch((e) => {
      if (!abort.signal.aborted) setSavedAiError(`已有 AI 分析读取失败：${String(e)}`);
    });
    return () => abort.abort();
  }, [open, runId, job?.job_id, job?.run_id]);
  const r =
    job?.status === "completed" ? data?.summary || job.report : undefined;
  function filter(v: AnalysisFilters, replace = false) {
    setFilters((old) => {
      const next = Object.fromEntries(
        Object.entries(replace ? v : { ...old, ...v }).filter(
          ([, value]) => value,
        ),
      );
      const url = new URL(window.location.href);
      [...url.searchParams.keys()]
        .filter((k) => k.startsWith("ea_"))
        .forEach((k) => url.searchParams.delete(k));
      Object.entries(next).forEach(([k, value]) =>
        url.searchParams.set(`ea_${k}`, value),
      );
      window.history.pushState({}, "", url);
      return next;
    });
    setPage(1);
    setCasesOpen(true);
    setCasesLoading(true);
  }
  function browseCases(value: AnalysisFilters) {
    filter(value);
    caseHeading.current?.scrollIntoView({ block: "start" });
  }
  function navigate(ref: string) {
    if (ref === "scope:statistics") {
      setFocusRef(ref);
      return;
    }
    const match = /^i(\d+):/.exec(ref);
    if (match) {
      filter({ index: match[1] }, true);
      setFocusRef(ref.endsWith(":diagnosis") ? `i${match[1]}:case` : ref);
    } else {
      const group = ai?.report?.issues?.find((g) => g.group_id === ref);
      if (group)
        filter(
          {
            tool_name: group.tool_name,
            code: group.code,
            failure_reason: group.failure_reason,
            component: group.component || "",
          },
          true,
        );
    }
  }
  async function act(fn: () => Promise<void>) {
    setPending(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }
  const generate = () =>
    act(async () => {
      setJob(await post(runPath(runId), { kind: "rules", ...(scoringProfile ? {scoring_profile:scoringProfile,scoring_batch:scoringBatch} : {}) }));
        setPage(1);
      setTick((x) => x + 1);
    });
  return (
    <section className="analysis-panel">
      <button
        className="btn"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        错误分析 {open ? "收起" : "展开"}
      </button>
      {open && (
        <>
          <div className="analysis-toolbar">
            <label>分析标准 <select aria-label="错误分析评分标准" value={scoringProfile} onChange={e => {setScoringProfile(e.target.value);setJob(null);setData(null);setError('');setPage(1);}}>{Object.entries(PROFILE_LABELS).map(([id,label]) => <option key={id} value={id}>{label}</option>)}<option value="">历史评分</option></select></label>
            <b>{status[job?.status || ""] || "读取分析状态…"}</b>
            {activeAnalysis(job) && <span>已处理 {job?.processed} 题</span>}
            <button
              className="btn compact"
              disabled={!terminal || pending || activeAnalysis(job)}
              onClick={() => void generate()}
            >
              {job?.job_id ? "重新生成规则报告" : "生成规则报告"}
            </button>
            {activeAnalysis(job) && (
              <button
                className="btn compact"
                onClick={() =>
                  void act(async () => {
                    await post(`/analyses/${job?.job_id}/cancel`, {});
                    setTick((x) => x + 1);
                  })
                }
              >
                取消分析
              </button>
            )}
            {job?.status === "completed" && job.job_id && (
              <HtmlExport job={job} />
            )}
          </div>
          {!terminal && <p>测评和采样退出后自动分析；历史运行可手动生成。</p>}
          {job?.stale && (
            <p className="error">报告需更新，请重新生成后查看。</p>
          )}
          {(error || job?.error) && (
            <p className="error" role="alert">
              {error || job?.error}
            </p>
          )}
          {r && (
            <>
              {data && (
                <>
                  <AnalysisFiltersBar
                    data={data}
                    filters={filters}
                    change={filter}
                  />
                  <p className="analysis-hint">
                    当前范围 {data.total}/{job?.report?.item_count} 题 ·
                    页面统计沿用当前筛选；导出 HTML 包含全部题目，可在文件内筛选
                  </p>
                  {r.source.scoring_summary != null && <ScoringResults summary={{scoring:{profiles:{[String(r.source.scoring_profile)]:r.source.scoring_summary}}}} />}
                  <AnalysisWorkspace
                    data={data}
                    tab={tab}
                    setTab={setTab}
                    filter={browseCases}
                    unit={filters.content_unit}
                  />
                  {savedAiError && <p role="alert">{savedAiError}</p>}
                  <AnalysisAI
                    scoringProfile={scoringProfile}
                    runId={runId}
                    jobId={job!.job_id!}
                    stale={!!job?.stale}
                    filters={filters}
                    total={data.total}
                    ai={ai}
                    setAi={(value) => { aiRevision.current += 1; setAi(value); }}
                    navigate={navigate}
                  />
                </>
              )}
              <details>
                <summary>统计口径与来源</summary>
                <ul>
                  {r.definitions.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
                <p>
                  来源 {r.result_fingerprint} · 规则 {r.rule_version}
                </p>
              </details>
              <details
                className="analysis-case-list"
                open={casesOpen}
                onToggle={(event) => setCasesOpen(event.currentTarget.open)}
              >
                <summary
                  ref={caseHeading}
                  className="analysis-case-heading"
                >
                  题目列表
                  <span className="analysis-hint"> · {data?.total || 0} 题</span>
                </summary>
                <p role="status">
                  {casesLoading
                    ? "正在读取筛选结果…"
                    : `筛选命中 ${data?.total || 0}题 · 第${page}页`}{" "}
                </p>
                {casesLoading ? null : !data?.total ? (
                  <p>没有符合当前条件的题目。可清空筛选查看全部。</p>
                ) : (
                  data!.items.map((c) => (
                    <CaseCard
                      focusRef={focusRef}
                      abilityLabels={reportAbilityLabels(r!)}
                      key={`${job?.job_id}-${c.index}`}
                      c={c}
                      jobId={job!.job_id!}

                    />
                  ))
                )}
                <div className="analysis-toolbar">
            <label>分析标准 <select aria-label="错误分析评分标准" value={scoringProfile} onChange={e => {setScoringProfile(e.target.value);setJob(null);setData(null);setError('');setPage(1);}}>{Object.entries(PROFILE_LABELS).map(([id,label]) => <option key={id} value={id}>{label}</option>)}<option value="">历史评分</option></select></label>
                  <button
                    className="btn compact"
                    disabled={casesLoading || page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="btn compact"
                    disabled={casesLoading || page * 20 >= (data?.total || 0)}
                    onClick={() => setPage(page + 1)}
                  >
                    下一页
                  </button>
                </div>
              </details>

            </>
          )}

        </>
      )}
    </section>
  );
}

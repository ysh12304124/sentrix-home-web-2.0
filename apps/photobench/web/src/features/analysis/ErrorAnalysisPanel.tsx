import { useEffect, useRef, useState } from "react";
import { api, BASE, post } from "../../api/client.ts";
import type {
  AnalysisCase,
  AnalysisEvent,
  AnalysisFilters,
  AnalysisJob,
  AnalysisView,
  ComparisonReport,
  DiagnosisReport,
} from "../../api/analysis.ts";
import { activeAnalysis } from "../../api/analysis.ts";
import { ComparisonPicker } from "./ComparisonPicker.tsx";
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
  selected,
  toggle,
}: {
  c: AnalysisCase;
  abilityLabels: Record<string, string>;
  focusRef?: string;
  jobId: string;
  selected: boolean;
  toggle: () => void;
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
        <input
          aria-label={`选择 ${c.qa_id}`}
          type="checkbox"
          checked={selected}
          onClick={(e) => e.stopPropagation()}
          onChange={toggle}
        />{" "}
        <span className={`analysis-outcome outcome-${c.score ?? "None"}`}>
          {scoreLabel(c.score)}
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
function Exports({
  job,
  filters = {},
}: {
  job: AnalysisJob<unknown>;
  filters?: AnalysisFilters;
}) {
  return (
    <span className="analysis-toolbar">
      {["html", "json", "csv"].map((format) => (
        <a
          className="btn compact"
          key={format}
          href={`${BASE}/analyses/${job.job_id}/export?${new URLSearchParams({ ...filters, format })}`}
          download
        >
          导出 {format.toUpperCase()}
        </a>
      ))}
    </span>
  );
}

export function ErrorAnalysisPanel({
  runId,
  terminal,
}: {
  runId: string;
  terminal: boolean;
}) {
  const [viewJob, setViewJob] = useState("");
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
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const [casesLoading, setCasesLoading] = useState(false);
  const [preparingOther, setPreparingOther] = useState<AnalysisJob | null>(
    null,
  );
  const [selected, setSelected] = useState<number[]>([]);
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
  const [ai, setAi] = useState<AnalysisJob<DiagnosisReport> | null>(null),
    [compare, setCompare] = useState<AnalysisJob<ComparisonReport> | null>(
      null,
    );
  const [otherRun, setOtherRun] = useState(""),
    [pending, setPending] = useState(false),
    [history, setHistory] = useState<AnalysisJob<unknown>[]>([]),
    [cp, setCp] = useState(1);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const abort = new AbortController();
    let timer: number | undefined;
    const load = async () => {
      try {
        const x = await api<AnalysisJob>(
          viewJob ? `/analyses/${viewJob}` : runPath(runId),
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
  }, [open, runId, tick, viewJob]);
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
    if (!open || (!activeAnalysis(ai) && !activeAnalysis(compare))) return;
    let alive = true;
    const poll = async () => {
      try {
        if (activeAnalysis(ai)) {
          const x = await api<AnalysisJob<DiagnosisReport>>(
            `/analyses/${ai?.job_id}`,
          );
          if (alive) setAi(x);
        }
        if (activeAnalysis(compare)) {
          const x = await api<AnalysisJob<ComparisonReport>>(
            `/analyses/${compare?.job_id}`,
          );
          if (alive) setCompare(x);
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
  }, [open, ai, compare]);
  useEffect(() => {
    setSelected([]);
    setAi(null);
    setCompare(null);
    setPreparingOther(null);
  }, [job?.job_id]);
  useEffect(() => {
    if (!open || !preparingOther?.job_id) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const other = await api<AnalysisJob>(
            `/analyses/${preparingOther.job_id}`,
          );
          if (!alive) return;
          if (other.status === "completed" && !other.stale) {
            const result = await post<AnalysisJob<ComparisonReport>>(
              runPath(runId),
              {
                kind: "compare",
                left_job: job?.job_id,
                right_job: other.job_id,
              },
            );
            if (alive) {
              setCompare(result);
              setPreparingOther(null);
              setCp(1);
            }
          } else if (activeAnalysis(other)) setPreparingOther(other);
          else {
            setError(
              `对比运行的报告${status[other.status] || other.status}，请重试。${other.error || ""}`,
            );
            setPreparingOther(null);
          }
        } catch (e) {
          if (alive) {
            setError(String(e));
            setPreparingOther(null);
          }
        }
      })();
    }, 1500);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [open, preparingOther, job?.job_id, runId]);
  const r =
    job?.status === "completed" ? data?.summary || job.report : undefined;
  const displayLabel = (value: string) =>
    displayWithLabels(value, r ? reportAbilityLabels(r) : {});
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
    setSelected([]);
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
      setViewJob("");
      setJob(await post(runPath(runId), { kind: "rules" }));
      setSelected([]);
      setPage(1);
      setTick((x) => x + 1);
    });
  const compareRuns = () =>
    act(async () => {
      if (!otherRun || otherRun === runId) return;
      let other = await api<AnalysisJob>(runPath(otherRun));
      if (!other.job_id || other.status !== "completed" || other.stale) {
        if (!activeAnalysis(other) || other.stale)
          other = await post(runPath(otherRun), { kind: "rules" });
        setPreparingOther(other);
        return;
      }
      setCompare(
        await post(runPath(runId), {
          kind: "compare",
          left_job: job?.job_id,
          right_job: other.job_id,
        }),
      );
      setCp(1);
    });
  const openHistory = (h: AnalysisJob<unknown>) =>
    act(async () => {
      if (h.kind === "rules") {
        setViewJob(h.job_id || "");
        setSelected([]);
        setPage(1);
        setJob(await api(`/analyses/${h.job_id}`));
      } else if (h.kind === "ai") setAi(await api(`/analyses/${h.job_id}`));
      else setCompare(await api(`/analyses/${h.job_id}`));
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
            <button
              className="btn compact"
              onClick={() =>
                void act(async () =>
                  setHistory(
                    await api(`/runs/${encodeURIComponent(runId)}/analyses`),
                  ),
                )
              }
            >
              历史分析任务
            </button>
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
          {history.length > 0 && (
            <details open>
              <summary>历史任务（旧报告保留来源）</summary>
              {history.map((h) => (
                <p key={h.job_id}>
                  <button
                    className="btn compact"
                    onClick={() => void openHistory(h)}
                  >
                    {
                      (
                        {
                          rules: "统计报告",
                          compare: "逐题对比",
                          ai: "AI分析",
                        } as Record<string, string>
                      )[h.kind || ""]
                    }{" "}
                    · {status[h.status] || h.status} ·{" "}
                    {h.created_at
                      ? new Date(h.created_at * 1000).toLocaleString()
                      : h.job_id}
                  </button>
                </p>
              ))}
            </details>
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
                    所有统计与导出沿用当前筛选
                  </p>
                  <AnalysisWorkspace
                    data={data}
                    tab={tab}
                    setTab={setTab}
                    filter={browseCases}
                    unit={filters.content_unit}
                  />
                  <AnalysisAI
                    runId={runId}
                    jobId={job!.job_id!}
                    stale={!!job?.stale}
                    filters={filters}
                    total={data.total}
                    selected={selected}
                    ai={ai}
                    setAi={setAi}
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
              <h3
                ref={caseHeading}
                tabIndex={-1}
                className="analysis-case-heading"
              >
                题目列表
              </h3>
              <div className="analysis-toolbar">
                {job && <Exports job={job} filters={filters} />}
                <button
                  className="btn compact"
                  disabled={casesLoading || !data?.items.length}
                  onClick={() =>
                    setSelected((old) => [
                      ...new Set([
                        ...old,
                        ...(data?.items.map((c) => c.index) || []),
                      ]),
                    ])
                  }
                >
                  全选本页
                </button>
                <button className="btn compact" onClick={() => setSelected([])}>
                  清空选择
                </button>
              </div>
              <p role="status">
                {casesLoading
                  ? "正在读取筛选结果…"
                  : `筛选命中 ${data?.total || 0}题 · 第${page}页`}{" "}
                · 已选择 {selected.length} 题
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
                    selected={selected.includes(c.index)}
                    toggle={() => {
                      if (pending) return;
                      if (selected.includes(c.index))
                        setSelected(selected.filter((i) => i !== c.index));
                      else setSelected([...selected, c.index]);
                    }}
                  />
                ))
              )}
              <div className="analysis-toolbar">
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
              <h3>两轮逐题对比</h3>
              <p>选择另一轮，与本轮逐题比较。</p>
              <ComparisonPicker
                runId={runId}
                value={otherRun}
                onChange={setOtherRun}
                disabled={
                  pending || !!preparingOther || activeAnalysis(compare)
                }
              />
              <div className="analysis-toolbar">
                <button
                  className="btn compact"
                  disabled={
                    pending ||
                    !!job?.stale ||
                    !otherRun ||
                    !!preparingOther ||
                    activeAnalysis(compare)
                  }
                  onClick={() => void compareRuns()}
                >
                  创建对比报告
                </button>
                {preparingOther && (
                  <>
                    <span role="status">
                      正在准备所选运行的规则报告：
                      {status[preparingOther.status] || preparingOther.status}
                      ，完成后自动对比。
                    </span>
                    <button
                      className="btn compact"
                      onClick={() => setPreparingOther(null)}
                    >
                      停止等待
                    </button>
                  </>
                )}
              </div>
            </>
          )}
          {compare && (
            <section>
              <h4>逐题对比 · {status[compare.status]}</h4>
              {compare.error && <p role="alert">{compare.error}</p>}
              {compare.stale && <p>来源已过期</p>}
              {activeAnalysis(compare) && (
                <button
                  className="btn compact"
                  onClick={() =>
                    void act(async () =>
                      setCompare(
                        await post(`/analyses/${compare.job_id}/cancel`, {}),
                      ),
                    )
                  }
                >
                  取消对比
                </button>
              )}
              {compare.report && (
                <>
                  <p>
                    {compare.report.comparable
                      ? `另一轮相对本轮：改善 ${compare.report.quality_changes?.improved || 0} 题，退步 ${compare.report.quality_changes?.regressed || 0} 题，持平 ${compare.report.quality_changes?.unchanged || 0} 题，缺分等无法判定 ${compare.report.quality_changes?.unknown || 0} 题。`
                      : "配置不具可比性，仅并排展示事实，不给出版本提升结论。"}
                  </p>
                  <p>
                    配置差异：
                    {compare.report.differences.map(displayLabel).join("、") ||
                      "未发现已记录差异"}
                    ；定义一致 {compare.report.pairing_counts.paired || 0}
                    题，定义变化 {compare.report.pairing_counts.changed || 0}
                    题，未匹配 {compare.report.pairing_counts.unmatched || 0}
                    题；重复/空ID：
                    {compare.report.duplicates.join("、") || "无"}
                  </p>
                  <p>{compare.report.note}</p>
                  <p>
                    未记录的关键配置：
                    {compare.report.missing_configuration
                      .map(displayLabel)
                      .join("、") || "无"}
                  </p>
                  <Exports job={compare} />
                  <details>
                    <summary>来源与配置</summary>
                    <pre>{pretty(compare.report.sources)}</pre>
                  </details>
                  {compare.report.rows
                    .slice((cp - 1) * 20, cp * 20)
                    .map((row) => (
                      <details key={row.qa_id}>
                        <summary>
                          {row.qa_id} · {displayLabel(row.pairing)} ·{" "}
                          {displayLabel(row.quality_change)} · 分数变化{" "}
                          {row.score_delta ?? "未知"}
                        </summary>
                        <p>
                          参考答案变化 {row.gt_changed ? "有" : "无"}
                          ；执行状态变化 {row.execution_changed ? "有" : "无"}
                          ；新增异常 {row.errors_added.join("、") || "无"}
                          ；消失异常 {row.errors_removed.join("、") || "无"}
                        </p>
                        <div className="analysis-grid">
                          {[
                            ["本轮（基准）", row.left],
                            ["所选另一轮", row.right],
                          ].map(([label, entry]) => {
                            const c = entry as AnalysisCase | null;
                            return (
                              <article key={String(label)}>
                                <h5>{String(label)}</h5>
                                {c ? (
                                  <>
                                    <p>{c.question}</p>
                                    <p>
                                      {scoreLabel(c.score)} ·{" "}
                                      {displayLabel(c.execution)} ·{" "}
                                      {c.events.length}次异常
                                    </p>
                                    <h5>模型回答</h5>
                                    <p>{c.answer || "未记录"}</p>
                                    <h5>参考答案</h5>
                                    <p>{c.reference_answer || "未记录"}</p>
                                    <details>
                                      <summary>完整案例数据</summary>
                                      <pre>{pretty(c)}</pre>
                                    </details>
                                  </>
                                ) : (
                                  <p>此轮未匹配到该题。</p>
                                )}
                              </article>
                            );
                          })}
                        </div>
                      </details>
                    ))}
                  <button
                    className="btn compact"
                    disabled={cp <= 1}
                    onClick={() => setCp(cp - 1)}
                  >
                    对比上一页
                  </button>
                  <button
                    className="btn compact"
                    disabled={cp * 20 >= compare.report.rows.length}
                    onClick={() => setCp(cp + 1)}
                  >
                    对比下一页
                  </button>
                </>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}

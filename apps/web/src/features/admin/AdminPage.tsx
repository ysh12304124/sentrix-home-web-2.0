import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../../app/AuthProvider";

interface ServiceState {
  ok?: boolean;
  status_code?: number;
  error?: string;
  status?: {
    jobs?: Record<string, number>;
    turns?: Record<string, number>;
    assets?: number;
    embeddings?: boolean;
  };
}

interface GpuState {
  status?: string;
  name?: string;
  utilization_percent?: number | null;
  memory_used_mib?: number | null;
  memory_total_mib?: number | null;
  temperature_c?: number | null;
  power_w?: number | null;
}

interface Overview {
  sampled_at: string | null;
  stale: boolean;
  host?: {
    cpu_percent?: number | null;
    load_1m?: number | null;
    ram_used_gb?: number;
    ram_total_gb?: number;
    disk_free_gb?: number | null;
    gpu?: GpuState;
  };
  services?: {
    control?: ServiceState;
    model?: ServiceState;
    bge?: ServiceState;
    clip?: ServiceState;
    users?: Record<string, ServiceState>;
  };
  units?: Record<string, Record<string, unknown>>;
  model_queue?: {
    upstream?: string;
    model_id?: string;
    slots?: number;
    active?: number;
    waiting?: Record<string, number>;
    last_ok_at?: number | null;
  };
  open_incidents?: number;
}

interface Incident {
  event_id: string;
  time: string;
  source: string;
  severity: string;
  category: string;
  message: string;
  status: string;
  occurrences: number;
}

interface AdminJob {
  account: string;
  job_id: string;
  file_name: string;
  status: string;
  stage: string;
  attempts: number;
  error_message: string;
}

interface FeedbackItem {
  id: string;
  account_id: string;
  kind: string;
  body: string;
  status: string;
  reply: string;
  assignee: string;
  severity: string;
  internal_note: string;
  created_at: string;
}

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)sentrix_control_csrf=([^;]*)/);
  return match === null ? "" : decodeURIComponent(match[1]);
}

const KIND_LABELS: Record<string, string> = {
  problem: "问题",
  inaccurate: "内容不准确",
  suggestion: "建议",
};

const STATUS_LABELS: Record<string, string> = {
  received: "已收到",
  triaging: "处理中",
  processed: "已处理",
};

export function AdminPage() {
  const { identity } = useAuth();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [problemJobs, setProblemJobs] = useState<AdminJob[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<Record<string, { status: string; reply: string }>>({});
  const [saving, setSaving] = useState("");

  const load = useCallback(async () => {
    try {
      const [overviewResponse, incidentsResponse, feedbackResponse] = await Promise.all([
        fetch("/control/v1/admin/overview", { credentials: "same-origin" }),
        fetch("/control/v1/admin/incidents?limit=30", { credentials: "same-origin" }),
        fetch("/control/v1/admin/feedback", { credentials: "same-origin" }),
      ]);
      if (!overviewResponse.ok) throw new Error(`概览接口返回 ${overviewResponse.status}`);
      const nextOverview = (await overviewResponse.json()) as Overview;
      setOverview(nextOverview);
      const accounts = Object.keys(nextOverview.services?.users ?? {});
      const jobResponses = await Promise.all(
        accounts.map(async (account) => {
          const response = await fetch(`/control/v1/admin/users/${account}/jobs`, {
            credentials: "same-origin",
          });
          if (!response.ok) return [] as AdminJob[];
          const body = (await response.json()) as {
            items?: Array<{
              job_id: string;
              file_name: string;
              status: string;
              stage: string;
              attempts: number;
              error_message: string;
            }>;
          };
          return (body.items ?? [])
            .filter((job) => job.status === "needs_attention" || job.status === "partial")
            .map((job) => ({ ...job, account }));
        }),
      );
      setProblemJobs(jobResponses.flat());
      setIncidents(
        incidentsResponse.ok
          ? ((await incidentsResponse.json()) as { items: Incident[] }).items
          : [],
      );
      const items = feedbackResponse.ok
        ? ((await feedbackResponse.json()) as { items: FeedbackItem[] }).items
        : [];
      setFeedback(items);
      setDraft((previous) => {
        const next = { ...previous };
        for (const item of items) {
          if (next[item.id] === undefined) {
            next[item.id] = { status: item.status, reply: item.reply };
          }
        }
        return next;
      });
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    if (identity?.role !== "admin") return;
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(timer);
  }, [identity, load]);

  const saveFeedback = useCallback(
    async (item: FeedbackItem) => {
      const value = draft[item.id];
      if (value === undefined) return;
      setSaving(item.id);
      try {
        const response = await fetch(`/control/v1/admin/feedback/${item.id}`, {
          method: "PATCH",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "x-csrf-token": csrfToken(),
          },
          body: JSON.stringify({ status: value.status, reply: value.reply }),
        });
        if (!response.ok) throw new Error(`保存失败（${response.status}）`);
        await load();
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setSaving("");
      }
    },
    [draft, load],
  );

  const requeue = useCallback(
    async (job: AdminJob) => {
      setSaving(job.job_id);
      try {
        const response = await fetch(
          `/control/v1/admin/users/${job.account}/jobs/${job.job_id}/requeue`,
          {
            method: "POST",
            credentials: "same-origin",
            headers: { "x-csrf-token": csrfToken() },
          },
        );
        if (!response.ok) throw new Error(`重排失败（${response.status}）`);
        await load();
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setSaving("");
      }
    },
    [load],
  );

  const rows = useMemo(() => serviceRows(overview), [overview]);
  const okCount = rows.filter((row) => row.state?.ok === true).length;
  const waiting = overview?.model_queue?.waiting ?? {};
  const queued = Object.values(waiting).reduce((total, value) => total + value, 0);
  const cpu = Math.max(0, Math.min(100, overview?.host?.cpu_percent ?? 0));
  const ramUsed = overview?.host?.ram_used_gb ?? 0;
  const ramTotal = overview?.host?.ram_total_gb ?? 0;
  const ramPercent = ramTotal > 0 ? Math.min(100, (ramUsed / ramTotal) * 100) : 0;
  const gpu = overview?.host?.gpu;
  const vramPercent =
    gpu?.memory_total_mib && gpu.memory_used_mib
      ? Math.min(100, (gpu.memory_used_mib / gpu.memory_total_mib) * 100)
      : 0;
  const diskFree = overview?.host?.disk_free_gb;
  const filteredFeedback = feedback.filter((item) => filter === "" || item.status === filter);

  if (identity?.role !== "admin") {
    return (
      <section className="view-root">
        <div className="empty-panel">当前账号没有管理权限。</div>
      </section>
    );
  }

  return (
    <section className="admin-page">
      <header className="admin-head">
        <div>
          <p className="admin-eyebrow">运营中心</p>
          <h1>运行监控</h1>
          <p>
            采样时间 {overview?.sampled_at ? formatTime(overview.sampled_at) : "未采集"} ·
            每 10 秒刷新
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="live-pill" data-stale={overview?.stale === true}>
            <i />
            {overview?.stale === true ? "采样已过期" : "实时"}
          </span>
          <button type="button" className="ghost-action" onClick={() => void load()}>
            立即刷新
          </button>
        </div>
      </header>

      {error !== "" ? <div className="empty-panel" role="alert">{error}</div> : null}

      <div className="kpi-grid">
        <article className="kpi-card">
          <div className="kpi-label">服务可用</div>
          <div className="kpi-value">{okCount} / {rows.length}</div>
          <div className="kpi-note">控制面、模型、嵌入与用户 API</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-label">未解决告警</div>
          <div className="kpi-value">{overview?.open_incidents ?? "—"}</div>
          <div className="kpi-note">服务中断、磁盘与传感器</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-label">模型槽位</div>
          <div className="kpi-value">
            {overview?.model_queue?.active ?? 0} / {overview?.model_queue?.slots ?? "—"}
          </div>
          <div className="kpi-note">等待 {queued} 个请求</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-label">磁盘可用</div>
          <div className="kpi-value">{diskFree ?? "—"} GiB</div>
          <div className="kpi-note">
            {typeof diskFree === "number" && diskFree < 100
              ? "低于 100 GiB，已暂停新上传"
              : typeof diskFree === "number" && diskFree < 150
                ? "低于 150 GiB 告警阈值"
                : "阈值 150 / 100 GiB"}
          </div>
        </article>
      </div>

      <div className="dash-grid">
        <article className="panel">
          <div className="panel-head">
            <h2>服务状态</h2>
            <small>按真实端点探活</small>
          </div>
          <div className="service-list">
            {rows.map((row) => (
              <div key={row.key} className="service-row" data-ok={row.state?.ok === true}>
                <span
                  className={`status-dot ${
                    row.state === undefined ? "unknown" : row.state.ok === true ? "" : "bad"
                  }`}
                />
                <div>
                  <strong>{row.name}</strong>
                  <small>{serviceDetail(row.state)}</small>
                </div>
                <span className="service-badge">
                  {row.state === undefined ? "未采集" : row.state.ok === true ? "正常" : "异常"}
                </span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="panel-head">
            <h2>机器资源</h2>
            <small>GPU 传感器{ gpu?.status === "ok" ? "正常" : "不可读" }</small>
          </div>
          <div className="meter">
            <div className="meter-head"><span>CPU</span><span>{cpu.toFixed(0)}%</span></div>
            <div className="meter-track"><div className="meter-fill" style={{ width: `${cpu}%` }} /></div>
          </div>
          <div className="meter">
            <div className="meter-head">
              <span>内存</span><span>{ramUsed} / {ramTotal} GiB</span>
            </div>
            <div className="meter-track">
              <div className={`meter-fill ${ramPercent > 85 ? "warn" : ""}`} style={{ width: `${ramPercent}%` }} />
            </div>
          </div>
          <div className="meter">
            <div className="meter-head">
              <span>显存</span>
              <span>
                {gpu?.memory_used_mib ?? "—"} / {gpu?.memory_total_mib ?? "—"} MiB
              </span>
            </div>
            <div className="meter-track">
              <div
                className={`meter-fill ${vramPercent > 90 ? "bad" : vramPercent > 80 ? "warn" : ""}`}
                style={{ width: `${vramPercent}%` }}
              />
            </div>
          </div>
          <div className="queue-chips" style={{ marginTop: 16 }}>
            <span className="queue-chip">GPU 温度 <b>{gpu?.temperature_c ?? "—"}</b> °C</span>
            <span className="queue-chip">功率 <b>{gpu?.power_w ?? "—"}</b> W</span>
            <span className="queue-chip">负载 <b>{overview?.host?.load_1m ?? "—"}</b></span>
          </div>
        </article>
      </div>

      <div className="dash-grid">
        <article className="panel">
          <div className="panel-head">
            <h2>共享模型调度</h2>
            <small>{overview?.model_queue?.model_id ?? "未知模型"}</small>
          </div>
          <div className="queue-chips">
            <span className="queue-chip">执行中 <b>{overview?.model_queue?.active ?? 0}</b></span>
            <span className="queue-chip">问答等待 <b>{waiting.chat ?? 0}</b></span>
            <span className="queue-chip">导入等待 <b>{waiting.import ?? 0}</b></span>
            <span className="queue-chip">评测等待 <b>{waiting.benchmark ?? 0}</b></span>
          </div>
          <p style={{ margin: "14px 0 0", color: "var(--ex-muted)", fontSize: 11.5 }}>
            上游 {overview?.model_queue?.upstream ?? "—"} · 槽位 {overview?.model_queue?.slots ?? "—"}
            {overview?.model_queue?.last_ok_at
              ? ` · 最近成功 ${formatTime(
                  new Date(overview.model_queue.last_ok_at * 1000).toISOString(),
                )}`
              : ""}
          </p>
          <div className="service-list" style={{ marginTop: 14 }}>
            {Object.entries(overview?.services?.users ?? {}).map(([account, state]) => (
              <div key={account} className="service-row" data-ok={state.ok === true}>
                <span className={`status-dot ${state.ok === true ? "" : "bad"}`} />
                <div>
                  <strong>{account}</strong>
                  <small>
                    作业 {sum(state.status?.jobs)} · 回合 {sum(state.status?.turns)} · 资产{" "}
                    {state.status?.assets ?? 0}
                  </small>
                </div>
                <span className="service-badge">{state.ok === true ? "在线" : "离线"}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="panel-head">
            <h2>告警与事件</h2>
            <small>近 30 条</small>
          </div>
          {incidents.length === 0 ? (
            <div className="empty-panel">暂无事件记录。</div>
          ) : (
            <ul className="incident-list">
              {incidents.map((incident) => (
                <li
                  key={incident.event_id}
                  className="incident-item"
                  data-severity={incident.severity}
                  data-status={incident.status}
                >
                  <strong>{incident.message}</strong>
                  <small>
                    {incident.source} · {incident.category} ·{" "}
                    {incident.status === "open" ? "未解决" : "已恢复"} · 次数 {incident.occurrences}
                  </small>
                  <small>{formatTime(incident.time)}</small>
                </li>
              ))}
            </ul>
          )}
        </article>
      </div>

      <article className="panel">
        <div className="panel-head">
          <h2>需要处理的作业</h2>
          <small>只有已停下、未被执行的作业允许重排</small>
        </div>
        {problemJobs.length === 0 ? (
          <div className="empty-panel">当前没有需要人工介入的导入作业。</div>
        ) : (
          <div className="service-list">
            {problemJobs.map((job) => (
              <div key={`${job.account}-${job.job_id}`} className="service-row" data-ok="false">
                <span className="status-dot bad" />
                <div>
                  <strong>{job.file_name}</strong>
                  <small>
                    {job.account} · {job.status} · {job.stage || "—"} · 第 {job.attempts} 次
                    {job.error_message !== "" ? ` · ${job.error_message.slice(0, 90)}` : ""}
                  </small>
                </div>
                <button
                  type="button"
                  className="ghost-action"
                  disabled={saving === job.job_id}
                  onClick={() => void requeue(job)}
                >
                  {saving === job.job_id ? "重排中…" : "重新排队"}
                </button>
              </div>
            ))}
          </div>
        )}
      </article>

      <article className="panel">
        <div className="panel-head">
          <h2>反馈收件箱</h2>
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            style={{
              font: "inherit",
              fontSize: 12,
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid var(--ex-line)",
              background: "#fff",
            }}
          >
            <option value="">全部状态</option>
            <option value="received">已收到</option>
            <option value="triaging">处理中</option>
            <option value="processed">已处理</option>
          </select>
        </div>
        {filteredFeedback.length === 0 ? (
          <div className="empty-panel">当前筛选下没有反馈。</div>
        ) : (
          <div className="feedback-admin-list">
            {filteredFeedback.map((item) => {
              const value = draft[item.id] ?? { status: item.status, reply: item.reply };
              return (
                <div key={item.id} className="feedback-admin-item">
                  <header>
                    <strong>{item.account_id}</strong>
                    <span className="job-status" data-status={item.status}>
                      {STATUS_LABELS[item.status] ?? item.status}
                    </span>
                    <span className="job-status">{KIND_LABELS[item.kind] ?? item.kind}</span>
                    <small style={{ marginLeft: "auto", color: "var(--ex-muted)" }}>
                      {formatTime(item.created_at)}
                    </small>
                  </header>
                  <p>{item.body}</p>
                  <div style={{ display: "grid", gridTemplateColumns: "160px 1fr auto", gap: 8 }}>
                    <select
                      value={value.status}
                      onChange={(event) =>
                        setDraft((previous) => ({
                          ...previous,
                          [item.id]: { ...value, status: event.target.value },
                        }))
                      }
                    >
                      <option value="received">已收到</option>
                      <option value="triaging">处理中</option>
                      <option value="processed">已处理</option>
                    </select>
                    <textarea
                      rows={2}
                      placeholder="给用户的简短答复（可选）"
                      value={value.reply}
                      onChange={(event) =>
                        setDraft((previous) => ({
                          ...previous,
                          [item.id]: { ...value, reply: event.target.value },
                        }))
                      }
                    />
                    <button
                      type="button"
                      className="ghost-action"
                      disabled={saving === item.id}
                      onClick={() => void saveFeedback(item)}
                    >
                      {saving === item.id ? "保存中…" : "保存"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </article>

      {Object.keys(overview?.units ?? {}).length > 0 ? (
        <article className="panel">
          <div className="panel-head">
            <h2>systemd 单元</h2>
            <small>真实 MainPID 与重启次数</small>
          </div>
          <div className="service-list">
            {Object.entries(overview?.units ?? {}).map(([unit, value]) => (
              <div key={unit} className="service-row" data-ok={value.active_state === "active"}>
                <span className={`status-dot ${value.active_state === "active" ? "" : "bad"}`} />
                <div>
                  <strong>{unit}</strong>
                  <small>
                    MainPID {String(value.main_pid ?? "—")} · 重启{" "}
                    {String(value.n_restarts ?? "—")} 次
                  </small>
                </div>
                <span className="service-badge">{String(value.active_state ?? "未知")}</span>
              </div>
            ))}
          </div>
        </article>
      ) : null}
    </section>
  );
}

function serviceRows(overview: Overview | null) {
  const services = overview?.services;
  return [
    { key: "control", name: "控制面", state: services?.control },
    { key: "model", name: "主模型", state: services?.model },
    { key: "bge", name: "BGE-M3 文本嵌入", state: services?.bge },
    { key: "clip", name: "Chinese-CLIP 视觉嵌入", state: services?.clip },
    ...Object.entries(services?.users ?? {}).map(([account, state]) => ({
      key: `user-${account}`,
      name: `用户 API · ${account}`,
      state,
    })),
  ];
}

function serviceDetail(state: ServiceState | undefined): string {
  if (state === undefined) return "尚未采集";
  if (state.ok === true) return `HTTP ${state.status_code ?? 200}`;
  if (state.error !== undefined) return state.error.slice(0, 120);
  return `HTTP ${state.status_code ?? "?"}`;
}

function sum(values: Record<string, number> | undefined): number {
  return Object.values(values ?? {}).reduce((total, value) => total + value, 0);
}

function formatTime(value: string): string {
  try {
    return new Date(value).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return value;
  }
}

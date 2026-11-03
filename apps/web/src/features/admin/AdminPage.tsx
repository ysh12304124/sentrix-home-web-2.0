import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "../../app/AuthProvider";
import { csrfToken } from "../../shared/csrf";
import { newClientId } from "../../shared/id";

interface JobStage {
  stage: string;
  status: string;
  reason: string;
  attempts: number;
  error_category: string;
  updated_at: string;
}

interface JobInfo {
  job_id: string;
  asset_id: string;
  scope_id: string;
  file_name: string;
  media_type: string;
  size_bytes: number;
  status: string;
  stage: string;
  attempts: number;
  accepted_at: string;
  updated_at: string;
  next_retry_at: string | null;
  error_category: string;
  error_message: string;
  progress?: { done: number; total: number };
  stages?: JobStage[];
}

interface TurnInfo {
  turn_id: string;
  conversation_id: string;
  scope_id: string;
  status: string;
  execution_state: string;
  attempt_no: number;
  cancel_requested: boolean;
  waiting_reason: string;
  next_retry_at: string | null;
  created_at: string;
  updated_at: string;
}

interface UserStatus {
  jobs?: Record<string, number>;
  job_total?: number;
  jobs_active?: number;
  oldest_active_job?: string | null;
  active_jobs?: JobInfo[];
  recent_jobs?: JobInfo[];
  postprocess_backlog?: number;
  turns?: Record<string, number>;
  turns_active?: number;
  oldest_active_turn?: string | null;
  active_turns?: TurnInfo[];
  recent_turns?: TurnInfo[];
  assets?: number;
  embeddings?: boolean;
  build_revision?: string;
}

interface ServiceState {
  ok?: boolean;
  status_code?: number;
  error?: string;
  api_url?: string;
  encoding?: { ok?: boolean; dimension?: number; expected?: number; error?: string };
  inference?: { ok?: boolean; skipped?: string; error?: string };
  status?: UserStatus;
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
  build_sha?: string;
  frontend_build?: string;
  shared_model_units?: string[];
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
    waiting_total?: number;
    active_by_user?: Record<string, number>;
    waiting_by_user?: Record<string, Record<string, number>>;
    oldest_wait_s?: number;
    last_ok_at?: number | null;
  };
  model_queue_sampled_at?: string;
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

interface FeedbackAttachment {
  id: string;
  content_type: string;
  byte_size: number;
  created_at: string;
}

interface FeedbackItem {
  id: string;
  account_id: string;
  kind: string;
  body: string;
  route: string;
  scope_id: string;
  job_id: string;
  turn_id: string;
  event_id: string;
  asset_id: string;
  request_id: string;
  status: string;
  reply: string;
  assignee: string;
  severity: string;
  internal_note: string;
  created_at: string;
  attachments?: FeedbackAttachment[];
}

interface TrendPoint {
  sampled_at: string;
  payload: {
    host?: {
      cpu_percent?: number | null;
      ram_used_gb?: number | null;
      disk_free_gb?: number | null;
      load_1m?: number | null;
      gpu?: {
        utilization_percent?: number | null;
        memory_used_mib?: number | null;
        temperature_c?: number | null;
      };
    };
    queue?: { active?: number; waiting_total?: number; oldest_wait_s?: number };
  };
}

interface FeedbackFilters {
  account: string;
  kind: string;
  status: string;
  related: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: FeedbackFilters = {
  account: "",
  kind: "",
  status: "",
  related: "",
  since: "",
  until: "",
};

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

const JOB_STATUS_LABELS: Record<string, string> = {
  accepted: "已接收",
  queued: "排队",
  running: "处理中",
  waiting_dependency: "等待依赖",
  retry_scheduled: "等待重试",
  completed: "完成",
  partial: "部分完成",
  cancelled: "已取消",
  needs_attention: "需处理",
};

const STAGE_LABELS: Record<string, string> = {
  asset_staged: "原件落盘",
  prepared: "元数据/预览",
  visual_indexed: "视觉索引",
  observation_saved: "观察",
  faces_saved: "人脸",
  event_assigned: "事件归属",
  text_indexed: "文本索引",
  entities_saved: "实体",
  facts_saved: "事实",
  lexical_indexed: "词法索引",
  video_scenes_done: "视频场景",
  postprocess: "相册后处理",
  completed: "完成",
  cancelled: "已取消",
};

const TURN_STATUS_LABELS: Record<string, string> = {
  pending: "排队",
  running: "运行中",
  complete: "完成",
  partial: "部分完成",
  failed: "失败",
  blocked: "阻断",
  cancelled: "已取消",
};

export function AdminPage() {
  const { identity } = useAuth();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [problemJobs, setProblemJobs] = useState<AdminJob[]>([]);
  const [filterDraft, setFilterDraft] = useState<FeedbackFilters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<FeedbackFilters>(EMPTY_FILTERS);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<Record<string, { status: string; reply: string }>>({});
  const [saving, setSaving] = useState("");
  const [restarting, setRestarting] = useState("");
  // 一次重启意图只生成一枚键；响应丢失后重试仍是同一操作，不会再次重启。
  const restartKeys = useRef<Record<string, string>>({});

  const feedbackQuery = useMemo(() => {
    const params = new URLSearchParams();
    if (filters.account.trim()) params.set("account_id", filters.account.trim());
    if (filters.kind) params.set("kind", filters.kind);
    if (filters.status) params.set("status", filters.status);
    if (filters.related.trim()) params.set("related", filters.related.trim());
    if (filters.since) params.set("since", new Date(filters.since).toISOString());
    if (filters.until) params.set("until", new Date(filters.until).toISOString());
    return params.toString();
  }, [filters]);

  const load = useCallback(async () => {
    try {
      const [overviewResponse, incidentsResponse, feedbackResponse, trendResponse] =
        await Promise.all([
          fetch("/control/v1/admin/overview", { credentials: "same-origin" }),
          fetch("/control/v1/admin/incidents?limit=30", { credentials: "same-origin" }),
          fetch(`/control/v1/admin/feedback${feedbackQuery ? `?${feedbackQuery}` : ""}`, {
            credentials: "same-origin",
          }),
          fetch("/control/v1/admin/trends?minutes=60", { credentials: "same-origin" }),
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
      setTrends(
        trendResponse.ok
          ? ((await trendResponse.json()) as { items: TrendPoint[] }).items
          : [],
      );
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [feedbackQuery]);

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

  const restartUnit = useCallback(
    async (unit: string) => {
      const shared = (overview?.shared_model_units ?? []).includes(unit);
      if (
        shared &&
        !window.confirm(
          `${unit} 是共享模型单元，重启会中断所有用户正在进行的问答与导入，确定继续？`,
        )
      ) {
        return;
      }
      setRestarting(unit);
      setError("");
      const key = restartKeys.current[unit] ?? newClientId("restart");
      restartKeys.current[unit] = key;
      try {
        const response = await fetch(
          `/control/v1/admin/services/${encodeURIComponent(unit)}/restart`,
          {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "x-csrf-token": csrfToken(),
              "Idempotency-Key": key,
            },
          },
        );
        // 服务端已经给出结论：这次意图结束。只有网络异常（响应没回来）才保留键，
        // 让用户的“再试一次”落到同一次操作上。
        delete restartKeys.current[unit];
        const body = (await response.json().catch(() => ({}))) as { detail?: string };
        if (!response.ok) {
          throw new Error(body.detail ?? `重启失败（${response.status}）`);
        }
        await load();
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setRestarting("");
      }
    },
    [load, overview?.shared_model_units],
  );

  const rows = useMemo(() => serviceRows(overview), [overview]);
  const okCount = rows.filter((row) => row.state?.ok === true).length;
  const waiting = overview?.model_queue?.waiting ?? {};
  const queued = Object.values(waiting).reduce((total, value) => total + value, 0);
  const users = Object.entries(overview?.services?.users ?? {});
  const activeJobsTotal = users.reduce(
    (total, [, state]) => total + (state.status?.jobs_active ?? activeJobCount(state.status)),
    0,
  );
  const activeTurnsTotal = users.reduce(
    (total, [, state]) => total + (state.status?.turns_active ?? activeTurnCount(state.status)),
    0,
  );
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
            每 10 秒刷新 · 控制面 SHA {overview?.build_sha ?? "—"} · 前端{" "}
            {overview?.frontend_build ?? "—"}
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
          <div className="kpi-note">服务中断、磁盘、排队与传感器</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-label">在飞任务</div>
          <div className="kpi-value">{activeJobsTotal} / {activeTurnsTotal}</div>
          <div className="kpi-note">导入作业 / 问答回合</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-label">模型槽位</div>
          <div className="kpi-value">
            {overview?.model_queue?.active ?? 0} / {overview?.model_queue?.slots ?? "—"}
          </div>
          <div className="kpi-note">
            等待 {queued} 个请求
            {overview?.model_queue?.oldest_wait_s
              ? ` · 最久 ${Math.round(overview.model_queue.oldest_wait_s)} 秒`
              : ""}
          </div>
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

      <article className="panel">
        <div className="panel-head">
          <h2>60 分钟趋势</h2>
          <small>按分钟聚合；资源取均值，队列取峰值</small>
        </div>
        {trends.length === 0 ? (
          <div className="empty-panel">趋势数据积累中，约一分钟后出现第一个点。</div>
        ) : (
          <div className="trend-grid">
            <TrendCard
              label="CPU"
              unit="%"
              points={trends.map((item) => item.payload.host?.cpu_percent ?? null)}
            />
            <TrendCard
              label="内存"
              unit="GiB"
              points={trends.map((item) => item.payload.host?.ram_used_gb ?? null)}
            />
            <TrendCard
              label="GPU"
              unit="%"
              points={trends.map(
                (item) => item.payload.host?.gpu?.utilization_percent ?? null,
              )}
            />
            <TrendCard
              label="磁盘可用"
              unit="GiB"
              points={trends.map((item) => item.payload.host?.disk_free_gb ?? null)}
            />
            <TrendCard
              label="排队请求"
              unit="个"
              points={trends.map((item) => item.payload.queue?.waiting_total ?? null)}
            />
            <TrendCard
              label="最久排队"
              unit="秒"
              points={trends.map((item) => item.payload.queue?.oldest_wait_s ?? null)}
            />
          </div>
        )}
      </article>

      <div className="panel-head" style={{ marginTop: 22 }}>
        <h2>按用户运行</h2>
        <small>只展示运行元数据，不读取问答正文与媒体内容</small>
      </div>
      <div className="user-grid">
        {users.length === 0 ? (
          <div className="empty-panel">路由表里没有用户 API。</div>
        ) : (
          users.map(([account, state]) => (
            <UserRunCard
              key={account}
              account={account}
              state={state}
              modelQueue={overview?.model_queue}
            />
          ))
        )}
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
            <span className="queue-chip">
              最久等待 <b>{Math.round(overview?.model_queue?.oldest_wait_s ?? 0)}</b> 秒
            </span>
          </div>
          <p style={{ margin: "14px 0 0", color: "var(--ex-muted)", fontSize: 11.5 }}>
            上游 {overview?.model_queue?.upstream ?? "—"} · 槽位 {overview?.model_queue?.slots ?? "—"}
            {overview?.model_queue?.last_ok_at
              ? ` · 最近成功 ${formatTime(
                  new Date(overview.model_queue.last_ok_at * 1000).toISOString(),
                )}`
              : ""}
            {overview?.model_queue_sampled_at
              ? ` · 队列采样 ${formatTime(overview.model_queue_sampled_at)}`
              : ""}
          </p>
          <div className="service-list" style={{ marginTop: 14 }}>
            {users.map(([account, state]) => (
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
                    {job.account} · {JOB_STATUS_LABELS[job.status] ?? job.status} ·{" "}
                    {STAGE_LABELS[job.stage] ?? job.stage ?? "—"} · 第 {job.attempts} 次
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
          <small>按账号、类型、状态、时间和关联 ID 筛选</small>
        </div>
        <div className="feedback-filters">
          <input
            placeholder="账号"
            value={filterDraft.account}
            onChange={(event) =>
              setFilterDraft((previous) => ({ ...previous, account: event.target.value }))
            }
          />
          <select
            value={filterDraft.kind}
            onChange={(event) =>
              setFilterDraft((previous) => ({ ...previous, kind: event.target.value }))
            }
          >
            <option value="">全部类型</option>
            {Object.entries(KIND_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select
            value={filterDraft.status}
            onChange={(event) =>
              setFilterDraft((previous) => ({ ...previous, status: event.target.value }))
            }
          >
            <option value="">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <input
            placeholder="关联 ID（作业/回合/事件/资产/请求）"
            value={filterDraft.related}
            onChange={(event) =>
              setFilterDraft((previous) => ({ ...previous, related: event.target.value }))
            }
          />
          <label>
            从
            <input
              type="datetime-local"
              value={filterDraft.since}
              onChange={(event) =>
                setFilterDraft((previous) => ({ ...previous, since: event.target.value }))
              }
            />
          </label>
          <label>
            到
            <input
              type="datetime-local"
              value={filterDraft.until}
              onChange={(event) =>
                setFilterDraft((previous) => ({ ...previous, until: event.target.value }))
              }
            />
          </label>
          <button type="button" className="ghost-action" onClick={() => setFilters(filterDraft)}>
            筛选
          </button>
          <button
            type="button"
            className="ghost-action"
            onClick={() => {
              setFilterDraft(EMPTY_FILTERS);
              setFilters(EMPTY_FILTERS);
            }}
          >
            清空
          </button>
        </div>
        {feedback.length === 0 ? (
          <div className="empty-panel">当前筛选下没有反馈。</div>
        ) : (
          <div className="feedback-admin-list">
            {feedback.map((item) => {
              const value = draft[item.id] ?? { status: item.status, reply: item.reply };
              const related = [
                item.job_id ? `job ${item.job_id}` : "",
                item.turn_id ? `turn ${item.turn_id}` : "",
                item.event_id ? `event ${item.event_id}` : "",
                item.asset_id ? `asset ${item.asset_id}` : "",
                item.request_id ? `req ${item.request_id}` : "",
              ].filter(Boolean);
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
                  {item.route ? <small className="feedback-meta">来源 {item.route}</small> : null}
                  {related.length > 0 ? (
                    <small className="feedback-meta">关联 {related.join(" · ")}</small>
                  ) : null}
                  {item.attachments !== undefined && item.attachments.length > 0 ? (
                    <div className="feedback-attachments">
                      {item.attachments.map((attachment) => (
                        <a
                          key={attachment.id}
                          href={`/control/v1/admin/feedback/attachments/${attachment.id}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看截图（{(attachment.byte_size / 1024).toFixed(0)} KiB）
                        </a>
                      ))}
                    </div>
                  ) : null}
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
            <small>真实 MainPID 与重启次数；重启只接受固定单元</small>
          </div>
          <div className="service-list">
            {Object.entries(overview?.units ?? {}).map(([unit, value]) => {
              const shared = (overview?.shared_model_units ?? []).includes(unit);
              return (
                <div key={unit} className="service-row" data-ok={value.active_state === "active"}>
                  <span className={`status-dot ${value.active_state === "active" ? "" : "bad"}`} />
                  <div>
                    <strong>{unit}</strong>
                    <small>
                      MainPID {String(value.main_pid ?? "—")} · 重启{" "}
                      {String(value.n_restarts ?? "—")} 次
                      {shared ? " · 共享模型，影响全部用户" : ""}
                    </small>
                  </div>
                  <button
                    type="button"
                    className="ghost-action"
                    disabled={restarting === unit}
                    onClick={() => void restartUnit(unit)}
                  >
                    {restarting === unit ? "重启中…" : "重启"}
                  </button>
                  <span className="service-badge">{String(value.active_state ?? "未知")}</span>
                </div>
              );
            })}
          </div>
        </article>
      ) : null}
    </section>
  );
}

function UserRunCard({
  account,
  state,
  modelQueue,
}: {
  account: string;
  state: ServiceState;
  modelQueue?: Overview["model_queue"];
}) {
  const status = state.status;
  const activeJobs = status?.active_jobs ?? [];
  const activeTurns = status?.active_turns ?? [];
  const recentJobs = status?.recent_jobs ?? [];
  const recentTurns = status?.recent_turns ?? [];
  return (
    <article className="panel user-panel">
      <div className="panel-head">
        <h2>{account}</h2>
        <small>{state.api_url ?? "未配置地址"}</small>
      </div>
      <div className="queue-chips">
        <span className="queue-chip">
          状态 <b>{state.ok === true ? "在线" : "离线"}</b>
        </span>
        <span className="queue-chip">
          版本 <b>{status?.build_revision ?? "—"}</b>
        </span>
        <span className="queue-chip">
          资产 <b>{status?.assets ?? 0}</b>
        </span>
        <span className="queue-chip">
          在飞作业 <b>{status?.jobs_active ?? activeJobs.length}</b>
        </span>
        <span className="queue-chip">
          在飞问答 <b>{status?.turns_active ?? activeTurns.length}</b>
        </span>
        <span className="queue-chip">
          后处理待办 <b>{status?.postprocess_backlog ?? 0}</b>
        </span>
        <span className="queue-chip">
          模型执行 <b>{modelQueue?.active_by_user?.[account] ?? 0}</b>
        </span>
        <span className="queue-chip">
          模型排队 <b>{sum(modelQueue?.waiting_by_user?.[account])}</b>
        </span>
        <span className="queue-chip">
          最老作业活动 <b>{formatAge(status?.oldest_active_job)}</b>
        </span>
        <span className="queue-chip">
          最老回合活动 <b>{formatAge(status?.oldest_active_turn)}</b>
        </span>
      </div>

      <div className="queue-chips" style={{ marginTop: 8 }}>
        {Object.entries(status?.jobs ?? {})
          .filter(([, count]) => count > 0)
          .map(([name, count]) => (
            <span className="queue-chip" key={`job-${name}`}>
              {JOB_STATUS_LABELS[name] ?? name} <b>{count}</b>
            </span>
          ))}
        {Object.entries(status?.turns ?? {})
          .filter(([, count]) => count > 0)
          .map(([name, count]) => (
            <span className="queue-chip" key={`turn-${name}`}>
              问答 {TURN_STATUS_LABELS[name] ?? name} <b>{count}</b>
            </span>
          ))}
      </div>

      <div className="user-section">
        <h3>在飞导入</h3>
        {activeJobs.length === 0 ? (
          <div className="empty-panel">没有正在处理的导入。</div>
        ) : (
          activeJobs.map((job) => {
            const done = job.progress?.done ?? 0;
            const total = job.progress?.total ?? 11;
            const percent = total > 0 ? Math.min(100, (done / total) * 100) : 0;
            const current = job.stages?.find((stage) => stage.stage === job.stage);
            return (
              <div className="flow-row" key={job.job_id}>
                <div className="flow-head">
                  <strong>{job.file_name}</strong>
                  <span>
                    {JOB_STATUS_LABELS[job.status] ?? job.status} ·{" "}
                    {STAGE_LABELS[job.stage] ?? job.stage ?? "—"} · 第 {job.attempts} 次
                  </span>
                </div>
                <div className="meter-track">
                  <div className="meter-fill" style={{ width: `${percent}%` }} />
                </div>
                <small>
                  {done}/{total} 阶段 · 更新于 {formatAge(job.updated_at)}
                  {job.next_retry_at ? ` · 下次重试 ${formatTime(job.next_retry_at)}` : ""}
                </small>
                {current?.reason ? (
                  <small className="error-line">{current.reason}</small>
                ) : null}
                {job.error_message ? (
                  <small className="error-line">{job.error_message.slice(0, 160)}</small>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      <div className="user-section">
        <h3>在飞问答</h3>
        {activeTurns.length === 0 ? (
          <div className="empty-panel">没有正在执行的问答。</div>
        ) : (
          activeTurns.map((turn) => (
            <div className="flow-row" key={turn.turn_id}>
              <div className="flow-head">
                <strong>{shortId(turn.turn_id)}</strong>
                <span>
                  {TURN_STATUS_LABELS[turn.status] ?? turn.status}
                  {turn.execution_state ? ` · ${turn.execution_state}` : ""} · 第{" "}
                  {turn.attempt_no} 次尝试
                </span>
              </div>
              <small>
                更新于 {formatAge(turn.updated_at)} · 已运行{" "}
                {formatDuration(turn.created_at, turn.updated_at)}
                {turn.cancel_requested ? " · 用户已请求停止" : ""}
                {turn.waiting_reason ? ` · ${turn.waiting_reason}` : ""}
              </small>
            </div>
          ))
        )}
      </div>

      <details className="user-details">
        <summary>最近作业与回合</summary>
        <div className="user-section">
          <h3>最近作业</h3>
          {recentJobs.length === 0 ? (
            <div className="empty-panel">暂无。</div>
          ) : (
            recentJobs.slice(0, 6).map((job) => (
              <div className="flow-row compact" key={job.job_id}>
                <div className="flow-head">
                  <strong>{job.file_name}</strong>
                  <span>{JOB_STATUS_LABELS[job.status] ?? job.status}</span>
                </div>
                <small>
                  {STAGE_LABELS[job.stage] ?? job.stage} · 更新于 {formatAge(job.updated_at)}
                  {job.error_message ? ` · ${job.error_message.slice(0, 100)}` : ""}
                </small>
              </div>
            ))
          )}
        </div>
        <div className="user-section">
          <h3>最近回合</h3>
          {recentTurns.length === 0 ? (
            <div className="empty-panel">暂无。</div>
          ) : (
            recentTurns.slice(0, 6).map((turn) => (
              <div className="flow-row compact" key={turn.turn_id}>
                <div className="flow-head">
                  <strong>{shortId(turn.turn_id)}</strong>
                  <span>
                    {TURN_STATUS_LABELS[turn.status] ?? turn.status}
                    {turn.execution_state ? ` · ${turn.execution_state}` : ""}
                  </span>
                </div>
                <small>
                  第 {turn.attempt_no} 次 · 更新于 {formatAge(turn.updated_at)} · 耗时{" "}
                  {formatDuration(turn.created_at, turn.updated_at)}
                </small>
              </div>
            ))
          )}
        </div>
      </details>
    </article>
  );
}

function TrendCard({ label, unit, points }: { label: string; unit: string; points: (number | null)[] }) {
  const values = points.filter((value): value is number => typeof value === "number");
  const latest = values.length > 0 ? values[values.length - 1] : null;
  return (
    <div className="trend-card">
      <div className="trend-head">
        <span>{label}</span>
        <b>{latest === null ? "—" : `${Math.round(latest * 10) / 10} ${unit}`}</b>
      </div>
      <Sparkline values={values} />
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) {
    return <div className="sparkline-empty">积累中</div>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 160;
  const height = 38;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - ((value - min) / span) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
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
  if (state.ok !== true) {
    return state.error !== undefined ? state.error.slice(0, 120) : `HTTP ${state.status_code ?? "?"}`;
  }
  const parts = [`HTTP ${state.status_code ?? 200}`];
  if (state.encoding !== undefined) {
    parts.push(
      `编码 ${state.encoding.dimension ?? "?"}d / 期望 ${state.encoding.expected ?? "?"}d${
        state.encoding.ok === false ? "（异常）" : ""
      }`,
    );
  }
  if (state.inference !== undefined) {
    parts.push(
      state.inference.skipped === "busy"
        ? "推理探针跳过高负载"
        : state.inference.ok === true
          ? "真实推理正常"
          : "真实推理失败",
    );
  }
  return parts.join(" · ");
}

function sum(values: Record<string, number> | undefined): number {
  return Object.values(values ?? {}).reduce((total, value) => total + value, 0);
}

function activeJobCount(status: UserStatus | undefined): number {
  const jobs = status?.jobs ?? {};
  return (
    (jobs.accepted ?? 0) +
    (jobs.queued ?? 0) +
    (jobs.running ?? 0) +
    (jobs.waiting_dependency ?? 0) +
    (jobs.retry_scheduled ?? 0)
  );
}

function activeTurnCount(status: UserStatus | undefined): number {
  const turns = status?.turns ?? {};
  return (turns.pending ?? 0) + (turns.running ?? 0);
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function formatAge(value: string | null | undefined): string {
  if (!value) return "—";
  const stamp = new Date(value).getTime();
  if (Number.isNaN(stamp)) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  return `${Math.round(seconds / 3600)} 小时前`;
}

function formatDuration(start: string, end: string): string {
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "—";
  const seconds = Math.max(0, Math.round((endMs - startMs) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

function formatTime(value: string): string {
  try {
    return new Date(value).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return value;
  }
}

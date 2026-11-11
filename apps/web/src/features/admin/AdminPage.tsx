import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "../../app/AuthProvider";
import { csrfToken } from "../../shared/csrf";
import { newClientId } from "../../shared/id";
import {
  listClientEvents,
  type ClientEventSummaryItem,
  type ClientEventView,
} from "../../shared/api/client";

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
  last_client_event?: { id: number; received_at: string; event_name: string };
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
    active_by_user_kind?: Record<string, number>;
    waiting_by_user?: Record<string, Record<string, number>>;
    budgets?: Record<string, number>;
    fair_caps?: Record<string, number>;
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

interface ActivityUser {
  accountId: string;
  name: string;
  clicks: number;
  uploads: number;
  questions: number;
  total: number;
}

interface ActivityGroup {
  name: string;
  users: ActivityUser[];
}

function activityGroups(
  items: readonly ClientEventSummaryItem[],
  users: Record<string, ServiceState>,
): ActivityGroup[] {
  const byGroup = new Map<string, ActivityUser[]>();
  for (const item of items) {
    if (ACTIVITY_EXCLUDED_ACCOUNTS.has(item.account_id)) continue;
    const status = users[item.account_id]?.status;
    // 上传/问答优先取服务端直接查用户库的累计完成数；按需模式下 API 停着也有数。
    const uploads = item.completed_uploads ?? status?.jobs?.completed ?? 0;
    // partial 表示这次问答已经产生结果但只完成了一部分，仍计入已完成的问答。
    const questions =
      item.completed_turns ??
      ((status?.turns?.complete ?? 0) + (status?.turns?.partial ?? 0));
    // 之前用「全部事件 − 上传 − 提问」，结果 page.view、上传生命周期和
    // assistant.accepted/complete 都被算成点击。点击只取明确的用户动作事件。
    const clicks = [...CLICK_EVENT_NAMES].reduce(
      (sum, eventName) => sum + (item.events[eventName] ?? 0),
      0,
    );
    const user: ActivityUser = {
      accountId: item.account_id,
      name: (item.display_name ?? "").trim() || item.account_id,
      clicks,
      uploads,
      questions,
      total: clicks + uploads + questions,
    };
    const groupName = (item.group_name ?? "").trim() || "项目组";
    const group = byGroup.get(groupName) ?? [];
    group.push(user);
    byGroup.set(groupName, group);
  }
  const rank = (name: string): number => {
    const index = ACTIVITY_GROUP_ORDER.indexOf(name);
    return index < 0 ? ACTIVITY_GROUP_ORDER.length : index;
  };
  return [...byGroup.entries()]
    .map(([name, users]) => ({
      name,
      users: [...users].sort(
        (left, right) =>
          right.total - left.total || left.name.localeCompare(right.name, "zh-CN"),
      ),
    }))
    .sort(
      (left, right) =>
        rank(left.name) - rank(right.name) ||
        left.name.localeCompare(right.name, "zh-CN"),
    );
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

const EVENT_LABELS: Record<string, string> = {
  "page.view": "打开页面",
  "nav.click": "切换导航",
  "scope.change": "切换相册",
  "feedback.open": "打开反馈",
  "auth.logout": "退出登录",
  "onboarding.open": "打开引导",
  "onboarding.skip": "跳过引导",
  "onboarding.complete": "完成引导",
  "help.open": "打开帮助",
  "memory.next": "下一段回忆",
  "memory.prev": "上一段回忆",
  "memory.pause": "暂停回忆",
  "memory.resume": "继续回忆",
  "memory.video_play": "播放回忆视频",
  "memory.media_error": "回忆素材失败",
  "import.pick": "选择资料",
  "import.upload_start": "开始上传",
  "import.upload_accepted": "上传已接收",
  "import.upload_failed": "上传失败",
  "import.cancel": "取消导入",
  "assistant.ask": "提交问题",
  "assistant.accepted": "问题已接收",
  "assistant.stop": "停止回答",
  "assistant.complete": "回答结束",
  "people.view": "查看人物",
  "people.detail_open": "打开人物详情",
  "people.confirm_submit": "提交人物确认",
  "people.confirm_result": "人物确认结果",
  "people.filter": "筛选人物",
  "library.view": "查看资料库",
  "library.detail_open": "打开资料详情",
  "library.filter": "筛选资料",
  "library.media_error": "资料预览失败",
  "timeline.month": "切换月份",
  "timeline.date": "展开日期",
  "timeline.event_open": "打开事件",
  "timeline.media_error": "时间线素材失败",
};

const CLIENT_RESULT_LABELS: Record<string, string> = {
  started: "已开始",
  accepted: "已接收",
  ok: "完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  rejected: "被拒绝",
  ignored: "已忽略",
};

const CLIENT_ERROR_LABELS: Record<string, string> = {
  network: "网络不可用",
  timeout: "超时",
  http_4xx: "请求被拒绝",
  http_5xx: "服务异常",
  media_load: "素材加载失败",
  video_play: "视频无法播放",
  unknown: "未知原因",
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

const RECENT_USER_LIMIT = 5;
const FEEDBACK_PAGE_SIZE = 10;
// 测试账号的历史数据量远大于真实用户，会把项目组的柱形图压扁；只从使用统计里排除，
// 按用户运行/监控仍然保留，方便继续排查。
const ACTIVITY_EXCLUDED_ACCOUNTS = new Set(["alice", "bob"]);
const ACTIVITY_GROUP_ORDER = ["项目组", "505实验室", "体验用户"];
// 真正的用户点击：导航、打开详情/筛选、时间线/回忆操作、反馈/帮助/引导、退出。
// 不包含 page.view（自动）、import.*（上传列已统计）、assistant.*（问数列已统计）
// 和 *_error（自动失败事件）。
const CLICK_EVENT_NAMES = new Set([
  "nav.click",
  "scope.change",
  "people.view",
  "people.detail_open",
  "people.filter",
  "people.confirm_submit",
  "library.view",
  "library.detail_open",
  "library.filter",
  "timeline.month",
  "timeline.date",
  "timeline.event_open",
  "memory.next",
  "memory.prev",
  "memory.pause",
  "memory.resume",
  "memory.video_play",
  "feedback.open",
  "help.open",
  "onboarding.open",
  "onboarding.skip",
  "onboarding.complete",
  "auth.logout",
]);

export function AdminPage() {
  const { identity } = useAuth();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [eventSummary, setEventSummary] = useState<ClientEventSummaryItem[]>([]);
  const [problemJobs, setProblemJobs] = useState<AdminJob[]>([]);
  const [filterDraft, setFilterDraft] = useState<FeedbackFilters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<FeedbackFilters>(EMPTY_FILTERS);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<Record<string, { status: string; reply: string }>>({});
  const [saving, setSaving] = useState("");
  const [restarting, setRestarting] = useState("");
  // 一次重启意图只生成一枚键；响应丢失后重试仍是同一操作，不会再次重启。
  const restartKeys = useRef<Record<string, string>>({});
  // 展开状态独立于 10 秒总览刷新；默认全部收起，顺序按账号稳定排序。
  const [expandedUsers, setExpandedUsers] = useState<Set<string>>(new Set());
  const [showAllUsers, setShowAllUsers] = useState(false);
  const [feedbackPage, setFeedbackPage] = useState(1);
  const [focusedRunId, setFocusedRunId] = useState("");

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
      const [overviewResponse, incidentsResponse, feedbackResponse, trendResponse, eventSummaryResponse] =
        await Promise.all([
          fetch("/control/v1/admin/overview", { credentials: "same-origin" }),
          fetch("/control/v1/admin/incidents?limit=30", { credentials: "same-origin" }),
          fetch(`/control/v1/admin/feedback${feedbackQuery ? `?${feedbackQuery}` : ""}`, {
            credentials: "same-origin",
          }),
          fetch("/control/v1/admin/trends?minutes=60", { credentials: "same-origin" }),
          fetch("/control/v1/admin/client-events/summary?all_time=true", {
            credentials: "same-origin",
          }),
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
      setEventSummary(
        eventSummaryResponse.ok
          ? ((await eventSummaryResponse.json()) as { items: ClientEventSummaryItem[] }).items
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
  // 「最近使用」取控制库记录的最后一次点击流时间；没有记录的用户排在末尾。
  const users = useMemo(
    () =>
      Object.entries(overview?.services?.users ?? {}).sort(
        ([leftAccount, left], [rightAccount, right]) => {
          const leftTime = left.last_client_event?.received_at ?? "";
          const rightTime = right.last_client_event?.received_at ?? "";
          if (leftTime !== rightTime) {
            if (leftTime === "") return 1;
            if (rightTime === "") return -1;
            return rightTime.localeCompare(leftTime);
          }
          return leftAccount.localeCompare(rightAccount);
        },
      ),
    [overview],
  );
  const foldableUserCount = Math.max(0, users.length - RECENT_USER_LIMIT);
  const visibleUsers = showAllUsers ? users : users.slice(0, RECENT_USER_LIMIT);
  const hiddenUserCount = users.length - visibleUsers.length;
  const feedbackPageCount = Math.max(1, Math.ceil(feedback.length / FEEDBACK_PAGE_SIZE));
  const currentFeedbackPage = Math.min(feedbackPage, feedbackPageCount);
  const pagedFeedback = feedback.slice(
    (currentFeedbackPage - 1) * FEEDBACK_PAGE_SIZE,
    currentFeedbackPage * FEEDBACK_PAGE_SIZE,
  );
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
  const activity = useMemo(
    () => activityGroups(eventSummary, overview?.services?.users ?? {}),
    [eventSummary, overview?.services?.users],
  );
  const activityMax = useMemo(
    () =>
      activity.reduce(
        (maximum, group) =>
          group.users.reduce((groupMaximum, user) => Math.max(groupMaximum, user.total), maximum),
        1,
      ),
    [activity],
  );
  const hasActivity = useMemo(
    () => activity.some((group) => group.users.some((user) => user.total > 0)),
    [activity],
  );

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
            <small>共享服务按真实端点探活；用户 API 不在此探测</small>
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
        <small>
          共 {users.length} 个用户，默认显示最近使用的 {Math.min(RECENT_USER_LIMIT, users.length)} 个；
          展开用户可看运行详情与关键操作轨迹，不读取正文与媒体
        </small>
      </div>
      {users.length > 0 ? (
        <div className="admin-list-toolbar">
          <span>
            {showAllUsers
              ? `已展开全部 ${users.length} 个用户`
              : `按最近使用排序，默认显示最近 ${Math.min(RECENT_USER_LIMIT, users.length)} 个`}
          </span>
          {foldableUserCount > 0 ? (
            <button
              type="button"
              className="ghost-action"
              aria-expanded={showAllUsers}
              onClick={() => setShowAllUsers((value) => !value)}
            >
              {showAllUsers ? "收起其余用户" : `展开其余 ${foldableUserCount} 个用户`}
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="user-summary-list">
        {users.length === 0 ? (
          <div className="empty-panel">路由表里没有用户 API。</div>
        ) : (
          <>
            {visibleUsers.map(([account, state]) => (
              <UserSummary
                key={account}
                account={account}
                state={state}
                modelQueue={overview?.model_queue}
                expanded={expandedUsers.has(account)}
                onToggle={() =>
                  setExpandedUsers((previous) => {
                    const next = new Set(previous);
                    if (next.has(account)) next.delete(account);
                    else next.add(account);
                    return next;
                  })
                }
                focusedRunId={focusedRunId}
                onFocusRun={setFocusedRunId}
              />
            ))}
            {foldableUserCount > 0 ? (
              <button
                type="button"
                className="text-button admin-fold-toggle"
                aria-expanded={showAllUsers}
                onClick={() => setShowAllUsers((value) => !value)}
              >
                {showAllUsers ? "收起其余用户" : `展开其余 ${foldableUserCount} 个用户`}
              </button>
            ) : null}
          </>
        )}
      </div>

      <article className="panel">
        <div className="panel-head">
          <h2>用户使用统计</h2>
          <small>
            上传取已完成作业数，问答含部分完成；点击只计明确用户操作（点击流保留 7 天）
          </small>
        </div>
        {!hasActivity ? (
          <div className="empty-panel">还没有可统计的使用记录。</div>
        ) : (
          <ActivityChart groups={activity} maxTotal={activityMax} />
        )}
      </article>

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
            <span className="queue-chip">
              问答每用户上限 <b>{overview?.model_queue?.fair_caps?.chat ?? "—"}</b>
            </span>
            <span className="queue-chip">
              导入每用户上限 <b>{overview?.model_queue?.fair_caps?.import ?? "—"}</b>
            </span>
          </div>
          <p style={{ margin: "8px 0 0", color: "var(--ex-muted)", fontSize: 11 }}>
            按当前活跃/等待用户数动态均分；问答先占槽，剩余槽位再分给导入。
          </p>
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
          <div className="queue-chips" style={{ marginTop: 14 }}>
            {visibleUsers.map(([account]) => (
              <span key={account} className="queue-chip">
                {account} 执行 <b>{overview?.model_queue?.active_by_user?.[account] ?? 0}</b> ·
                排队 <b>{sum(overview?.model_queue?.waiting_by_user?.[account])}</b>
              </span>
            ))}
            {hiddenUserCount > 0 ? (
              <span className="queue-chip">其余 {hiddenUserCount} 个用户已折叠</span>
            ) : null}
          </div>
        </article>

        <article className="panel" id="admin-alerts">
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
          <small>
            按账号、类型、状态、时间和关联 ID 筛选；每页 {FEEDBACK_PAGE_SIZE} 条
          </small>
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
          <button
            type="button"
            className="ghost-action"
            onClick={() => {
              setFilters(filterDraft);
              setFeedbackPage(1);
            }}
          >
            筛选
          </button>
          <button
            type="button"
            className="ghost-action"
            onClick={() => {
              setFilterDraft(EMPTY_FILTERS);
              setFilters(EMPTY_FILTERS);
              setFeedbackPage(1);
            }}
          >
            清空
          </button>
        </div>
        {feedback.length === 0 ? (
          <div className="empty-panel">当前筛选下没有反馈。</div>
        ) : (
          <>
            <div className="feedback-admin-list">
              {pagedFeedback.map((item) => {
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
                          className="feedback-attachment"
                          title="打开原图"
                        >
                          <img
                            src={`/control/v1/admin/feedback/attachments/${attachment.id}`}
                            alt="反馈截图"
                            loading="lazy"
                          />
                          <small>{(attachment.byte_size / 1024).toFixed(0)} KiB</small>
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
            <div className="admin-pagination">
              <span>
                共 {feedback.length} 条 · 第 {currentFeedbackPage} / {feedbackPageCount} 页
              </span>
              {feedbackPageCount > 1 ? (
                <div>
                  <button
                    type="button"
                    className="ghost-action"
                    disabled={currentFeedbackPage <= 1}
                    onClick={() => setFeedbackPage(currentFeedbackPage - 1)}
                  >
                    上一页
                  </button>
                  <button
                    type="button"
                    className="ghost-action"
                    disabled={currentFeedbackPage >= feedbackPageCount}
                    onClick={() => setFeedbackPage(currentFeedbackPage + 1)}
                  >
                    下一页
                  </button>
                </div>
              ) : null}
            </div>
          </>
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

function ActivityChart({
  groups,
  maxTotal,
}: {
  groups: readonly ActivityGroup[];
  maxTotal: number;
}) {
  return (
    <div className="activity-wrap">
      <div className="activity-legend">
        <span><i className="legend-swatch clicks" />点击</span>
        <span><i className="legend-swatch uploads" />上传</span>
        <span><i className="legend-swatch questions" />问答</span>
      </div>
      {groups.map((group) => {
        return (
          <section key={group.name} className="activity-group">
            <div className="activity-group-head">
              <h3>{group.name}</h3>
              <small>{group.users.length} 人</small>
            </div>
            <div
              className="activity-list"
              role="img"
              aria-label={`${group.name}用户点击、上传和问答累计柱形图`}
            >
              {group.users.map((user) => {
                const width = user.total > 0 ? (user.total / maxTotal) * 100 : 0;
                return (
                  <div
                    key={user.accountId}
                    className="activity-row"
                    title={`${user.name}（${user.accountId}）：点击 ${user.clicks} · 上传 ${user.uploads} · 问答 ${user.questions}`}
                  >
                    <span className="activity-name" title={`${user.name}（${user.accountId}）`}>
                      {user.name}
                    </span>
                    <div className="activity-track">
                      {width > 0 ? (
                        <div
                          className="activity-bar"
                          style={{ width: `${Math.max(0.8, width)}%` }}
                        >
                          {user.clicks > 0 ? (
                            <span
                              className="activity-segment clicks"
                              style={{ flexGrow: user.clicks, flexBasis: 0 }}
                              title={`点击 ${user.clicks}`}
                            >
                              {user.clicks}
                            </span>
                          ) : null}
                          {user.uploads > 0 ? (
                            <span
                              className="activity-segment uploads"
                              style={{ flexGrow: user.uploads, flexBasis: 0 }}
                              title={`上传 ${user.uploads}`}
                            >
                              {user.uploads}
                            </span>
                          ) : null}
                          {user.questions > 0 ? (
                            <span
                              className="activity-segment questions"
                              style={{ flexGrow: user.questions, flexBasis: 0 }}
                              title={`问答 ${user.questions}`}
                            >
                              {user.questions}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                    <span className="activity-total">{user.total}</span>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function UserSummary({
  account,
  state,
  modelQueue,
  expanded,
  onToggle,
  focusedRunId,
  onFocusRun,
}: {
  account: string;
  state: ServiceState;
  modelQueue?: Overview["model_queue"];
  expanded: boolean;
  onToggle: () => void;
  focusedRunId: string;
  onFocusRun: (id: string) => void;
}) {
  const status = state.status;
  const activeJobs = status?.jobs_active ?? status?.active_jobs?.length ?? 0;
  const activeTurns = status?.turns_active ?? status?.active_turns?.length ?? 0;
  const needsAttention =
    (status?.jobs?.needs_attention ?? 0) + (status?.jobs?.partial ?? 0);
  // 按需模式下停掉的用户 API 会显示 ConnectError；这是“休眠”，不是故障。
  const asleep = state.ok !== true && state.error === "ConnectError";
  const abnormal = state.ok !== true && !asleep;
  const last = state.last_client_event;
  return (
    <article className="panel user-summary" data-abnormal={abnormal} id={`user-${account}`}>
      <button
        type="button"
        className="user-summary-toggle"
        aria-expanded={expanded}
        aria-controls={`user-detail-${account}`}
        onClick={onToggle}
      >
        <span className="summary-caret" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        <span className="summary-account">{account}</span>
        <span
          className="summary-state"
          data-ok={abnormal ? false : undefined}
          data-asleep={asleep}
        >
          {asleep ? "休眠" : abnormal ? "异常" : "在线"}
        </span>
        <span className="summary-metric">在飞导入 <b>{activeJobs}</b></span>
        <span className="summary-metric">在飞问答 <b>{activeTurns}</b></span>
        <span className="summary-metric" data-warn={needsAttention > 0}>
          需处理 <b>{needsAttention}</b>
        </span>
        <span className="summary-metric">
          最近操作 <b>{last ? formatAge(last.received_at) : "—"}</b>
        </span>
        <span className="summary-hint">{expanded ? "收起" : "展开"}</span>
      </button>
      {expanded ? (
        <div className="user-summary-detail" id={`user-detail-${account}`}>
          <UserRunDetail
            account={account}
            state={state}
            modelQueue={modelQueue}
            focusedRunId={focusedRunId}
          />
          <UserTrajectory account={account} onFocusRun={onFocusRun} />
        </div>
      ) : null}
    </article>
  );
}

function UserRunDetail({
  account,
  state,
  modelQueue,
  focusedRunId,
}: {
  account: string;
  state: ServiceState;
  modelQueue?: Overview["model_queue"];
  focusedRunId: string;
}) {
  const status = state.status;
  const activeJobs = status?.active_jobs ?? [];
  const activeTurns = status?.active_turns ?? [];
  const recentJobs = status?.recent_jobs ?? [];
  const recentTurns = status?.recent_turns ?? [];
  const [showAllJobs, setShowAllJobs] = useState(false);
  const focusedInJobs = activeJobs.some((job) => job.job_id === focusedRunId);
  const visibleJobs =
    showAllJobs || focusedInJobs ? activeJobs : activeJobs.slice(0, 3);

  useEffect(() => {
    if (focusedRunId === "") return;
    const target = document.getElementById(`run-${focusedRunId}`);
    target?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusedRunId]);

  return (
    <div className="user-run-detail">
      <p className="user-detail-meta">{state.api_url ?? "未配置地址"}</p>
      <div className="queue-chips">
        <span className="queue-chip">版本 <b>{status?.build_revision ?? "—"}</b></span>
        <span className="queue-chip">资产 <b>{status?.assets ?? 0}</b></span>
        <span className="queue-chip">后处理待办 <b>{status?.postprocess_backlog ?? 0}</b></span>
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
          <>
          {visibleJobs.map((job) => {
            const done = job.progress?.done ?? 0;
            const total = job.progress?.total ?? 11;
            const percent = total > 0 ? Math.min(100, (done / total) * 100) : 0;
            const current = job.stages?.find((stage) => stage.stage === job.stage);
            return (
              <div
                className="flow-row"
                key={job.job_id}
                id={`run-${job.job_id}`}
                data-focused={focusedRunId === job.job_id}
              >
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
          })}
          {activeJobs.length > 3 ? (
            <button
              type="button"
              className="text-button"
              aria-expanded={showAllJobs}
              onClick={() => setShowAllJobs((value) => !value)}
            >
              {showAllJobs ? "收起" : `展开全部 ${activeJobs.length} 条`}
            </button>
          ) : null}
          </>
        )}
      </div>

      <div className="user-section">
        <h3>在飞问答</h3>
        {activeTurns.length === 0 ? (
          <div className="empty-panel">没有正在执行的问答。</div>
        ) : (
          activeTurns.map((turn) => (
            <div
              className="flow-row"
              key={turn.turn_id}
              id={`run-${turn.turn_id}`}
              data-focused={focusedRunId === turn.turn_id}
            >
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
    </div>
  );
}

const TRAJECTORY_LIMIT = 300;
const POLL_INTERVAL_MS = 2000;

function UserTrajectory({
  account,
  onFocusRun,
}: {
  account: string;
  onFocusRun: (id: string) => void;
}) {
  const [items, setItems] = useState<ClientEventView[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [lastSuccess, setLastSuccess] = useState("");
  const [paused, setPaused] = useState(false);
  const cursorRef = useRef(0);
  const busyRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);

  const merge = useCallback((incoming: ClientEventView[]) => {
    if (incoming.length === 0) return;
    setItems((previous) => {
      const byId = new Map(previous.map((item) => [item.id, item]));
      for (const item of incoming) byId.set(item.id, item);
      // 最新的在最上面；“继续加载”拉回来的是更早的记录，自然接在底部。
      return [...byId.values()]
        .sort((a, b) => b.id - a.id)
        .slice(0, TRAJECTORY_LIMIT);
    });
  }, []);

  const pull = useCallback(
    async (initial = false) => {
      if (busyRef.current) return;
      busyRef.current = true;
      try {
        const page = await listClientEvents(account, initial ? 0 : cursorRef.current, initial ? 60 : 100);
        if (cancelledRef.current) return;
        merge(page.items);
        cursorRef.current = Math.max(cursorRef.current, page.next_cursor);
        setHasMore(page.has_more);
        setError("");
        setLastSuccess(new Date().toISOString());
      } catch {
        if (!cancelledRef.current) setError("连接中断，信息可能过期");
      } finally {
        busyRef.current = false;
      }
    },
    [account, merge],
  );

  useEffect(() => {
    cancelledRef.current = false;
    cursorRef.current = 0;
    setItems([]);
    setHasMore(false);
    setError("");
    setLastSuccess("");
    const stopTimer = () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
    const startTimer = () => {
      if (timerRef.current !== null) return;
      timerRef.current = window.setInterval(() => {
        if (document.visibilityState === "visible") void pull();
      }, POLL_INTERVAL_MS);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        setPaused(false);
        void pull();
        startTimer();
      } else {
        setPaused(true);
        // 标签页在后台时不发高频请求；恢复时立即补一次增量。
        stopTimer();
      }
    };
    void pull(true);
    if (document.visibilityState === "visible") startTimer();
    else setPaused(true);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelledRef.current = true;
      stopTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [account, pull]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      await pull();
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="user-trajectory">
      <div className="panel-head trajectory-head">
        <h3>关键操作轨迹</h3>
        <small>
          {error !== ""
            ? `${error}${lastSuccess ? ` · 最后成功 ${formatAge(lastSuccess)}` : ""}`
            : lastSuccess
              ? `最近更新 ${formatAge(lastSuccess)}`
              : "正在读取…"}
          {paused ? " · 标签页在后台，已暂停刷新" : ""}
        </small>
      </div>
      {items.length === 0 ? (
        <div className="empty-panel">
          {error !== "" ? "暂时读不到轨迹，恢复后会自动继续。" : "还没有可显示的操作记录。"}
        </div>
      ) : (
        <>
          <ol className="trajectory-list">
            {items.map((item) => (
              <TrajectoryRow key={item.id} item={item} onFocusRun={onFocusRun} />
            ))}
          </ol>
          {items.length >= TRAJECTORY_LIMIT ? (
            <small className="trajectory-note">仅显示最近 {TRAJECTORY_LIMIT} 条。</small>
          ) : null}
          {hasMore ? (
            <button
              type="button"
              className="ghost-action"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "加载中…" : "继续加载"}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}

function TrajectoryRow({
  item,
  onFocusRun,
}: {
  item: ClientEventView;
  onFocusRun: (id: string) => void;
}) {
  const related: Array<{ kind: string; id: string; focus: boolean }> = [];
  if (item.job_id) related.push({ kind: "作业", id: item.job_id, focus: true });
  if (item.turn_id) related.push({ kind: "回合", id: item.turn_id, focus: true });
  if (item.event_id) related.push({ kind: "事件", id: item.event_id, focus: false });
  if (item.asset_id) related.push({ kind: "资料", id: item.asset_id, focus: false });
  if (item.entity_id) related.push({ kind: "人物", id: item.entity_id, focus: false });
  return (
    <li className="trajectory-row" data-result={item.result}>
      <time dateTime={item.received_at}>{formatClock(item.received_at)}</time>
      <span className="trajectory-action">
        {EVENT_LABELS[item.event_name] ?? "未命名操作"}
      </span>
      <span className="job-status" data-status={item.result}>
        {CLIENT_RESULT_LABELS[item.result] ?? "状态更新中"}
      </span>
      <span className="trajectory-route">{item.route}</span>
      {item.error_code ? (
        <span className="trajectory-error">
          {CLIENT_ERROR_LABELS[item.error_code] ?? "未知原因"}
        </span>
      ) : null}
      {related.map((entry) =>
        entry.focus ? (
          <button
            key={`${entry.kind}-${entry.id}`}
            type="button"
            className="trajectory-link"
            onClick={() => onFocusRun(entry.id)}
            title={`定位到同一用户的${entry.kind}`}
          >
            {entry.kind} {shortId(entry.id)}
          </button>
        ) : (
          <span key={`${entry.kind}-${entry.id}`} className="trajectory-chip">
            {entry.kind} {shortId(entry.id)}
          </span>
        ),
      )}
      {item.result === "failed" || item.result === "rejected" ? (
        <a className="trajectory-link" href="#admin-alerts">
          查看告警
        </a>
      ) : null}
    </li>
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

function formatClock(value: string): string {
  try {
    return new Date(value).toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    return value;
  }
}

function formatTime(value: string): string {
  try {
    return new Date(value).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return value;
  }
}

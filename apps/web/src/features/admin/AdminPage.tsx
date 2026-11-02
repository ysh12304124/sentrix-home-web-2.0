import { useEffect, useState } from "react";

import { useAuth } from "../../app/AuthProvider";
import "../auth/auth.css";

interface ServiceState {
  ok?: boolean;
  status_code?: number;
  error?: string;
  status?: Record<string, unknown>;
}

interface Overview {
  sampled_at: string | null;
  stale: boolean;
  host?: {
    cpu_percent?: number | null;
    ram_used_gb?: number;
    ram_total_gb?: number;
    disk_free_gb?: number | null;
    gpu?: Record<string, unknown>;
  };
  services?: {
    model?: ServiceState;
    bge?: ServiceState;
    clip?: ServiceState;
    users?: Record<string, ServiceState>;
  };
  units?: Record<string, Record<string, unknown>>;
  model_queue?: Record<string, unknown>;
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
}

export function AdminPage() {
  const { identity } = useAuth();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (identity?.role !== "admin") return;
    const load = () => {
      fetch("/control/v1/admin/overview", { credentials: "same-origin" })
        .then(async (response) => {
          if (!response.ok) throw new Error(`概览接口返回 ${response.status}`);
          setOverview((await response.json()) as Overview);
        })
        .catch((cause: Error) => setError(cause.message));
      fetch("/control/v1/admin/incidents?limit=20", { credentials: "same-origin" })
        .then(async (response) =>
          response.ok ? ((await response.json()) as { items: Incident[] }).items : [],
        )
        .then(setIncidents)
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 10_000);
    return () => window.clearInterval(timer);
  }, [identity]);

  if (identity?.role !== "admin") {
    return <section className="view-root"><p>当前账号没有管理权限。</p></section>;
  }

  const services = overview?.services;
  return (
    <section className="view-root">
      <h1>运行概览</h1>
      {error !== "" ? <p role="alert">{error}</p> : null}
      <p>
        采样时间：{overview?.sampled_at ?? "未采集"}
        {overview?.stale ? "（已过期）" : ""} · 未解决告警：{overview?.open_incidents ?? "—"}
      </p>

      <h2>服务</h2>
      <dl className="admin-summary">
        <dt>主模型</dt><dd>{serviceLabel(services?.model)}</dd>
        <dt>BGE-M3</dt><dd>{serviceLabel(services?.bge)}</dd>
        <dt>Chinese-CLIP</dt><dd>{serviceLabel(services?.clip)}</dd>
        {Object.entries(services?.users ?? {}).map(([account, value]) => (
          <span key={account} style={{ display: "contents" }}>
            <dt>用户 API · {account}</dt>
            <dd>{serviceLabel(value)}</dd>
          </span>
        ))}
      </dl>

      <h2>机器资源</h2>
      <dl className="admin-summary">
        <dt>CPU</dt><dd>{overview?.host?.cpu_percent ?? "未采集"}%</dd>
        <dt>内存</dt>
        <dd>
          {overview?.host?.ram_used_gb ?? "—"} / {overview?.host?.ram_total_gb ?? "—"} GiB
        </dd>
        <dt>磁盘可用</dt><dd>{overview?.host?.disk_free_gb ?? "未采集"} GiB</dd>
        <dt>GPU</dt><dd>{gpuLabel(overview?.host?.gpu)}</dd>
      </dl>

      <h2>模型队列</h2>
      <pre>{JSON.stringify(overview?.model_queue ?? {}, null, 2)}</pre>

      <h2>systemd 单元</h2>
      <pre>{JSON.stringify(overview?.units ?? {}, null, 2)}</pre>

      <h2>告警与事件</h2>
      {incidents.length === 0 ? (
        <p>暂无事件记录。</p>
      ) : (
        <ul>
          {incidents.map((incident) => (
            <li key={incident.event_id}>
              [{incident.status}] {incident.severity} · {incident.source} · {incident.message}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function serviceLabel(state: ServiceState | undefined): string {
  if (state === undefined) return "未采集";
  if (state.ok) return "可用";
  if (state.error !== undefined) return `不可用：${state.error}`;
  return `不可用（HTTP ${state.status_code ?? "?"}）`;
}

function gpuLabel(gpu: Record<string, unknown> | undefined): string {
  if (gpu === undefined || gpu.status !== "ok") return "未采集";
  return `${gpu.name ?? "GPU"} · 显存 ${gpu.memory_used_mib ?? "?"}/${gpu.memory_total_mib ?? "?"} MiB · ${gpu.temperature_c ?? "?"}°C`;
}

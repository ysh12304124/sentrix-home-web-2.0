import { useEffect, useState } from "react";

import { useAuth } from "../../app/AuthProvider";
import "../auth/auth.css";

interface Diagnostics {
  build_sha?: string;
  ready?: boolean;
  route_users?: number;
  model?: { upstream?: string; model_id?: string; slots?: number };
  schema_version?: string;
  host?: { process_rss_mb?: number; disk_free_gb?: number };
}

export function AdminPage() {
  const { identity } = useAuth();
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (identity?.role !== "admin") return;
    fetch("/control/v1/diagnostics", { credentials: "same-origin" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`诊断接口返回 ${response.status}`);
        setData((await response.json()) as Diagnostics);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [identity]);

  if (identity?.role !== "admin") {
    return <section className="view-root"><p>当前账号没有管理权限。</p></section>;
  }

  return (
    <section className="view-root">
      <h1>运行概览</h1>
      {error !== "" ? <p role="alert">{error}</p> : null}
      {data === null ? (
        <p>正在读取控制面状态…</p>
      ) : (
        <dl className="admin-summary">
          <dt>就绪</dt><dd>{data.ready ? "是" : "否"}</dd>
          <dt>构建</dt><dd>{data.build_sha ?? "unknown"}</dd>
          <dt>控制库 schema</dt><dd>{data.schema_version ?? "—"}</dd>
          <dt>已登记用户 API</dt><dd>{data.route_users ?? 0}</dd>
          <dt>主模型上游</dt><dd>{data.model?.upstream ?? "—"}</dd>
          <dt>模型槽位</dt><dd>{data.model?.slots ?? "—"}</dd>
          <dt>控制进程内存</dt><dd>{data.host?.process_rss_mb ?? "未采集"} MiB</dd>
          <dt>磁盘可用</dt><dd>{data.host?.disk_free_gb ?? "未采集"} GiB</dd>
        </dl>
      )}
    </section>
  );
}

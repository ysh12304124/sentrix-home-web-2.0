import { useCallback, useEffect, useRef, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import {
  cancelImportJob,
  listImportJobs,
  uploadMedia,
  type ImportJobView,
} from "../../shared/api/client";

type EntryStatus = "waiting" | "uploading" | "accepted" | "failed";

interface Entry {
  key: string;
  file: File;
  status: EntryStatus;
  jobId: string;
  error: string;
}

const MAX_CONCURRENT = 2;
const ACTIVE_JOB_STATUSES = new Set([
  "accepted",
  "queued",
  "running",
  "waiting_dependency",
  "retry_scheduled",
]);

function keyFor(file: File): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(16).slice(2)}`;
}

function jobStatusLabel(job: ImportJobView): string {
  const labels: Record<string, string> = {
    accepted: "已接收",
    queued: "排队中",
    running: "处理中",
    waiting_dependency: "等待服务恢复",
    retry_scheduled: "等待重试",
    completed: "完成",
    partial: "部分完成",
    needs_attention: "需要处理",
    cancelled: "已取消",
  };
  return labels[job.status] ?? job.status;
}

export function ImportsPage() {
  const { scopeId, spaces } = useScope();
  // “全部相册”是读侧筛选；写入必须落到具体相册，默认是 home-default。
  const targetScope = scopeId || "home-default";
  const targetName =
    spaces.find((space) => space.id === targetScope)?.name ?? targetScope;
  const [entries, setEntries] = useState<Entry[]>([]);
  const [jobs, setJobs] = useState<ImportJobView[]>([]);
  const [error, setError] = useState("");
  const entriesRef = useRef<Entry[]>([]);
  entriesRef.current = entries;

  const refreshJobs = useCallback(async () => {
    try {
      setJobs(await listImportJobs(scopeId));
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [scopeId]);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    const hasActive =
      jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status)) ||
      entries.some((entry) => entry.status === "waiting" || entry.status === "uploading");
    if (!hasActive) return;
    const timer = window.setTimeout(() => void refreshJobs(), 2000);
    return () => window.clearTimeout(timer);
  }, [jobs, entries, refreshJobs]);

  const updateEntry = useCallback((key: string, patch: Partial<Entry>) => {
    setEntries((previous) =>
      previous.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)),
    );
  }, []);

  const uploadOne = useCallback(
    async (entry: Entry) => {
      updateEntry(entry.key, { status: "uploading", error: "" });
      try {
        const accepted = await uploadMedia(entry.file, targetScope, entry.key);
        updateEntry(entry.key, { status: "accepted", jobId: accepted.job_id });
      } catch (cause) {
        updateEntry(entry.key, { status: "failed", error: (cause as Error).message });
      }
    },
    [targetScope, updateEntry],
  );

  const startUploads = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      const additions: Entry[] = files.map((file) => ({
        key: keyFor(file),
        file,
        status: "waiting",
        jobId: "",
        error: "",
      }));
      setEntries((previous) => [...additions, ...previous]);
      const queue = [...additions];
      await Promise.all(
        Array.from({ length: Math.min(MAX_CONCURRENT, queue.length) }, async () => {
          while (queue.length > 0) {
            const next = queue.shift();
            if (next !== undefined) await uploadOne(next);
          }
        }),
      );
      await refreshJobs();
    },
    [refreshJobs, uploadOne],
  );

  return (
    <>
      <header className="import-head">
        <p className="eyebrow">资料导入</p>
        <h1>把照片和视频交给回忆</h1>
        <p>
          上传后会进入持久作业，关闭页面也会继续处理。当前导入目标：<strong>{targetName}</strong>
          {scopeId === "" ? "（查看全部相册时默认导入这里）" : ""}
        </p>
      </header>
      <div className="import-stats">
        <article><span>全部作业</span><b>{jobs.length}</b></article>
        <article><span>处理中</span><b>{jobs.filter((job) => ["accepted", "queued", "running", "waiting_dependency", "retry_scheduled"].includes(job.status)).length}</b></article>
        <article><span>已完成</span><b>{jobs.filter((job) => job.status === "completed").length}</b></article>
        <article><span>需要处理</span><b>{jobs.filter((job) => job.status === "needs_attention" || job.status === "partial").length}</b></article>
      </div>
      <section className="import-layout">
      <div>
        <label
          className="dropzone"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            void startUploads(Array.from(event.dataTransfer.files));
          }}
        >
          <input
            type="file"
            multiple
            accept="image/*,video/*,audio/*"
            onChange={(event) => void startUploads(Array.from(event.target.files ?? []))}
          />
          <span className="drop-icon" aria-hidden="true">↓</span>
          <strong>拖入照片或视频，或点击选择</strong>
          <small>同一时间最多接收 2 个文件；关闭页面后处理仍会继续。</small>
          <span className="button primary">选择资料</span>
        </label>

        <div className="import-notice">
          <span className="notice-mark" aria-hidden="true">i</span>
          <div>
            <strong>已接收不等于已处理</strong>
            <p>列表里的状态来自服务器上的持久作业；刷新或重新登录都能看到同一批进度。</p>
          </div>
        </div>

        {error !== "" ? <p role="alert">作业状态读取失败：{error}</p> : null}

        {entries.length > 0 ? (
          <>
            <h2>本次上传</h2>
            <div className="queue-list">
              {entries.map((entry) => (
                <div key={entry.key} className="queue-row">
                  <span className="queue-type">FILE</span>
                  <div>
                    <strong>{entry.file.name}</strong>
                    <small>
                      <span
                        className="job-status"
                        data-status={
                          entry.status === "accepted"
                            ? "accepted"
                            : entry.status === "failed"
                              ? "failed"
                              : "queued"
                        }
                      >
                        {entry.status === "waiting"
                          ? "等待上传"
                          : entry.status === "uploading"
                            ? "正在上传"
                            : entry.status === "accepted"
                              ? "已接收"
                              : "上传失败"}
                      </span>{" "}
                      {entry.status === "accepted"
                        ? `作业 ${entry.jobId}`
                        : entry.status === "failed"
                          ? entry.error
                          : ""}
                    </small>
                  </div>
                  {entry.status === "failed" ? (
                    <button type="button" className="text-button" onClick={() => void uploadOne(entry)}>
                      重试
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>

      <aside className="import-status">
        <div className="panel-title">
          <h2>导入作业</h2>
          <button type="button" className="text-button" onClick={() => void refreshJobs()}>
            刷新 ↻
          </button>
        </div>
        <p className="live-label">当前范围：{spaces.find((space) => space.id === scopeId)?.name ?? "全部相册"}</p>
        {jobs.length === 0 ? (
          <p className="empty">这个范围还没有导入作业。</p>
        ) : (
          <div className="queue-list">
            {jobs.map((job) => (
              <div key={job.job_id} className="queue-row">
                <span className="queue-type">{job.media_type.slice(0, 3).toUpperCase()}</span>
                <div>
                  <strong>{job.file_name}</strong>
                  <small>
                    <span className="job-status" data-status={job.status}>
                      {jobStatusLabel(job)}
                    </span>{" "}
                    {job.stage !== "" ? job.stage : ""}
                    {job.attempts > 0 ? ` · 第 ${job.attempts} 次` : ""}
                  </small>
                  {job.error_message !== "" ? (
                    <small className="job-error">{job.error_message}</small>
                  ) : null}
                  <div className="job-progress" aria-hidden="true">
                    <i
                      style={{
                        width: `${
                          job.stages.length === 0
                            ? job.status === "completed"
                              ? 100
                              : 8
                            : Math.round(
                                (job.stages.filter((stage) => stage.status === "done" || stage.status === "skipped").length /
                                  job.stages.length) *
                                  100,
                              )
                        }%`,
                      }}
                    />
                  </div>
                </div>
                {ACTIVE_JOB_STATUSES.has(job.status) ? (
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => void cancelImportJob(job.job_id).then(refreshJobs)}
                  >
                    取消
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </aside>
      </section>
    </>
  );
}

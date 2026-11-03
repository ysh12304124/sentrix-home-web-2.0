import { useCallback, useEffect, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { newClientId } from "../../shared/id";
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

// 与后端固定阶段顺序一致；列表接口不回传每个作业的 stage 行，用当前阶段估算单张进度。
const STAGE_ORDER = [
  "asset_staged",
  "prepared",
  "visual_indexed",
  "observation_saved",
  "faces_saved",
  "event_assigned",
  "text_indexed",
  "entities_saved",
  "facts_saved",
  "lexical_indexed",
  "video_scenes_done",
];

function jobPercent(job: ImportJobView): number {
  if (job.status === "completed") return 100;
  if (job.status === "cancelled") return 0;
  if (job.stage === "postprocess") return 95;
  const index = STAGE_ORDER.indexOf(job.stage);
  if (index >= 0) return Math.round(((index + 1) / STAGE_ORDER.length) * 100);
  return job.status === "accepted" || job.status === "queued" ? 4 : 8;
}

function keyFor(_file: File): string {
  // HTTP 头只能带 ISO-8859-1；中文文件名绝不能进 Idempotency-Key。
  return newClientId("upload");
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
  const [dragging, setDragging] = useState(false);

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
        void reportClientEvent("upload.started", `${entry.file.name}:${entry.file.size}`);
        const accepted = await uploadMedia(entry.file, targetScope, entry.key);
        updateEntry(entry.key, { status: "accepted", jobId: accepted.job_id });
        void reportClientEvent("upload.accepted", `${entry.file.name}:${accepted.job_id}`);
      } catch (cause) {
        const message = (cause as Error).message;
        updateEntry(entry.key, { status: "failed", error: message });
        void reportClientEvent("upload.failed", `${entry.file.name}:${message}`);
      }
    },
    [targetScope, updateEntry],
  );

  const startUploads = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      try {
        const additions: Entry[] = files.map((file) => ({
          key: keyFor(file),
          file,
          status: "waiting",
          jobId: "",
          error: "",
        }));
        setEntries((previous) => [...additions, ...previous]);
        setError("");
        void reportClientEvent(
          "upload.selected",
          additions.map((entry) => `${entry.file.name}:${entry.file.size}:${entry.file.type}`).join(","),
        );
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
      } catch (cause) {
        setError(`上传没有开始：${(cause as Error).message}`);
      }
    },
    [refreshJobs, uploadOne],
  );

  const completedCount = jobs.filter((job) => job.status === "completed").length;
  const processingCount = jobs.filter((job) => ACTIVE_JOB_STATUSES.has(job.status)).length;
  const problemCount = jobs.filter(
    (job) => job.status === "needs_attention" || job.status === "partial",
  ).length;
  const waitingUploads = entries.filter(
    (entry) => entry.status === "waiting" || entry.status === "uploading",
  ).length;
  const failedUploads = entries.filter((entry) => entry.status === "failed").length;
  const batchTotal = jobs.length + waitingUploads;
  const batchPercent =
    batchTotal > 0 ? Math.round((completedCount / batchTotal) * 100) : 0;

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
      <div className="import-overall">
        <div className="import-overall-copy">
          <span>当前相册处理进度</span>
          <b>
            {completedCount} / {batchTotal}
          </b>
        </div>
        <div className="import-overall-bar" aria-hidden="true">
          <i style={{ width: `${batchPercent}%` }} />
        </div>
        <small>
          完成 {completedCount} · 处理中 {processingCount} · 等待上传 {waitingUploads}
          {failedUploads > 0 ? ` · 上传失败 ${failedUploads}` : ""}
        </small>
      </div>

      <div className="import-stats">
        <article><span>已接收</span><b>{jobs.length}</b></article>
        <article><span>处理中</span><b>{processingCount}</b></article>
        <article><span>已完成</span><b>{completedCount}</b></article>
        <article><span>需要处理</span><b>{problemCount}</b></article>
      </div>
      <section className="import-layout">
      <div>
        <section
          className={dragging ? "import-picker dragging" : "import-picker"}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            void startUploads(Array.from(event.dataTransfer.files));
          }}
        >
          <span className="import-picker-mark" aria-hidden="true">↓</span>
          <div className="import-picker-copy">
            <strong>拖入照片或视频</strong>
            <small>也可以一次选择整个文件夹；上传后关闭页面，处理仍会继续。</small>
          </div>
          <div className="import-picker-actions">
            <label className="button primary file-button">
              选择文件
              <input
                type="file"
                multiple
                accept="image/*,video/*,audio/*"
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  void startUploads(files);
                }}
              />
            </label>
            <label className="button ghost file-button">
              选择文件夹
              <input
                type="file"
                multiple
                ref={(node) => {
                  if (node === null) return;
                  node.setAttribute("webkitdirectory", "");
                  node.setAttribute("directory", "");
                }}
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  void startUploads(files);
                }}
              />
            </label>
          </div>
        </section>

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
            <h2>本次上传（{entries.length}）</h2>
            <div className="queue-list upload-queue">
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
          <h2>导入作业{jobs.length > 0 ? `（${jobs.length}）` : ""}</h2>
          <button type="button" className="text-button" onClick={() => void refreshJobs()}>
            刷新 ↻
          </button>
        </div>
        <p className="live-label">当前范围：{spaces.find((space) => space.id === scopeId)?.name ?? "全部相册"}</p>
        {jobs.length === 0 ? (
          <p className="empty">这个范围还没有导入作业。</p>
        ) : (
          <div className="queue-list job-queue">
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
                    <i style={{ width: `${jobPercent(job)}%` }} />
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

async function reportClientEvent(kind: string, detail: string): Promise<void> {
  try {
    const match = document.cookie.match(/(?:^|;\s*)sentrix_(?:control|admin)_csrf=([^;]*)/);
    const csrf = match === null ? "" : decodeURIComponent(match[1]);
    await fetch("/control/v1/client-events", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
      body: JSON.stringify({ kind, detail: detail.slice(0, 500) }),
    });
  } catch {
    // 诊断日志失败不能影响主流程。
  }
}

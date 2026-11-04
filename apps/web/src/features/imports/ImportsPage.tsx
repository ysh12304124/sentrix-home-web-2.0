import { useCallback, useEffect, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { newClientId } from "../../shared/id";
import {
  errorCodeFromStatus,
  newOperationId,
  reportClientEvent,
} from "../../shared/clientEvents";
import {
  ApiError,
  cancelImportJob,
  fetchImportProgress,
  listImportJobs,
  uploadMedia,
  type ImportJobView,
  type ImportProgress,
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

function mediaKindLabel(mediaType: string): string {
  return { image: "图片", video: "视频", audio: "音频", text: "文件" }[mediaType] ?? "资料";
}

function fileKindLabel(file: File): string {
  if (file.type.startsWith("video/")) return "视频";
  if (file.type.startsWith("audio/")) return "音频";
  return "图片";
}

function formatUploadTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", { hour12: false }).slice(0, 16);
}

const POSTPROCESS_STAGE_LABELS: Readonly<Record<string, string>> = {
  starting: "准备整理",
  participants: "整理人物与参与者",
  memory_graph: "梳理记忆关系",
  event_summary: "总结事件",
  family_graph: "推断家庭关系",
  insights: "生成人物画像",
  retry_wait: "等待重试",
  done: "整理完成",
};

function postprocessStageLabel(stage: string): string {
  return POSTPROCESS_STAGE_LABELS[stage] ?? "整理回忆";
}

function postprocessPercent(progress: ImportProgress["postprocess"] | undefined): number | null {
  if (progress === undefined) return null;
  if (progress.state === "done") return 100;
  if (progress.stage === "event_summary" && progress.events_total > 0) {
    return Math.min(100, Math.round((progress.events_done / progress.events_total) * 100));
  }
  return null;
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
  const { scopeId } = useScope();
  // 界面不再区分相册；写入交给后端默认相册。
  const targetScope = "";
  const [entries, setEntries] = useState<Entry[]>([]);
  const [jobs, setJobs] = useState<ImportJobView[]>([]);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);

  const refreshJobs = useCallback(async () => {
    try {
      const [nextJobs, nextProgress] = await Promise.all([
        listImportJobs(scopeId),
        fetchImportProgress(scopeId),
      ]);
      setJobs(nextJobs);
      setProgress(nextProgress);
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [scopeId]);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    const postprocessActive =
      progress?.postprocess.state === "running" ||
      progress?.postprocess.state === "pending" ||
      progress?.postprocess.state === "waiting_retry";
    const hasActive =
      jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status)) ||
      entries.some((entry) => entry.status === "waiting" || entry.status === "uploading") ||
      postprocessActive;
    if (!hasActive) return;
    const timer = window.setTimeout(() => void refreshJobs(), 2000);
    return () => window.clearTimeout(timer);
  }, [jobs, entries, progress, refreshJobs]);

  const updateEntry = useCallback((key: string, patch: Partial<Entry>) => {
    setEntries((previous) =>
      previous.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)),
    );
  }, []);

  const uploadOne = useCallback(
    async (entry: Entry) => {
      updateEntry(entry.key, { status: "uploading", error: "" });
      try {
        void reportClientEvent({
          eventName: "import.upload_start",
          result: "started",
          operationId: entry.key,
          scopeId: targetScope,
        });
        const accepted = await uploadMedia(entry.file, targetScope, entry.key);
        updateEntry(entry.key, { status: "accepted", jobId: accepted.job_id });
        void reportClientEvent({
          eventName: "import.upload_accepted",
          result: "accepted",
          operationId: entry.key,
          scopeId: targetScope,
          jobId: accepted.job_id,
          assetId: accepted.asset_id,
        });
      } catch (cause) {
        const message = (cause as Error).message;
        updateEntry(entry.key, { status: "failed", error: message });
        void reportClientEvent({
          eventName: "import.upload_failed",
          result: "failed",
          operationId: entry.key,
          scopeId: targetScope,
          errorCode: cause instanceof ApiError ? errorCodeFromStatus(cause.status) : "network",
        });
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
        void reportClientEvent({
          eventName: "import.pick",
          result: "ok",
          operationId: newOperationId(),
          scopeId: targetScope,
        });
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

  const acceptedCount = progress?.library.total ?? jobs.length;
  const completedCount =
    progress?.library.completed ?? jobs.filter((job) => job.status === "completed").length;
  const processingCount =
    progress?.library.processing ??
    jobs.filter((job) => ACTIVE_JOB_STATUSES.has(job.status)).length;
  const problemCount =
    progress?.library.failed ??
    jobs.filter((job) => job.status === "needs_attention" || job.status === "partial").length;
  const waitingUploads = entries.filter(
    (entry) => entry.status === "waiting" || entry.status === "uploading",
  ).length;
  const failedUploads = entries.filter((entry) => entry.status === "failed").length;
  const batchTotal = acceptedCount + waitingUploads;
  const batchPercent =
    batchTotal > 0 ? Math.min(100, Math.round((completedCount / batchTotal) * 100)) : 0;
  const postprocess = progress?.postprocess;
  const postprocessValue = postprocessPercent(postprocess);
  const postprocessActive =
    postprocess?.state === "running" ||
    postprocess?.state === "pending" ||
    postprocess?.state === "waiting_retry";
  const postprocessText =
    postprocess === undefined
      ? "正在读取后处理进度…"
      : postprocess.state === "idle"
        ? "还没有需要整理的回忆。"
        : postprocess.state === "pending"
          ? "资料建库完成后会自动开始。"
          : postprocess.state === "waiting_retry"
            ? postprocess.next_attempt_at
              ? `这一步暂时没完成，将在 ${formatUploadTime(postprocess.next_attempt_at)} 后自动重试。`
              : "这一步暂时没完成，稍后会自动重试。"
            : postprocess.state === "done"
              ? "事件、人物关系与画像已经整理完成。"
              : postprocess.stage === "event_summary" && postprocess.events_total > 0
                ? `正在总结事件：${postprocess.events_done} / ${postprocess.events_total}。`
                : `当前阶段：${postprocessStageLabel(postprocess.stage)}。这一步完成后会自动进入下一段。`;
  const libraryComplete =
    batchTotal > 0 &&
    completedCount >= batchTotal &&
    waitingUploads === 0 &&
    processingCount === 0 &&
    failedUploads === 0 &&
    problemCount === 0;
  const postprocessComplete = postprocess?.state === "done";
  // 完成的阶段不再展示进度条；新的一批上传或新的后处理代际会重新出现。
  const showLibraryPhase = batchTotal > 0 && !libraryComplete;
  const showPostprocessPhase =
    postprocess !== undefined &&
    (postprocess.state === "pending" ||
      postprocess.state === "running" ||
      postprocess.state === "waiting_retry");
  const showAllCompleteNote = libraryComplete && postprocessComplete && acceptedCount > 0;

  return (
    <>
      <header className="import-head">
        <p className="eyebrow">资料导入</p>
        <h1>把照片和视频交给回忆</h1>
        <p>
          上传成功后会继续整理，关闭页面也不会中断。
        </p>
      </header>
      {showLibraryPhase || showPostprocessPhase ? (
        <div className="import-phases">
          {showLibraryPhase ? (
            <article className="import-phase">
              <div className="import-phase-head">
                <div>
                  <span>1 / 资料建库</span>
                  <small>逐张照片和视频：读取时间地点、理解画面、建立检索索引。</small>
                </div>
                <b>
                  {completedCount} / {batchTotal}
                </b>
              </div>
              <div className="import-phase-bar library" aria-hidden="true">
                <i style={{ width: `${batchPercent}%` }} />
              </div>
              <small>
                完成 {completedCount} · 处理中 {processingCount} · 等待上传 {waitingUploads}
                {failedUploads > 0 ? ` · 上传失败 ${failedUploads}` : ""}
              </small>
            </article>
          ) : null}

          {showPostprocessPhase ? (
            <article className="import-phase">
              <div className="import-phase-head">
                <div>
                  <span>2 / 整理回忆</span>
                  <small>把资料串成事件、总结标题与描述，并维护人物关系和画像。</small>
                </div>
                <b>
                  {postprocessValue !== null
                    ? `${postprocessValue}%`
                    : postprocess?.state === "done"
                      ? "完成"
                      : postprocessActive
                        ? "进行中"
                        : "等待"}
                </b>
              </div>
              <div
                className={
                  postprocess?.state === "running" && postprocessValue === null
                    ? "import-phase-bar postprocess indeterminate"
                    : "import-phase-bar postprocess"
                }
                aria-hidden="true"
              >
                <i
                  style={
                    postprocessValue !== null ? { width: `${postprocessValue}%` } : undefined
                  }
                />
              </div>
              <small>{postprocessText}</small>
            </article>
          ) : null}
        </div>
      ) : showAllCompleteNote ? (
        <p className="import-complete-note" role="status">
          这一批资料和回忆都已整理完成。
        </p>
      ) : null}

      <div className="import-stats">
        <article><span>已接收</span><b>{acceptedCount}</b></article>
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
          <div>
            <strong>上传成功后会继续整理</strong>
            <p>上传成功后服务器会继续整理；刷新或重新登录都能看到同一批进度。</p>
          </div>
        </div>

        {error !== "" ? <p role="alert">整理进度读取失败：{error}</p> : null}

        {entries.length > 0 ? (
          <>
            <h2>本次上传（{entries.length}）</h2>
            <div className="queue-list upload-queue">
              {entries.map((entry) => (
                <div key={entry.key} className="queue-row">
                  <span className="queue-type">FILE</span>
                  <div>
                    <strong>{fileKindLabel(entry.file)}</strong>
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
                        ? "已进入整理"
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
          <h2>整理进度{jobs.length > 0 ? `（${jobs.length}）` : ""}</h2>
          <button type="button" className="text-button" onClick={() => void refreshJobs()}>
            刷新 ↻
          </button>
        </div>
        <p className="live-label">全部上传记录</p>
        {jobs.length === 0 ? (
          <p className="empty">这里还没有上传记录。</p>
        ) : (
          <div className="queue-list job-queue">
            {jobs.map((job) => (
              <div key={job.job_id} className="queue-row">
                <span className="queue-type">{job.media_type.slice(0, 3).toUpperCase()}</span>
                <div>
                  <strong>{mediaKindLabel(job.media_type)}</strong>
                  <small>
                    <span className="job-status" data-status={job.status}>
                      {jobStatusLabel(job)}
                    </span>{" "}
                    上传于 {formatUploadTime(job.accepted_at)}
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
                    onClick={() => {
                      void reportClientEvent({
                        eventName: "import.cancel",
                        result: "started",
                        scopeId: targetScope,
                        jobId: job.job_id,
                      });
                      void cancelImportJob(job.job_id)
                        .then(refreshJobs)
                        .catch(() => undefined);
                    }}
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

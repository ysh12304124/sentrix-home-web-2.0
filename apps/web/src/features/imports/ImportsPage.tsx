import { useCallback, useEffect, useRef, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import {
  fetchImportProgress,
  listAssetPage,
  listImportJobs,
  type ImportProgress,
} from "../../shared/api/client";
import { useUploads } from "./UploadsProvider";
import { DocumentImport } from "./DocumentImport";

// 与后端固定阶段顺序一致；这些都算「建库仍在进行」。
const ACTIVE_JOB_STATUSES = new Set([
  "accepted",
  "queued",
  "running",
  "waiting_dependency",
  "retry_scheduled",
]);
// 落到终态但不是成功：任何一步失败都算「上传失败」。
const FAILED_JOB_STATUSES = new Set(["partial", "needs_attention", "cancelled", "failed"]);

type Phase = "add" | "library" | "postprocess";

function formatUploadTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", { hour12: false }).slice(0, 16);
}

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
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

// 后端只有 event_summary 上报事件计数，其余阶段只给 stage 名。用阶段权重把
// 5 个阶段映射到 0–100 的区间，event_summary 段内再按真实事件数细分。
const POSTPROCESS_STAGE_RANGES: Readonly<Record<string, readonly [number, number]>> = {
  starting: [0, 5],
  participants: [5, 18],
  memory_graph: [18, 32],
  event_summary: [32, 72],
  family_graph: [72, 90],
  insights: [90, 100],
  done: [100, 100],
};

function postprocessStageLabel(stage: string): string {
  return POSTPROCESS_STAGE_LABELS[stage] ?? "整理回忆";
}

function postprocessPercent(progress: ImportProgress["postprocess"] | undefined): number {
  if (progress === undefined || progress.state === "idle") return 0;
  if (progress.state === "done") return 100;
  if (progress.state === "pending") return 0;
  const [low, high] = POSTPROCESS_STAGE_RANGES[progress.stage] ?? [0, 100];
  if (progress.stage === "event_summary" && progress.events_total > 0) {
    return Math.min(
      high,
      Math.round(low + ((high - low) * progress.events_done) / progress.events_total),
    );
  }
  return low;
}

/**
 * 进度条的「缓慢向前走」。真实进度是按张/按批跳的，两次跳之间条子完全静止，看着像卡住；
 * 这里给**显示宽度**叠一段缓慢爬升，但绝不超过调用方给的 cap（cap 由调用方按下一批
 * 真实节点算，保证不会出现「条子说好了、其实还没好」）。
 *
 * 真实进度一跳，显示值立刻收回真实值重新爬（`real` 变化会重置），不会累积偏差。
 */
function useCreepingPercent(
  real: number,
  capPercent: number,
  running: boolean,
  tickIncrement: number,
): number {
  const [creep, setCreep] = useState({ base: real, value: 0 });
  const active = running && creep.base === real;
  const creepValue = active ? creep.value : 0;
  useEffect(() => {
    if (!running || capPercent <= 0) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const timer = window.setInterval(() => {
      setCreep((previous) => {
        const grown = previous.base === real ? previous.value : 0;
        return {
          base: real,
          value:
            grown >= capPercent
              ? capPercent
              : Math.min(capPercent, grown + tickIncrement),
        };
      });
    }, 250);
    return () => window.clearInterval(timer);
  }, [real, capPercent, running, tickIncrement]);
  return Math.max(0, Math.min(100, real + creepValue));
}

/** 建库 / 整理两步步骤条：当前阶段高亮，已完成阶段打勾。 */
function PhaseStepper({ phase }: { phase: "library" | "postprocess" }) {
  return (
    <div className="import-stepper" role="list" aria-label="导入步骤">
      <div
        className={`import-step ${phase === "library" ? "is-active" : "is-done"}`}
        role="listitem"
      >
        <span className="import-step-dot">{phase === "postprocess" ? "✓" : "1"}</span>
        <span>资料建库</span>
      </div>
      <span className={`import-step-line ${phase === "postprocess" ? "is-done" : ""}`} />
      <div
        className={`import-step ${phase === "postprocess" ? "is-active" : ""}`}
        role="listitem"
      >
        <span className="import-step-dot">2</span>
        <span>整理回忆</span>
      </div>
    </div>
  );
}

export function ImportsPage() {
  const { scopeId } = useScope();
  const {
    entries,
    jobs,
    progress,
    assetTotal,
    setImportData,
    uploadNotices,
    resumeNotice,
    uploadRevision,
    receiveSeqBase,
    startUploads,
  } = useUploads();

  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [recordsOpen, setRecordsOpen] = useState(false);

  const refreshJobs = useCallback(async () => {
    try {
      const [nextJobs, nextProgress, assetPage] = await Promise.all([
        listImportJobs(scopeId),
        fetchImportProgress(scopeId),
        // 资产总数取后端资料库（不是 import_jobs 计数）：limit=1 只为拿 total。
        listAssetPage(scopeId, { limit: 1 }).catch(() => null),
      ]);
      setImportData({
        jobs: nextJobs,
        progress: nextProgress,
        assetTotal: assetPage === null ? undefined : assetPage.total,
      });
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [scopeId, setImportData]);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    if (uploadRevision > 0) void refreshJobs();
  }, [refreshJobs, uploadRevision]);

  const postprocess = progress?.postprocess;
  const ppState = postprocess?.state;
  const postprocessActive =
    ppState === "running" || ppState === "pending" || ppState === "waiting_retry";

  // 是否有进行中的工作：作业未落定、前端还在传、或后处理在跑。轮询与收尾补刷共用同一判据。
  const uploadingNow = entries.some(
    (entry) => entry.status === "waiting" || entry.status === "uploading",
  );
  const buildingNow = jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status));
  const hasActiveWork = uploadingNow || buildingNow || postprocessActive;

  // 有进行中的工作时每 2 秒刷新一次，让进度与资料库资产总数自己滚动。
  useEffect(() => {
    if (!hasActiveWork) return;
    const timer = window.setTimeout(() => void refreshJobs(), 2000);
    return () => window.clearTimeout(timer);
  }, [hasActiveWork, jobs, entries, progress, refreshJobs]);

  // 全部落定后再补刷一次：资产写进资料库可能略晚于作业变 completed。
  const hadActiveWork = useRef(false);
  useEffect(() => {
    if (hasActiveWork) {
      hadActiveWork.current = true;
      return;
    }
    if (!hadActiveWork.current) return;
    hadActiveWork.current = false;
    const timer = window.setTimeout(() => void refreshJobs(), 3000);
    return () => window.clearTimeout(timer);
  }, [hasActiveWork, refreshJobs]);

  // ---- 本批次统计（receive_seq 起点，刷新/切页回来一致）----
  const batchJobs =
    receiveSeqBase === null ? [] : jobs.filter((job) => job.receive_seq > receiveSeqBase);
  const batchSuccess = batchJobs.filter((job) => job.status === "completed").length;
  const batchFailed = batchJobs.filter((job) => FAILED_JOB_STATUSES.has(job.status)).length;
  const batchTotal = batchJobs.length;
  const waitingUploads = entries.filter(
    (entry) => entry.status === "waiting" || entry.status === "uploading",
  ).length;

  // ---- 建库卡口径：本批次作业，一张成功分子 +1；新任务起点推进后分母回退。----
  const libraryDone = batchTotal > 0 && batchSuccess >= batchTotal;
  const libraryRunning = batchTotal > 0 && !libraryDone;
  const libraryPercent =
    batchTotal > 0 ? Math.min(100, Math.round((batchSuccess / batchTotal) * 100)) : 0;
  const libraryTitle =
    batchTotal === 0 ? "资料建库" : libraryDone ? "资料建库完成" : "正在资料建库";
  // 建库是 4 张并发（后端 import_workers=4），完成数是 4 张一跳：起步和每 4 张之后
  // 等待最久，批内间隔很短。爬升集中在长等待档。
  const LIBRARY_BATCH = 4;
  const atBatchBoundary =
    batchTotal > 0 && batchSuccess % LIBRARY_BATCH === 0 && !libraryDone;
  const nextBatch = Math.min(
    batchTotal,
    Math.floor(batchSuccess / LIBRARY_BATCH) * LIBRARY_BATCH + LIBRARY_BATCH,
  );
  const batchGapPercent =
    batchTotal > 0 ? ((nextBatch - batchSuccess) / batchTotal) * 100 : 0;
  const libraryCreepCap = Math.min(8, batchGapPercent * 0.8);
  const libraryDisplay = useCreepingPercent(
    libraryPercent,
    libraryCreepCap,
    libraryRunning,
    atBatchBoundary ? 0.12 : 0.04,
  );

  // ---- 回忆卡口径 ----
  const postprocessValue = postprocessPercent(postprocess);
  const postprocessText =
    postprocess === undefined
      ? "正在读取后处理进度…"
      : ppState === "idle"
        ? "还没有需要整理的回忆。"
        : ppState === "pending"
          ? "资料建库完成后会自动开始。"
          : ppState === "waiting_retry"
            ? postprocess.next_attempt_at
              ? `这一步暂时没完成，将在 ${formatUploadTime(postprocess.next_attempt_at)} 后自动重试。`
              : "这一步暂时没完成，稍后会自动重试。"
            : ppState === "done"
              ? "事件、人物关系与画像已经整理完成。"
              : postprocess.stage === "event_summary" && postprocess.events_total > 0
                ? `正在总结事件：${postprocess.events_done} / ${postprocess.events_total}。`
                : `当前阶段：${postprocessStageLabel(postprocess.stage)}。这一步完成后会自动进入下一段。`;
  const postprocessRunning = ppState === "running" || ppState === "pending";
  const postprocessTitle =
    ppState === "done"
      ? "整理回忆完成"
      : ppState === "running" || ppState === "pending" || ppState === "waiting_retry"
        ? "正在整理回忆"
        : "整理回忆";
  const postprocessDisplay = useCreepingPercent(postprocessValue, 6, postprocessRunning, 0.08);

  // ---- 三阶段调度：上传中=添加资料页，建库中=资料建库页，整理中=整理回忆页，其余=添加资料页 ----
  let phase: Phase = "add";
  if (uploadingNow) phase = "add";
  else if (postprocessActive) phase = "postprocess";
  else if (buildingNow) phase = "library";

  // 上传记录：后端作业里已落定的成功/失败，刷新不丢；中间态不进表。
  const records = jobs
    .filter(
      (job) => job.status === "completed" || FAILED_JOB_STATUSES.has(job.status),
    )
    .slice()
    .sort((a, b) => b.accepted_at.localeCompare(a.accepted_at));

  const errorBanner = error !== "" ? (
    <p role="alert" className="import-error">
      整理进度读取失败：{error}
    </p>
  ) : null;

  // ---------- 阶段 1：添加资料页 ----------
  if (phase === "add") {
    return (
      <div className="import-flow">
        <section className="import-add-card">
          <header className="import-add-head">
            <h1>添加资料</h1>
            <p>把照片和视频交给回忆，系统会自动建库并整理成回忆</p>
          </header>

          <div className="import-notices">
            {resumeNotice !== "" ? (
              <p className="import-resume-note" role="status">
                {resumeNotice}
              </p>
            ) : null}
            {uploadNotices.map((notice) => (
              <p key={notice.id} className="import-resume-note" role="status">
                {notice.text}
              </p>
            ))}
          </div>

          <div className="import-stats">
            <article>
              <span>历史上传总数</span>
              <b>{assetTotal ?? "—"}</b>
            </article>
            <article>
              <span>本次待上传</span>
              <b>{waitingUploads}</b>
            </article>
            <article>
              <span>本次上传成功</span>
              <b>{batchSuccess}</b>
            </article>
            <article>
              <span>本次上传失败</span>
              <b>{batchFailed}</b>
            </article>
          </div>

          <DocumentImport scopeId={scopeId} onImported={() => void refreshJobs()} />

          <div className="import-add-grid">
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
              <span className="import-picker-mark" aria-hidden="true">
                ↓
              </span>
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
              <small className="import-picker-foot">
                支持 图片 / 视频 / 音频 · 单个文件不超过 20MB
              </small>
            </section>

            <aside className="import-tips">
              <div className="import-tips-head">
                <span className="import-tips-icon" aria-hidden="true">
                  i
                </span>
                <strong>处理小贴士</strong>
              </div>
              <ul>
                <li>上传后先建库，再整理回忆</li>
                <li>自动读取时间地点与画面</li>
                <li>整理中可关闭页面，进程不会中断</li>
              </ul>
            </aside>
          </div>

          {errorBanner}

          <section className="import-records">
            <button
              type="button"
              className="import-records-toggle"
              aria-expanded={recordsOpen}
              onClick={() => setRecordsOpen((value) => !value)}
            >
              <span className="summary-caret" aria-hidden="true">
                {recordsOpen ? "▾" : "▸"}
              </span>
              历史上传记录
            </button>
            {recordsOpen ? (
              <div className="import-records-table">
                <div className="record-row record-head" aria-hidden="true">
                  <span>文件名</span>
                  <span>大小</span>
                  <span>状态</span>
                  <span>时间</span>
                </div>
                {records.length === 0 ? (
                  <p className="empty">还没有上传记录。</p>
                ) : (
                  records.map((job) => (
                    <div key={job.job_id} className="record-row">
                      <span className="record-name" title={job.file_name}>
                        {job.file_name.split("/").pop()}
                      </span>
                      <span>{formatFileSize(job.size_bytes)}</span>
                      <span
                        className={
                          job.status === "completed" ? "record-ok" : "record-fail"
                        }
                      >
                        {job.status === "completed" ? "上传成功" : "上传失败"}
                      </span>
                      <span>{formatUploadTime(job.accepted_at)}</span>
                    </div>
                  ))
                )}
              </div>
            ) : null}
          </section>
        </section>
      </div>
    );
  }

  // ---------- 阶段 2：资料建库页 ----------
  if (phase === "library") {
    return (
      <div className="import-flow">
        <PhaseStepper phase="library" />
        <section className="import-flow-card">
          <header className="import-add-head">
            <h1>{libraryTitle}</h1>
            <p>逐张照片和视频：读取时间地点、理解画面、建立检索索引</p>
          </header>
          {errorBanner}
          <div className="import-phase-panel">
            <div className="import-phase-line">
              <span>1 / {libraryTitle}</span>
              <b>
                {batchSuccess} / {batchTotal}
              </b>
            </div>
            <div
              className={
                libraryRunning
                  ? "import-phase-bar library is-running"
                  : "import-phase-bar library"
              }
              aria-hidden="true"
            >
              <i style={{ width: `${libraryDisplay}%` }} />
            </div>
            <small>
              本次已建库 {batchSuccess} / 本次选择 {batchTotal} · 等待上传 {waitingUploads}
              {batchFailed > 0 ? ` · 上传失败 ${batchFailed}` : ""}
            </small>
          </div>
        </section>
      </div>
    );
  }

  // ---------- 阶段 3：整理回忆页 ----------
  return (
    <div className="import-flow">
      <PhaseStepper phase="postprocess" />
      <section className="import-flow-card">
        <header className="import-add-head">
          <h1>{postprocessTitle}</h1>
          <p>把资料串成事件、总结标题与描述，并维护人物关系和画像</p>
        </header>
        {errorBanner}
        <div className="import-phase-panel">
          <div className="import-phase-line">
            <span>2 / {postprocessTitle}</span>
            <b className="postprocess-value">{postprocessValue}%</b>
          </div>
          <div
            className={
              postprocessRunning
                ? "import-phase-bar postprocess is-running"
                : "import-phase-bar postprocess"
            }
            aria-hidden="true"
          >
            <i style={{ width: `${postprocessDisplay}%` }} />
          </div>
          <small>{postprocessText}</small>
        </div>
      </section>
    </div>
  );
}

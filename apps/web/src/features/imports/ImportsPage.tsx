/**
 * 导入队列。对应 1.0 `src/app.js:733-745` 的 `importsView()`。
 *
 * 版式照 1.0：左边拖放区 + 右侧处理流水线 + 下方导入记录。
 *
 * **批量与失败重试（1.0 `:1536-1587`）**：2.0 没有 1.0 的批量导入端点，所以前端按
 * 每批 20 个并发调用 `POST /media/import`。失败项**留在本次列表里**，可以只重试
 * 失败的那些；已经成功的不会被再传一遍。
 *
 * **视频处理是持久后台任务。** 上传接口保存原片和探测结果后返回 `job_id`；任务状态
 * 由 SQLite 保存，页面按任务接口恢复，不依赖浏览器请求继续存活。关键帧、场景数量仍
 * 从真实的 Asset 血缘与视频详情读取。
 *
 * 整册导入 `POST /imports` 保留原同步接口；此页面维持逐文件上传，视频处理在服务端
 * SQLite Job worker 中后台运行。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { useToast } from "../../shared/ui/Toast";
import {
  getVideo,
  getVideoJob,
  importAsset,
  listVideoJobs,
  listAssets,
  reprocessVideo,
  resumeVideoJob,
  type AssetSummary,
  type ImportResponse,
  type VideoJobResponse,
} from "../../shared/api/client";
import { IMPORT_BATCH_SIZE, runInBatches } from "./batch";
import "../../styles/imports.css";
import "./imports-extra.css";

type OutcomeStatus = "pending" | "processing" | "ok" | "failed";

type TrackedVideoJob = VideoJobResponse;

const VIDEO_JOBS_STORAGE_KEY = "sentrix.video-jobs.v1";

interface ImportEntry {
  readonly id: string;
  readonly file: File;
}

interface ImportOutcome extends ImportEntry {
  readonly name: string;
  readonly status: OutcomeStatus;
  readonly note: string;
  readonly assetId: string | null;
  readonly mediaType: string | null;
}

/** 2.0 后端的四个真实状态（`domain/media/models.py` 的 `AssetStatus`）。 */
export function assetStatusLabel(status: string): string {
  if (status === "processed") return "已完成语义整理";
  if (status === "processing") return "正在建立基础证据";
  if (status === "queued") return "排队中";
  if (status === "failed") return "处理失败，可重试";
  return status || "排队中";
}

function videoStageLabel(stage: string): string {
  return ({
    UPLOAD: "上传与校验",
    VIDEO_PROBE: "视频解析",
    SOURCE_MEMORY: "视频语义准备",
    SPEECH_TRANSCRIPTION: "语音识别",
    FRAME_SAMPLE_VSD: "抽帧与关键帧筛选",
    EVENT_BUILD: "事件构建",
    KEYFRAME_MEMORY: "关键帧记忆与向量",
    MEMORY_GRAPH: "记忆图更新",
    COMPLETED: "处理完成",
  } as Record<string, string>)[stage] ?? stage;
}

function videoStageCheckpoint(outputArtifact: string): string | null {
  try {
    const value: unknown = JSON.parse(outputArtifact);
    if (typeof value !== "object" || value === null || !("completed" in value) || !("total" in value)) {
      return null;
    }
    const completed = Number(value.completed);
    const total = Number(value.total);
    return Number.isFinite(completed) && Number.isFinite(total) && total > 0
      ? `${completed}/${total}`
      : null;
  } catch {
    return null;
  }
}

function videoJobProgressLabel(job: VideoJobResponse): string {
  const stage = job.stages.find((item) => item.stage === job.current_stage);
  const checkpoint = stage ? videoStageCheckpoint(stage.output_artifact) : null;
  return `${videoStageLabel(job.current_stage)}${checkpoint ? ` · ${checkpoint}` : ""} · ${Math.round(job.overall_progress * 100)}%`;
}

function videoJobStageStatus(job: VideoJobResponse, stage: VideoJobResponse["stages"][number]): string {
  const checkpoint = videoStageCheckpoint(stage.output_artifact);
  if (stage.status === "SUCCEEDED") return `✓ ${videoStageLabel(stage.stage)}`;
  if (stage.status === "FAILED") return `⚠ ${videoStageLabel(stage.stage)}`;
  if (stage.status === "RUNNING") {
    return `● ${videoStageLabel(stage.stage)}${checkpoint ? ` ${checkpoint}` : ""}`;
  }
  if (stage.status === "SKIPPED") return `– ${videoStageLabel(stage.stage)}（跳过）`;
  if (stage.stage === job.current_stage) return `● ${videoStageLabel(stage.stage)}`;
  return `○ ${videoStageLabel(stage.stage)}`;
}

/** 把导入响应翻成一行给用户看的话。视频的 `video_scenes` 与图片的 `indexed` 同等重要。 */
export function describeImportResult(result: ImportResponse): string {
  if (result.deduplicated) return "已存在，跳过";
  const parts: string[] = [];
  if (result.video_probed) parts.push("已读取视频元数据");
  if ((result.video_scenes ?? 0) > 0) parts.push(`已切分 ${result.video_scenes} 个场景`);
  if (result.indexed) parts.push("已写入视觉向量");
  if (result.placed) parts.push("已解析拍摄地点");
  if (parts.length > 0) return parts.join(" · ");
  return result.media_type === "image" ? "已导入，但未写视觉向量" : "已导入";
}

function outcomeFromError(entry: ImportEntry, cause: unknown): ImportOutcome {
  return {
    ...entry,
    name: entry.file.name,
    status: "failed",
    note: (cause as Error).message || String(cause),
    assetId: null,
    mediaType: null,
  };
}

export function ImportsPage() {
  const { scopeId } = useScope();
  const { notify } = useToast();
  const [busy, setBusy] = useState(0);
  const [done, setDone] = useState(0);
  const [outcomes, setOutcomes] = useState<readonly ImportOutcome[]>([]);
  const [assets, setAssets] = useState<readonly AssetSummary[]>([]);
  const [videoScenes, setVideoScenes] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [videoBusy, setVideoBusy] = useState<string | null>(null);
  const [videoJobs, setVideoJobs] = useState<readonly TrackedVideoJob[]>(() => {
    try {
      const stored = window.localStorage.getItem(VIDEO_JOBS_STORAGE_KEY);
      const parsed: unknown = stored ? JSON.parse(stored) : [];
      return Array.isArray(parsed) ? (parsed as TrackedVideoJob[]) : [];
    } catch {
      return [];
    }
  });
  const nextId = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    listAssets(scopeId)
      .then((items) => {
        setAssets(items);
        const videos = items.filter((asset) => asset.media_type === "video").slice(0, 10);
        if (videos.length === 0) {
          setVideoScenes(0);
          return;
        }
        // 场景数没有列表级端点，只能按视频逐个读详情。**限制前 10 个**：这是导入页的
        // 进度条，不是全量统计；为它拉几百条详情会让 5 秒刷新变成负担。
        void Promise.allSettled(videos.map((video) => getVideo(video.id))).then((results) => {
          if (results.some((result) => result.status === "rejected")) {
            // 有详情读不出来时显示「—」，不把部分和伪装成总数。
            setVideoScenes(null);
            return;
          }
          setVideoScenes(
            results.reduce(
              (sum, result) => sum + (result.status === "fulfilled" ? result.value.scenes.length : 0),
              0,
            ),
          );
        });
      })
      .catch(() => {
        setAssets([]);
        setVideoScenes(null);
      });
  }, [scopeId]);

  useEffect(reload, [reload]);

  useEffect(() => {
    void listVideoJobs(scopeId).then(setVideoJobs).catch(() => undefined);
  }, [scopeId]);

  useEffect(() => {
    window.localStorage.setItem(VIDEO_JOBS_STORAGE_KEY, JSON.stringify(videoJobs));
  }, [videoJobs]);

  useEffect(() => {
    const pending = videoJobs.filter((job) => !["COMPLETED", "DEGRADED", "FAILED", "CANCELLED"].includes(job.status));
    if (pending.length === 0) return;
    const timer = window.setInterval(() => {
      void Promise.allSettled(pending.map((job) => getVideoJob(job.job_id))).then((results) => {
        let completed = false;
        setVideoJobs((current) => current.map((job) => {
          const result = results.find((item) => item.status === "fulfilled" && item.value.job_id === job.job_id);
          if (!result || result.status !== "fulfilled") return job;
          if (["COMPLETED", "DEGRADED", "FAILED"].includes(result.value.status)) completed = true;
          return { ...result.value, file_name: job.file_name };
        }));
        if (completed) reload();
      });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [videoJobs, reload]);

  const importOne = useCallback(
    async (entry: ImportEntry): Promise<ImportOutcome> => {
      try {
        const result = await importAsset(entry.file, scopeId);
        if (result.job_id) {
          const initialJob: TrackedVideoJob = {
            job_id: result.job_id,
            asset_id: result.asset_id,
            status: result.job_status ?? "QUEUED",
            current_stage: "UPLOAD",
            overall_progress: 0,
            retry_count: 0,
            error_code: null,
            error_message: null,
            stages: [],
            file_name: entry.file.name,
          };
          setVideoJobs((current) => [initialJob, ...current.filter((job) => job.job_id !== initialJob.job_id)]);
        }
        return {
          ...entry,
          name: entry.file.name,
          status: result.job_id ? "processing" : "ok",
          note: result.job_id ? "视频已安全保存，后台正在处理；关闭页面也可以稍后恢复查看" : describeImportResult(result),
          assetId: result.asset_id,
          mediaType: result.media_type,
        };
      } catch (cause) {
        return outcomeFromError(entry, cause);
      }
    },
    [scopeId],
  );

  /** 分片提交；每完成一个就写回一条，列表因此是逐个亮起来的。 */
  const submitEntries = useCallback(
    async (entries: readonly ImportEntry[]) => {
      if (entries.length === 0) return;
      setBusy(entries.length);
      setDone(0);
      setOutcomes((current) => {
        const byId = new Map(current.map((item) => [item.id, item]));
        for (const entry of entries) {
          byId.set(entry.id, {
            ...entry,
            name: entry.file.name,
            status: "pending",
            note: "等待处理",
            assetId: null,
            mediaType: null,
          });
        }
        return [...byId.values()];
      });

      let failures = 0;
      await runInBatches(entries, IMPORT_BATCH_SIZE, async (entry) => {
        const outcome = await importOne(entry);
        if (outcome.status === "failed") failures += 1;
        setDone((count) => count + 1);
        setOutcomes((current) =>
          current.map((item) => (item.id === entry.id ? outcome : item)),
        );
      });

      setBusy(0);
      reload();
      const ok = entries.length - failures;
      notify(
        failures === 0
          ? `导入完成：${ok} 个文件`
          : `导入完成：${ok} 个成功，${failures} 个失败`,
        failures === 0 ? "success" : "error",
      );
    },
    [importOne, notify, reload],
  );

  const upload = useCallback(
    (files: readonly File[]) => {
      const entries = files.map((file) => ({
        id: `import-${nextId.current++}`,
        file,
      }));
      void submitEntries(entries);
    },
    [submitEntries],
  );

  /**
   * 只重试失败项。**视频且已经拿到 asset_id 的**走 `POST /videos/{id}/reprocess`
   * ——重新上传同一份文件只会因去重被跳过，派生数据仍然是坏的那一份。
   */
  const retryFailed = useCallback(async () => {
    const failed = outcomes.filter((item) => item.status === "failed");
    if (failed.length === 0) return;
    setBusy(failed.length);
    setDone(0);

    let failures = 0;
    await runInBatches(failed, IMPORT_BATCH_SIZE, async (item) => {
      let outcome: ImportOutcome;
      if (item.assetId !== null && item.mediaType === "video") {
        try {
          await reprocessVideo(item.assetId);
          outcome = { ...item, status: "ok", note: "视频处理已重新完成" };
        } catch (cause) {
          outcome = { ...item, status: "failed", note: (cause as Error).message };
        }
      } else {
        outcome = await importOne(item);
      }
      if (outcome.status === "failed") failures += 1;
      setDone((count) => count + 1);
      setOutcomes((current) =>
        current.map((existing) => (existing.id === item.id ? outcome : existing)),
      );
    });

    setBusy(0);
    reload();
    notify(
      failures === 0
        ? `重试完成：${failed.length} 个文件`
        : `重试完成：${failed.length - failures} 个成功，${failures} 个仍失败`,
      failures === 0 ? "success" : "error",
    );
  }, [importOne, notify, outcomes, reload]);

  const retryVideo = useCallback(
    async (asset: AssetSummary) => {
      setVideoBusy(asset.id);
      try {
        await reprocessVideo(asset.id);
        notify(`视频「${asset.file_name}」已重新处理`);
        reload();
      } catch (cause) {
        notify(`视频重试失败：${(cause as Error).message}`, "error");
      } finally {
        setVideoBusy(null);
      }
    },
    [notify, reload],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragging(false);
      upload(Array.from(event.dataTransfer.files));
    },
    [upload],
  );

  const failedCount = outcomes.filter((item) => item.status === "failed").length;
  const videos = assets.filter((asset) => asset.media_type === "video");
  const keyframes = assets.filter((asset) => asset.derived_kind === "video_keyframe");
  const processedKeyframes = keyframes.filter((asset) => asset.status === "processed");

  /** `busy` 时只有首段已完成，其余在跑；空闲时按有没有数据决定「完成」还是「待命」。 */
  function stageState(index: number): "done" | "active" | "idle" {
    if (busy > 0) return index === 0 ? "done" : "active";
    return assets.length > 0 ? "done" : "idle";
  }

  const pipelineRows: readonly (readonly [string, string])[] = [
    ["接收与去重", `${assets.length} 个 Asset`],
    ["读取视频元数据", `${videos.length} 个视频`],
    ["提取视频关键帧", `${keyframes.length} 张关键帧 · ${processedKeyframes.length} 张已理解`],
    [
      "切分视频场景",
      videoScenes === null ? "场景数暂不可读" : `${videoScenes} 个场景`,
    ],
    ["事件归属", "并入当天的记忆"],
    ["建立索引", "视觉与词法检索"],
  ];

  return (
    <>
      <PageHeader
        kicker="资料入口 / 本地导入"
        title="把资料带回家，剩下的交给本地 AI。"
        description="选择相册文件夹后，图片和视频会一起上传，并自动读取拍摄时间与地点。"
      />

      <section className="import-layout">
        <div>
          <label
            className="dropzone"
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            style={dragging ? { borderColor: "var(--lime)", background: "#f4fce8" } : undefined}
          >
            <input
              ref={fileInput}
              type="file"
              multiple
              accept="image/*,.heic,.heif,video/*"
              onChange={(event) => {
                upload(Array.from(event.target.files ?? []));
                // 清空，否则再次选同一个文件不会触发 change。
                event.target.value = "";
              }}
            />
            <input
              ref={folderInput}
              type="file"
              multiple
              // webkitdirectory 是目录选择的非标准属性，React 的类型里没有，用展开绕开
              {...{ webkitdirectory: "", directory: "" }}
              onChange={(event) => {
                upload(Array.from(event.target.files ?? []));
                event.target.value = "";
              }}
            />
            <span className="drop-icon" aria-hidden="true">↓</span>
            <strong>拖入照片或视频，或选择整个相册文件夹</strong>
            <small>支持 JPG / HEIC / PNG / MP4 / MOV，自动解析拍摄时间和地点</small>
            <span className="button primary">选择资料</span>
          </label>

          <div className="import-notice">
            <span className="notice-mark" aria-hidden="true">i</span>
            <div>
              <strong>原始资料不会被覆盖</strong>
              <p>
                每批最多 20 个并发提交；同一份文件重复导入会被识别出来，不会产生第二条 Asset。
                视频的每个关键画面都能跳回原片的准确时刻。
              </p>
            </div>
          </div>
        </div>

        <aside className="import-status">
          <div className="panel-title">
            <span>本地处理</span>
            <span className="live-label">
              <i />
              {busy > 0 ? `进行中 ${done}/${busy}` : "真实状态"}
            </span>
          </div>
          <h2>当前处理</h2>
          {pipelineRows.map(([title, detail], index) => {
            const state = stageState(index);
            return (
              <div key={title} className="pipeline-row">
                <span className={state === "idle" ? "pipeline-state" : `pipeline-state ${state}`}>
                  {state === "done" ? "✓" : "•"}
                </span>
                <div>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </div>
                <em>
                  {busy > 0 ? (index === 0 ? "完成" : "运行中") : state === "done" ? "完成" : "待命"}
                </em>
              </div>
            );
          })}
        </aside>
      </section>

      {videoJobs.length > 0 ? (
        <Section kicker="视频任务" title="处理进度可在刷新后恢复">
          <div className="queue-list">
            {videoJobs.slice(0, 20).map((job) => (
              <div key={job.job_id} className="queue-row">
                <span className="queue-type">视频</span>
                <div>
                  <strong>{job.file_name}</strong>
                  <small>
                    {job.status === "FAILED"
                      ? (job.error_message ?? "视频处理暂时中断，已完成步骤不会丢失。")
                      : videoJobProgressLabel(job)}
                  </small>
                  <progress max={1} value={job.overall_progress} aria-label={`${job.file_name}处理进度`} />
                  <small>{job.stages.map((stage) => videoJobStageStatus(job, stage)).join("　")}</small>
                </div>
                {job.status === "FAILED" ? (
                  <button
                    type="button"
                    className="text-button"
                    disabled={videoBusy === job.asset_id}
                    onClick={() => {
                      setVideoBusy(job.asset_id);
                      void resumeVideoJob(job.job_id)
                        .then((result) => setVideoJobs((current) => current.map((item) => item.job_id === job.job_id ? { ...result, file_name: item.file_name } : item)))
                        .finally(() => setVideoBusy(null));
                    }}
                  >
                    {videoBusy === job.asset_id ? "继续中…" : "从已完成步骤继续"}
                  </button>
                ) : (
                    <span className="queue-status queued">
                      {job.status === "COMPLETED"
                        ? "已完成"
                        : job.status === "DEGRADED"
                          ? "部分能力降级"
                          : job.status === "RETRY_WAIT"
                            ? "系统重试中"
                            : "处理中"}
                    </span>
                )}
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {outcomes.length > 0 ? (
        <Section
          kicker="本次导入"
          title={`处理了 ${outcomes.length} 个文件`}
          action={
            failedCount > 0 ? (
              <button
                type="button"
                className="text-button"
                disabled={busy > 0}
                onClick={() => void retryFailed()}
              >
                {busy > 0 ? "重试中…" : `重试失败项（${failedCount}）↻`}
              </button>
            ) : null
          }
        >
          <div className="queue-list">
            {outcomes.map((outcome) => (
              <div key={outcome.id} className="queue-row">
                <span className="queue-type">FILE</span>
                <div>
                  <strong>{outcome.name}</strong>
                  <small>{outcome.note}</small>
                </div>
                <span
                  className={
                    outcome.status === "ok"
                      ? "queue-status queued"
                      : outcome.status === "failed"
                        ? "queue-status failed"
                        : "queue-status"
                  }
                >
                  {outcome.status === "ok" ? "已处理" : outcome.status === "failed" ? "失败" : "进行中"}
                </span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section
        kicker="导入记录"
        title="最近处理任务"
        action={
          <button type="button" className="text-button" onClick={reload}>刷新状态 ↻</button>
        }
      >
        {assets.length === 0 ? (
          <p className="empty">还没有资料。拖一张照片或视频进来试试。</p>
        ) : (
          <div className="queue-list">
            {assets.slice(0, 20).map((asset) => (
              <div key={asset.id} className="queue-row">
                <span className="queue-type">{asset.media_type.slice(0, 3).toUpperCase()}</span>
                <div>
                  <strong>{asset.file_name}</strong>
                  <small>
                    {asset.captured_at ?? "未标注时间"} · {assetStatusLabel(asset.status)}
                  </small>
                </div>
                {asset.media_type === "video" && asset.status === "failed" ? (
                  <button
                    type="button"
                    className="text-button"
                    disabled={videoBusy === asset.id}
                    onClick={() => void retryVideo(asset)}
                  >
                    {videoBusy === asset.id ? "重试中…" : "重试视频处理"}
                  </button>
                ) : (
                  <span className="queue-status queued">{assetStatusLabel(asset.status)}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

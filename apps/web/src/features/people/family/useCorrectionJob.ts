/**
 * 写回执里的 `sync_job_id` 是一条后台同步任务（记忆图、因果判定、检索索引）。写入本身
 * 已提交，界面只需要在旁边提示「正在后台同步」，并在任务结束时换成结果。
 */

import { useEffect, type Dispatch, type SetStateAction } from "react";

import { getCorrectionJob, type MemorySyncResult } from "../../../shared/api/client";

export const SYNC_POLL_MS = 1200;

/** 结果是 pending 且带任务 id 时轮询它，直到 synced / failed。 */
export function useCorrectionJobPolling(
  result: MemorySyncResult | null,
  setResult: Dispatch<SetStateAction<MemorySyncResult | null>>,
) {
  const jobId = result?.sync_job_id ?? null;
  const pending = result?.sync_status === "pending";
  useEffect(() => {
    if (jobId === null || !pending) return;
    let alive = true;
    let timer: number | undefined;
    const check = async () => {
      try {
        const job = await getCorrectionJob(jobId);
        if (!alive) return;
        setResult((current) =>
          current?.sync_job_id === jobId
            ? { sync_job_id: job.id, sync_status: job.status, sync_error: job.error }
            : current,
        );
        if (job.status !== "pending") return;
      } catch {
        // 查进度失败不影响已保存的写入；下一轮再查。
      }
      if (alive) timer = window.setTimeout(() => void check(), SYNC_POLL_MS);
    };
    timer = window.setTimeout(() => void check(), SYNC_POLL_MS);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [jobId, pending, setResult]);
}

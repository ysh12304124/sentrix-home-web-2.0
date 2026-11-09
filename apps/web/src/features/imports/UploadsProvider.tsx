import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useScope } from "../../app/ScopeProvider";
import { newClientId } from "../../shared/id";
import {
  errorCodeFromStatus,
  newOperationId,
  reportClientEvent,
} from "../../shared/clientEvents";
import {
  ApiError,
  listImportJobs,
  uploadMedia,
  type ImportJobView,
} from "../../shared/api/client";

export type EntryStatus = "waiting" | "uploading" | "accepted" | "failed";

export interface UploadEntry {
  key: string;
  file: File;
  status: EntryStatus;
  jobId: string;
  error: string;
}

interface UploadsValue {
  entries: UploadEntry[];
  uploadNotice: string;
  resumeNotice: string;
  uploadRevision: number;
  startUploads: (files: File[]) => Promise<void>;
  retryUpload: (entry: UploadEntry) => Promise<void>;
}

const MAX_CONCURRENT = 2;
const UPLOAD_TIMEOUT_MS = 120_000;
const UPLOAD_ATTEMPTS = 3;
const PENDING_UPLOAD_KEY = "sentrix.upload-pending.v1";
const ALREADY_UPLOADED_STATUSES = new Set([
  "accepted",
  "queued",
  "running",
  "waiting_dependency",
  "retry_scheduled",
  "completed",
]);

function keyFor(_file: File): string {
  // HTTP 头只能带 ISO-8859-1；中文文件名绝不能进 Idempotency-Key。
  return newClientId("upload");
}

const UploadsContext = createContext<UploadsValue | null>(null);

/**
 * 上传队列放在壳层而不是导入页里。
 *
 * 导入页只是队列的一个视图：用户切到回忆/问答时，这个 Provider 不会卸载，
 * 已选文件、待传队列和重试状态都继续存在。刷新/关闭页面仍会终止浏览器上传，
 * 所以另有 beforeunload 提醒与本地待传清单。
 */
export function UploadsProvider({ children }: { children: ReactNode }) {
  const { scopeId } = useScope();
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [uploadNotice, setUploadNotice] = useState("");
  const [uploadRevision, setUploadRevision] = useState(0);
  const [resumeNotice, setResumeNotice] = useState(() => {
    try {
      const raw = window.localStorage.getItem(PENDING_UPLOAD_KEY);
      const pending: unknown = raw ? JSON.parse(raw) : [];
      return Array.isArray(pending) && pending.length > 0
        ? `上次还有 ${pending.length} 个文件未完成上传。重新选择同一文件夹会跳过已上传部分。`
        : "";
    } catch {
      return "";
    }
  });

  const updateEntry = useCallback((key: string, patch: Partial<UploadEntry>) => {
    setEntries((previous) =>
      previous.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)),
    );
  }, []);

  const uploadOne = useCallback(
    async (entry: UploadEntry): Promise<"accepted" | "failed"> => {
      updateEntry(entry.key, { status: "uploading", error: "" });
      try {
        void reportClientEvent({
          eventName: "import.upload_start",
          result: "started",
          operationId: entry.key,
          scopeId: "",
        });
        let lastError: unknown = null;
        for (let attempt = 0; attempt < UPLOAD_ATTEMPTS; attempt += 1) {
          const controller = new AbortController();
          const timer = window.setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
          try {
            const accepted = await uploadMedia(entry.file, "", entry.key, controller.signal);
            updateEntry(entry.key, { status: "accepted", jobId: accepted.job_id });
            setUploadRevision((value) => value + 1);
            void reportClientEvent({
              eventName: "import.upload_accepted",
              result: "accepted",
              operationId: entry.key,
              scopeId: "",
              jobId: accepted.job_id,
              assetId: accepted.asset_id,
            });
            return "accepted";
          } catch (cause) {
            lastError = cause;
            // 4xx 是服务端明确拒绝，重试不会变好；网络中断、超时、429/5xx 才重试。
            const retryable =
              !(cause instanceof ApiError) ||
              cause.status === 429 ||
              cause.status >= 500;
            if (!retryable || attempt === UPLOAD_ATTEMPTS - 1) throw cause;
            await new Promise((resolve) => window.setTimeout(resolve, 800 * (attempt + 1)));
          } finally {
            window.clearTimeout(timer);
          }
        }
        throw lastError instanceof Error ? lastError : new Error("上传失败");
      } catch (cause) {
        const message = (cause as Error).message;
        updateEntry(entry.key, { status: "failed", error: message });
        setUploadRevision((value) => value + 1);
        void reportClientEvent({
          eventName: "import.upload_failed",
          result: "failed",
          operationId: entry.key,
          scopeId: "",
          errorCode: cause instanceof ApiError ? errorCodeFromStatus(cause.status) : "network",
        });
        return "failed";
      }
    },
    [updateEntry],
  );

  const startUploads = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setUploadNotice("");
      setResumeNotice("");
      try {
        // 重新选择同一文件夹时，跳过服务端已经有作业的文件。这样刷新/中途
        // 停止后补选整个文件夹不会重复建作业，也不会把已上传部分再传一遍。
        let currentJobs: ImportJobView[] = [];
        try {
          currentJobs = await listImportJobs(scopeId);
        } catch {
          // 读不到作业列表时保守地全部上传；服务端仍会按内容去重。
        }
        const uploaded = new Set(
          currentJobs
            .filter((job) => ALREADY_UPLOADED_STATUSES.has(job.status))
            .map((job) => `${job.file_name.split("/").pop()} ${job.size_bytes}`),
        );
        const selected = files.filter(
          (file) => !uploaded.has(`${file.name} ${file.size}`),
        );
        const skipped = files.length - selected.length;
        if (selected.length === 0) {
          setUploadNotice("这些文件都已经上传过了。");
          try {
            window.localStorage.removeItem(PENDING_UPLOAD_KEY);
          } catch {
            // 存储不可用时不影响当前页面。
          }
          return;
        }
        setUploadNotice(
          `本次选择 ${files.length} 个文件` +
            (skipped > 0 ? `，跳过已上传 ${skipped} 个` : "") +
            `，准备上传 ${selected.length} 个。`,
        );
        try {
          window.localStorage.setItem(
            PENDING_UPLOAD_KEY,
            JSON.stringify(selected.map((file) => ({ name: file.name, size: file.size }))),
          );
        } catch {
          // 存储不可用时至少还有 beforeunload 提醒。
        }
        const additions: UploadEntry[] = selected.map((file) => ({
          key: keyFor(file),
          file,
          status: "waiting",
          jobId: "",
          error: "",
        }));
        setEntries((previous) => [...additions, ...previous]);
        void reportClientEvent({
          eventName: "import.pick",
          result: "ok",
          operationId: newOperationId(),
          scopeId: "",
        });
        const queue = [...additions];
        let failures = 0;
        await Promise.all(
          Array.from({ length: Math.min(MAX_CONCURRENT, queue.length) }, async () => {
            while (queue.length > 0) {
              const next = queue.shift();
              if (next !== undefined && (await uploadOne(next)) === "failed") failures += 1;
            }
          }),
        );
        if (failures === 0) {
          setUploadNotice(`本次 ${selected.length} 个文件已全部提交。`);
          try {
            window.localStorage.removeItem(PENDING_UPLOAD_KEY);
          } catch {
            // 存储不可用时忽略。
          }
        } else {
          setUploadNotice(
            `本次上传结束，有 ${failures} 个文件失败；重新选择同一文件夹会只补传失败项。`,
          );
        }
      } catch (cause) {
        setUploadNotice(`上传没有开始：${(cause as Error).message}`);
      }
    },
    [scopeId, uploadOne],
  );

  const retryUpload = useCallback(
    async (entry: UploadEntry) => {
      await uploadOne(entry);
    },
    [uploadOne],
  );

  useEffect(() => {
    const hasPending = entries.some(
      (entry) => entry.status === "waiting" || entry.status === "uploading",
    );
    if (!hasPending) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [entries]);

  const value = useMemo<UploadsValue>(
    () => ({
      entries,
      uploadNotice,
      resumeNotice,
      uploadRevision,
      startUploads,
      retryUpload,
    }),
    [entries, resumeNotice, retryUpload, startUploads, uploadNotice, uploadRevision],
  );
  return <UploadsContext.Provider value={value}>{children}</UploadsContext.Provider>;
}

export function useUploads(): UploadsValue {
  const value = useContext(UploadsContext);
  if (value === null) throw new Error("useUploads 必须在 UploadsProvider 内使用");
  return value;
}

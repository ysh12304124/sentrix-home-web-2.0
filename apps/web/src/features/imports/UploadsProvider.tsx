import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { newClientId } from "../../shared/id";
import {
  errorCodeFromStatus,
  newOperationId,
  reportClientEvent,
} from "../../shared/clientEvents";
import { ApiError, uploadMedia } from "../../shared/api/client";
import {
  listPending,
  removePending,
  savePending,
  type PendingUploadRecord,
} from "./pendingUploads";

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

const DESKTOP_CONCURRENT = 2;
const MOBILE_CONCURRENT = 1;
const UPLOAD_TIMEOUT_MS = 180_000;
const UPLOAD_ATTEMPTS = 3;

function keyFor(_file: File): string {
  // HTTP 头只能带 ISO-8859-1；中文文件名绝不能进 Idempotency-Key。
  return newClientId("upload");
}

function pendingRecord(
  entry: UploadEntry,
  status: "waiting" | "failed" = "waiting",
  error = "",
): PendingUploadRecord {
  return {
    key: entry.key,
    name: entry.file.name,
    type: entry.file.type,
    lastModified: entry.file.lastModified,
    status,
    error,
    blob: entry.file,
  };
}

function restoredEntry(record: PendingUploadRecord): UploadEntry {
  const file =
    record.blob instanceof File
      ? record.blob
      : new File([record.blob], record.name, {
          type: record.type,
          lastModified: record.lastModified,
        });
  return {
    key: record.key,
    file,
    status: record.status === "failed" ? "failed" : "waiting",
    jobId: "",
    error: record.error,
  };
}

function mobileBrowser(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
}

const UploadsContext = createContext<UploadsValue | null>(null);

/**
 * 上传队列放在壳层而不是导入页里。
 *
 * 导入页只是队列的一个视图：用户切到回忆/问答时，这个 Provider 不会卸载，
 * 已选文件、待传队列和重试状态都继续存在。文件本身另存一份到 IndexedDB，
 * 手机切后台/刷新后回到页面会用同一个幂等键继续上传。
 */
export function UploadsProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [uploadNotice, setUploadNotice] = useState("");
  const [uploadRevision, setUploadRevision] = useState(0);
  const [resumeNotice, setResumeNotice] = useState("");
  const resumed = useRef(false);
  const concurrency = mobileBrowser() ? MOBILE_CONCURRENT : DESKTOP_CONCURRENT;

  const updateEntry = useCallback((key: string, patch: Partial<UploadEntry>) => {
    setEntries((previous) =>
      previous.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)),
    );
  }, []);

  const uploadOne = useCallback(
    async (entry: UploadEntry): Promise<"accepted" | "failed"> => {
      updateEntry(entry.key, { status: "uploading", error: "" });
      void savePending([pendingRecord(entry, "waiting")]);
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
            void removePending([entry.key]);
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
        void savePending([pendingRecord(entry, "failed", message)]);
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

  const runQueue = useCallback(
    async (queue: UploadEntry[]): Promise<number> => {
      let failures = 0;
      const pending = [...queue];
      await Promise.all(
        Array.from({ length: Math.min(concurrency, pending.length) }, async () => {
          while (pending.length > 0) {
            const next = pending.shift();
            if (next !== undefined && (await uploadOne(next)) === "failed") failures += 1;
          }
        }),
      );
      return failures;
    },
    [concurrency, uploadOne],
  );

  // 回到页面时恢复上次没传完的文件：失败的保留给用户手动重试，等待中的直接续传。
  useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;
    let cancelled = false;
    void listPending().then((records) => {
      if (cancelled || records.length === 0) return;
      const restored = records.map(restoredEntry);
      setEntries((previous) => {
        const existing = new Set(previous.map((entry) => entry.key));
        return [...restored.filter((entry) => !existing.has(entry.key)), ...previous];
      });
      const waiting = restored.filter((entry) => entry.status === "waiting");
      const failed = restored.length - waiting.length;
      setResumeNotice(
        `已恢复上次未完成的上传：${waiting.length} 个继续上传` +
          (failed > 0 ? `，${failed} 个失败可重试` : "") +
          "。",
      );
      if (waiting.length > 0) void runQueue(waiting);
    });
    return () => {
      cancelled = true;
    };
  }, [runQueue]);

  const startUploads = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setUploadNotice("");
      setResumeNotice("");
      const additions: UploadEntry[] = files.map((file) => ({
        key: keyFor(file),
        file,
        status: "waiting",
        jobId: "",
        error: "",
      }));
      // 先把队列摆到页面上：手机原生选择器关闭后立即有反馈，不让用户等网络查询。
      setEntries((previous) => [...additions, ...previous]);
      setUploadNotice(`本次选择 ${files.length} 个文件，正在开始上传。`);
      void reportClientEvent({
        eventName: "import.pick",
        result: "ok",
        operationId: newOperationId(),
        scopeId: "",
      });
      try {
        await savePending(additions.map((entry) => pendingRecord(entry)));
      } catch {
        // IndexedDB 不可用（隐私模式/配额满）时仍继续当前页面上传。
      }
      const failures = await runQueue(additions);
      if (failures === 0) {
        setUploadNotice(`本次 ${files.length} 个文件已全部提交。`);
      } else {
        setUploadNotice(
          `本次上传结束，有 ${failures} 个文件失败；可在本次上传列表里重试。`,
        );
      }
    },
    [runQueue],
  );

  const retryUpload = useCallback(
    async (entry: UploadEntry) => {
      await uploadOne(entry);
    },
    [uploadOne],
  );

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

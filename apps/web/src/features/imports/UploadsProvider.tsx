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

import { useScope } from "../../app/ScopeProvider";
import { newClientId } from "../../shared/id";
import {
  errorCodeFromStatus,
  newOperationId,
  reportClientEvent,
} from "../../shared/clientEvents";
import {
  ApiError,
  fetchImportProgress,
  listImportJobs,
  uploadMedia,
  type ImportJobView,
  type ImportProgress,
} from "../../shared/api/client";

export type EntryStatus = "waiting" | "uploading" | "accepted" | "failed";

/** 一条独立计时的上传提示：新条追加在旧条下方，各自 5 秒后消失。 */
export interface UploadNotice {
  id: number;
  text: string;
}

export interface UploadEntry {
  key: string;
  file: File;
  status: EntryStatus;
  jobId: string;
  error: string;
  /** 属于哪个进度窗口；关闭建库卡后开新窗口，统计从 0 重新开始。 */
  windowSeq: number;
}

interface UploadsValue {
  entries: UploadEntry[];
  /** 当前窗口内的条目（本次选择 + 上一批未完成一起累加的批次）。 */
  windowEntries: UploadEntry[];
  /** 当前窗口序号；整理卡的显示锁存靠它判断是不是真换了新批次。 */
  windowSeq: number;
  libraryDismissed: boolean;
  dismissLibraryCard: () => void;
  /** 回忆卡的关闭状态挂在这里，跨路由切换不会被页面卸载重置。 */
  postprocessDismissed: boolean;
  dismissPostprocessCard: () => void;
  /** 新一轮整理开始时把卡片重新打开（ImportedPage 的 state 观察 effect 调用）。 */
  reopenPostprocessCard: () => void;
  /** 回忆卡"本窗口已展示过"的锁存，跨路由保持，避免切回来 latch 丢失。 */
  postprocessShownSeq: number | null;
  setPostprocessShownSeq: (seq: number | null) => void;
  /** 导入进度快照：切到别的页面再回来时，页面不该从 0 重新渲染进度条。 */
  jobs: ImportJobView[];
  progress: ImportProgress | null;
  assetTotal: number | null;
  setImportData: (next: {
    jobs: ImportJobView[];
    progress: ImportProgress;
    assetTotal?: number | null;
  }) => void;
  /** 可堆叠的上传提示，新条在旧条下方，每条独立 5 秒消失。 */
  uploadNotices: UploadNotice[];
  resumeNotice: string;
  uploadRevision: number;
  /** 本批次统计起点：后端作业 receive_seq 大于它的才算「本次」。null=首次访问尚未确定。 */
  receiveSeqBase: number | null;
  startUploads: (files: File[]) => Promise<void>;
  retryUpload: (entry: UploadEntry) => Promise<void>;
}

const MAX_CONCURRENT = 2;
const UPLOAD_TIMEOUT_MS = 120_000;
const UPLOAD_ATTEMPTS = 3;
const PENDING_UPLOAD_KEY = "sentrix.upload-pending.v1";
const RECEIVE_SEQ_BASE_KEY = "sentrix.receive-seq-base.v1";
const ALREADY_UPLOADED_STATUSES = new Set([
  "accepted",
  "queued",
  "running",
  "waiting_dependency",
  "retry_scheduled",
  "completed",
]);
// 与 ImportsPage 的 ACTIVE_JOB_STATUSES 一致：这些都算「上一批还没干完」。
const ACTIVE_JOB_STATUSES = new Set([
  "accepted",
  "queued",
  "running",
  "waiting_dependency",
  "retry_scheduled",
]);
const ACTIVE_POSTPROCESS_STATES = new Set(["pending", "running", "waiting_retry"]);

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
  const [windowSeq, setWindowSeq] = useState(0);
  const [libraryDismissed, setLibraryDismissed] = useState(false);
  const [postprocessDismissed, setPostprocessDismissed] = useState(false);
  const [postprocessShownSeq, setPostprocessShownSeq] = useState<number | null>(null);
  // 进度快照放 Provider：页面卸载再挂载不该把进度条打回 0，再靠 300ms 过渡冲上去。
  const [jobs, setJobs] = useState<ImportJobView[]>([]);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [assetTotal, setAssetTotal] = useState<number | null>(null);
  // 提示条是队列：新条追加在末尾（显示在旧条下方），每条自己到点消失、互不覆盖。
  const [uploadNotices, setUploadNotices] = useState<UploadNotice[]>([]);
  const noticeIdRef = useRef(0);
  const noticeTimers = useRef<Map<number, number>>(new Map());
  // 本批次统计起点。localStorage 让刷新/切页回来后仍从同一起点重算后端作业，
  // 统计数字与「没退出、正常返回」时一致；null 表示尚无上传批次（不计任何历史作业）。
  const [receiveSeqBase, setReceiveSeqBase] = useState<number | null>(() => {
    try {
      const raw = window.localStorage.getItem(RECEIVE_SEQ_BASE_KEY);
      if (raw === null) return null;
      const parsed = Number(raw);
      return Number.isFinite(parsed) ? parsed : null;
    } catch {
      return null;
    }
  });
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

  // 提示条互不覆盖：新条追加到末尾（渲染在旧条下方），各自 5 秒到点只移除自己。
  // flashNotice("") 用于开新任务时清空整列；刷新页面本就不持久化。
  const flashNotice = useCallback((message: string) => {
    if (message === "") {
      noticeTimers.current.forEach((timer) => window.clearTimeout(timer));
      noticeTimers.current.clear();
      setUploadNotices([]);
      return;
    }
    const id = (noticeIdRef.current += 1);
    setUploadNotices((previous) => [...previous, { id, text: message }]);
    const timer = window.setTimeout(() => {
      setUploadNotices((previous) => previous.filter((notice) => notice.id !== id));
      noticeTimers.current.delete(id);
    }, 5000);
    noticeTimers.current.set(id, timer);
  }, []);

  useEffect(
    () => () => {
      noticeTimers.current.forEach((timer) => window.clearTimeout(timer));
      noticeTimers.current.clear();
    },
    [],
  );

  // 续传提示只在「上传中断后的第一次进入」出现一次：显示后立刻清掉待传清单，
  // 这样再刷新页面就不会从 localStorage 重新生成这条提示（下次上传开始也会清空）。
  useEffect(() => {
    if (resumeNotice === "") return;
    try {
      window.localStorage.removeItem(PENDING_UPLOAD_KEY);
    } catch {
      // 存储不可用时忽略：本次提示照常显示，最多次刷新重复一次。
    }
  }, [resumeNotice]);

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
      flashNotice("");
      setResumeNotice("");
      // 先读后端状态再决定要不要开新窗口（条目19）。
      // 只看前端 waiting/uploading 会在「文件已 accepted、作业还在后台跑」时误判成
      // 上一批已结束：一开新窗口，windowEntries 换成新文件，分母重置、旧卡片被顶掉。
      let currentJobs: ImportJobView[] = [];
      try {
        currentJobs = await listImportJobs(scopeId);
      } catch {
        // 读不到作业列表时保守地全部上传；服务端仍会按内容去重。
      }
      let postprocessActive = false;
      try {
        const progress = await fetchImportProgress(scopeId);
        postprocessActive = ACTIVE_POSTPROCESS_STATES.has(progress.postprocess.state);
      } catch {
        // 读不到进度时按不活跃处理，沿用原来的窗口判定。
      }
      const previousPending = entries.some(
        (entry) => entry.status === "waiting" || entry.status === "uploading",
      );
      const backendBusy =
        currentJobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status)) || postprocessActive;
      // 手动关掉建库卡、或前后端都确认上一批已落定，才开新窗口；
      // 上一批未结束又传新图则沿用当前 windowSeq —— 分母累加、进度允许倒退。
      const shouldOpenWindow = libraryDismissed || (!previousPending && !backendBusy);
      const nextWindowSeq = shouldOpenWindow ? windowSeq + 1 : windowSeq;
      if (shouldOpenWindow) {
        setWindowSeq(nextWindowSeq);
        setLibraryDismissed(false);
        // 新任务起点 = 下单前后端已有的最大 receive_seq。新作业号更大，统计从零开始。
        // 窗口合并（旧任务未完）时不改起点，仍按第一批累计。
        const nextBase = currentJobs.reduce(
          (max, job) => Math.max(max, job.receive_seq),
          0,
        );
        setReceiveSeqBase(nextBase);
        try {
          window.localStorage.setItem(RECEIVE_SEQ_BASE_KEY, String(nextBase));
        } catch {
          // 存储不可用时仍保留内存里的起点（本次会话内一致）。
        }
      }
      try {
        // 重新选择同一文件夹时，跳过服务端已经有作业的文件。这样刷新/中途
        // 停止后补选整个文件夹不会重复建作业，也不会把已上传部分再传一遍。
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
          flashNotice("这些文件都已经上传过了。");
          try {
            window.localStorage.removeItem(PENDING_UPLOAD_KEY);
          } catch {
            // 存储不可用时不影响当前页面。
          }
          return;
        }
        flashNotice(
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
          windowSeq: nextWindowSeq,
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
          flashNotice(`本次 ${selected.length} 个文件已全部提交。`);
          try {
            window.localStorage.removeItem(PENDING_UPLOAD_KEY);
          } catch {
            // 存储不可用时忽略。
          }
        } else {
          flashNotice(
            `本次上传结束，有 ${failures} 个文件失败；重新选择同一文件夹会只补传失败项。`,
          );
        }
      } catch (cause) {
        flashNotice(`上传没有开始：${(cause as Error).message}`);
      }
    },
    [scopeId, uploadOne, libraryDismissed, windowSeq, entries, flashNotice],
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

  const windowEntries = useMemo(
    () => entries.filter((entry) => entry.windowSeq === windowSeq),
    [entries, windowSeq],
  );

  const dismissLibraryCard = useCallback(() => setLibraryDismissed(true), []);
  const dismissPostprocessCard = useCallback(() => setPostprocessDismissed(true), []);
  const reopenPostprocessCard = useCallback(() => setPostprocessDismissed(false), []);
  const setImportData = useCallback(
    (next: {
      jobs: ImportJobView[];
      progress: ImportProgress;
      assetTotal?: number | null;
    }) => {
      setJobs(next.jobs);
      setProgress(next.progress);
      // 取资产数失败时保持原值，别把已有资料误判成"空库"。
      if (next.assetTotal !== undefined) setAssetTotal(next.assetTotal);
      // 首次进入（本地无起点）时，把当前历史作业的最大号设为起点：
      // 老作业不算「本次」，统计从 0 开始。之后由 startUploads 在新任务时推进。
      setReceiveSeqBase((previous) => {
        if (previous !== null) return previous;
        const max = next.jobs.reduce((value, job) => Math.max(value, job.receive_seq), 0);
        try {
          window.localStorage.setItem(RECEIVE_SEQ_BASE_KEY, String(max));
        } catch {
          // 忽略存储不可用。
        }
        return max;
      });
    },
    [],
  );

  const value = useMemo<UploadsValue>(
    () => ({
      entries,
      windowEntries,
      windowSeq,
      libraryDismissed,
      dismissLibraryCard,
      postprocessDismissed,
      dismissPostprocessCard,
      reopenPostprocessCard,
      postprocessShownSeq,
      setPostprocessShownSeq,
      jobs,
      progress,
      assetTotal,
      setImportData,
      uploadNotices,
      resumeNotice,
      uploadRevision,
      receiveSeqBase,
      startUploads,
      retryUpload,
    }),
    [
      entries,
      windowEntries,
      windowSeq,
      libraryDismissed,
      dismissLibraryCard,
      postprocessDismissed,
      dismissPostprocessCard,
      reopenPostprocessCard,
      postprocessShownSeq,
      jobs,
      progress,
      assetTotal,
      setImportData,
      resumeNotice,
      retryUpload,
      startUploads,
      uploadNotices,
      uploadRevision,
      receiveSeqBase,
    ],
  );
  return <UploadsContext.Provider value={value}>{children}</UploadsContext.Provider>;
}

export function useUploads(): UploadsValue {
  const value = useContext(UploadsContext);
  if (value === null) throw new Error("useUploads 必须在 UploadsProvider 内使用");
  return value;
}

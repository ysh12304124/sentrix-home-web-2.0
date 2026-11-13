import { useEffect, useState } from "react";
import { listLibraryDeletions, retryLibraryDeletion, type LibraryDeletion } from "../../shared/api/client";

export function deletionLabel(task: LibraryDeletion): string {
  if (task.lifecycle_state === "completed") return "已删除";
  if (task.library_state === "pending") return "删除中";
  if (task.library_state === "blocked" || task.lifecycle_state === "blocked") return "处理受阻";
  if (task.library_state === "failed" || task.lifecycle_state === "failed") return "处理失败";
  if (task.lifecycle_state === "rebuilding") return "记忆更新中";
  return "删除中";
}

export function DeletionProgress({ scopeId, revision }: { scopeId: string; revision: number }) {
  const [tasks, setTasks] = useState<LibraryDeletion[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [retryError, setRetryError] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await listLibraryDeletions(scopeId, controller.signal);
        if (controller.signal.aborted) return;
        setTasks(next);
        setLoadError(false);
      } catch {
        if (controller.signal.aborted) return;
        setLoadError(true);
      }
      timer = setTimeout(() => void poll(), 2500);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [scopeId, revision, refresh]);

  async function retry(task: LibraryDeletion) {
    setRetrying(task.deletion_id);
    setRetryError(false);
    try {
      await retryLibraryDeletion(task.deletion_id, task.scope_id);
      setRefresh((value) => value + 1);
    } catch {
      setRetryError(true);
    } finally {
      setRetrying(null);
    }
  }

  if (!tasks.length && !loadError) return null;
  return (
    <section className="library-deletion-progress" aria-label="删除记录">
      <details open>
        <summary>删除记录</summary>
        {loadError && <p role="status">暂时无法读取删除进度，稍后会自动刷新。</p>}
        {retryError && <p role="alert">未能重试，请稍后再试。</p>}
        <ul aria-live="polite">
          {tasks.map((task) => (
            <li key={task.deletion_id}>
              <strong>{deletionLabel(task)}</strong>
              {" · 资料编号 "}{task.root_asset_id}
              {task.lifecycle_state !== "completed" && (task.library_state === "failed" || task.library_state === "blocked") && (
                <button type="button" disabled={retrying !== null} onClick={() => void retry(task)}>
                  重试
                </button>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

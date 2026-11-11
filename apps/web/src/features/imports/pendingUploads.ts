/**
 * 待上传文件在 IndexedDB 里的持久副本。
 *
 * 手机切到别的 App、刷新或浏览器回收页面时，内存里的 File 会丢；只留
 * localStorage 的名字/大小只能提示「上次还有 N 个」，不能继续传。这里把 Blob
 * 和幂等键一起存下来，回到页面后用同一个 key 继续上传，服务端仍按幂等键去重。
 */

const DB_NAME = "sentrix-pending-uploads";
const STORE = "pending";
const DB_VERSION = 1;

export interface PendingUploadRecord {
  key: string;
  name: string;
  type: string;
  lastModified: number;
  status: "waiting" | "failed";
  error: string;
  blob: Blob;
}

function openDatabase(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    try {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "key" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function transactionDone(transaction: IDBTransaction): Promise<boolean> {
  return new Promise((resolve) => {
    transaction.oncomplete = () => resolve(true);
    transaction.onerror = () => resolve(false);
    transaction.onabort = () => resolve(false);
  });
}

export async function savePending(records: readonly PendingUploadRecord[]): Promise<void> {
  if (records.length === 0) return;
  const db = await openDatabase();
  if (db === null) return;
  try {
    const transaction = db.transaction(STORE, "readwrite");
    const store = transaction.objectStore(STORE);
    for (const record of records) store.put(record);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function removePending(keys: readonly string[]): Promise<void> {
  if (keys.length === 0) return;
  const db = await openDatabase();
  if (db === null) return;
  try {
    const transaction = db.transaction(STORE, "readwrite");
    const store = transaction.objectStore(STORE);
    for (const key of keys) store.delete(key);
    await transactionDone(transaction);
  } finally {
    db.close();
  }
}

export async function listPending(): Promise<PendingUploadRecord[]> {
  const db = await openDatabase();
  if (db === null) return [];
  try {
    return await new Promise((resolve) => {
      const transaction = db.transaction(STORE, "readonly");
      const request = transaction.objectStore(STORE).getAll();
      request.onsuccess = () => resolve((request.result as PendingUploadRecord[]) ?? []);
      request.onerror = () => resolve([]);
    });
  } finally {
    db.close();
  }
}

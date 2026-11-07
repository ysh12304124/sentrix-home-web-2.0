/**
 * 相册范围在界面上已取消：体验入口只有一份资料，读全部写入默认相册。
 *
 * 这里保留 provider 与既有导出，是为了让六页不必各自改参数；`scopeId` 恒为空串，
 * 与 API 的「全部相册」一致。旧的 `?scope=` 与按账号保存的选择会清掉。
 */

import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";

import type { MemorySpace } from "../shared/api/client";

const LEGACY_STORAGE_KEY = "sentrix.scopeId";
const ACCOUNT_STORAGE_PREFIX = "sentrix.scopeId.";
/** 「全部相册」的表示。1.0 用空串，2.0 沿用。 */
export const ALL_SCOPES = "";

interface ScopeValue {
  readonly scopeId: string;
  readonly spaces: readonly MemorySpace[];
  readonly setScopeId: (next: string) => void;
  readonly spacesError: string | null;
}

const ScopeContext = createContext<ScopeValue | null>(null);

/** 保留给旧测试的纯函数；界面不再使用相册选择。 */
export function resolveStoredScope(storedId: string, spaces: readonly MemorySpace[]): string {
  const stored = spaces.find((space) => space.id === storedId);
  return stored?.kind === "benchmark" ? stored.id : ALL_SCOPES;
}

export function scopeStorageKey(accountId: string | null, legacy: boolean): string | null {
  if (accountId !== null) return `${ACCOUNT_STORAGE_PREFIX}${accountId}`;
  return legacy ? LEGACY_STORAGE_KEY : null;
}

function clearLegacyScopeState(): void {
  try {
    const url = new URL(window.location.href);
    if (url.searchParams.has("scope")) {
      url.searchParams.delete("scope");
      window.history.replaceState(null, "", url);
    }
    for (const key of Object.keys(window.localStorage)) {
      if (key === LEGACY_STORAGE_KEY || key.startsWith(ACCOUNT_STORAGE_PREFIX)) {
        window.localStorage.removeItem(key);
      }
    }
  } catch {
    // 存储或 URL 不可用时不影响页面。
  }
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    clearLegacyScopeState();
  }, []);

  const setScopeId = useCallback((_next: string) => undefined, []);

  const value = useMemo<ScopeValue>(
    () => ({ scopeId: ALL_SCOPES, spaces: [], setScopeId, spacesError: null }),
    [setScopeId],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeValue {
  const value = useContext(ScopeContext);
  if (value === null) throw new Error("useScope 必须在 ScopeProvider 内使用");
  return value;
}

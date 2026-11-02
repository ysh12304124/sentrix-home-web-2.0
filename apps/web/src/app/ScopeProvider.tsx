/**
 * 相册范围（scope）的单一来源。
 *
 * **语义逐条移植 1.0 `src/app.js:1162-1172`（初始化）与 `:1247`（切换写回）**。
 *
 * **回落是两级，不是一句「不存在就回落」。** `:1168` 的实际写法是
 * `state.scopeId = storedSpace?.kind === "benchmark" ? storedSpace.id : ""`：
 *
 * 1. 存的 id 在列表里找不到 → `""`（全部相册）；
 * 2. **找得到但 `kind !== "benchmark"` → 也回落 `""`**。
 *
 * 第二种是 1.0 有意为之（注释写明是把旧的家庭选择迁到「全部相册」）。按字面实现
 * 「只判存在性」会漏掉它，所以那一条单独有用例骑住。
 *
 * 体验入口多两件事：存储键按登录账号隔开（`sentrix.scopeId.<account_id>`），
 * 且 URL 里的 scope 必须在本账号相册列表内，否则回退到「全部相册」。
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { listMemorySpaces, type MemorySpace } from "../shared/api/client";

const LEGACY_STORAGE_KEY = "sentrix.scopeId";
const ACCOUNT_STORAGE_PREFIX = "sentrix.scopeId.";
/** 「全部相册」的表示。1.0 用空串，2.0 沿用——它同时是 query 省略与 API 不传参的约定。 */
export const ALL_SCOPES = "";

interface ScopeValue {
  readonly scopeId: string;
  readonly spaces: readonly MemorySpace[];
  readonly setScopeId: (next: string) => void;
  /** 相册列表读失败的原因（后端还没起、或路由缺失）；非 null 时切换器只显示「全部相册」。 */
  readonly spacesError: string | null;
}

const ScopeContext = createContext<ScopeValue | null>(null);

/** 1.0 `:1168` 的判定，原样搬来并单独成函数——它有用例，不该埋在组件里。 */
export function resolveStoredScope(storedId: string, spaces: readonly MemorySpace[]): string {
  const stored = spaces.find((space) => space.id === storedId);
  return stored?.kind === "benchmark" ? stored.id : ALL_SCOPES;
}

/**
 * 体验入口的存储键。`null` 表示当前身份不可用，不读也不写任何 scope——
 * 避免把上一个账号的选择带给下一个账号。
 */
export function scopeStorageKey(accountId: string | null, legacy: boolean): string | null {
  if (accountId !== null) return `${ACCOUNT_STORAGE_PREFIX}${accountId}`;
  return legacy ? LEGACY_STORAGE_KEY : null;
}

function readQueryScope(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("scope");
}

function clearQueryScope(): void {
  const url = new URL(window.location.href);
  url.searchParams.delete("scope");
  window.history.replaceState(null, "", url);
}

interface Identity {
  readonly accountId: string | null;
  /** 控制服务不存在（旧 8191）；保留 1.0 的存储键，旧实例行为不变。 */
  readonly legacy: boolean;
}

function isControlSession(response: Response): boolean {
  if (response.status === 401 || response.status === 403) return true;
  return response.ok && (response.headers.get("content-type") ?? "").includes("application/json");
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [spaces, setSpaces] = useState<readonly MemorySpace[]>([]);
  const [spacesError, setSpacesError] = useState<string | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [scopeId, setScopeIdState] = useState<string>(ALL_SCOPES);

  useEffect(() => {
    let cancelled = false;
    fetch("/control/v1/auth/me", { credentials: "same-origin" })
      .then(async (response) => {
        if (cancelled) return;
        if (!isControlSession(response)) {
          setIdentity({ accountId: null, legacy: true });
          return;
        }
        if (!response.ok) {
          setIdentity({ accountId: null, legacy: false });
          return;
        }
        const body = (await response.json()) as { account_id?: unknown };
        const accountId = typeof body.account_id === "string" ? body.account_id : null;
        setIdentity({ accountId, legacy: false });
      })
      .catch(() => {
        if (!cancelled) setIdentity({ accountId: null, legacy: true });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (identity === null) return;
    let cancelled = false;
    const storageKey = scopeStorageKey(identity.accountId, identity.legacy);
    listMemorySpaces()
      .then((items) => {
        if (cancelled) return;
        setSpaces(items);
        setSpacesError(null);
        const queryScope = readQueryScope();
        if (queryScope !== null) {
          const resolved = resolveStoredScope(queryScope, items);
          if (resolved === ALL_SCOPES) clearQueryScope();
          setScopeIdState(resolved);
          return;
        }
        const stored = storageKey === null ? "" : (window.localStorage.getItem(storageKey) ?? "");
        setScopeIdState(stored === "" ? ALL_SCOPES : resolveStoredScope(stored, items));
      })
      .catch((cause: Error) => {
        if (cancelled) return;
        setSpacesError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  const setScopeId = useCallback(
    (next: string) => {
      setScopeIdState(next);
      const storageKey =
        identity === null ? null : scopeStorageKey(identity.accountId, identity.legacy);
      if (storageKey !== null) window.localStorage.setItem(storageKey, next);
      const url = new URL(window.location.href);
      if (next === ALL_SCOPES) url.searchParams.delete("scope");
      else url.searchParams.set("scope", next);
      // replaceState：切相册是筛选，不该往浏览器历史里塞一条。
      window.history.replaceState(null, "", url);
    },
    [identity],
  );

  const value = useMemo<ScopeValue>(
    () => ({ scopeId, spaces, setScopeId, spacesError }),
    [scopeId, spaces, setScopeId, spacesError],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeValue {
  const value = useContext(ScopeContext);
  if (value === null) throw new Error("useScope 必须在 ScopeProvider 内使用");
  return value;
}

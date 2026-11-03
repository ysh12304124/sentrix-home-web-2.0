import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { csrfToken, setCookieNamespace } from "../shared/csrf";

export interface Identity {
  account_id: string;
  display_name: string;
  role: "user" | "admin";
  admin_only: boolean;
}

interface AuthValue {
  identity: Identity | null;
  loading: boolean;
  adminOnly: boolean;
  login: (accountId: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

const FRONTEND_BUILD = (import.meta.env.VITE_BUILD_SHA as string | undefined) ?? "";
const RELOAD_MARKER = "sentrix.buildReloaded";

async function fetchIdentity(): Promise<Identity | null> {
  const response = await fetch("/control/v1/auth/me", { credentials: "same-origin" });
  if (response.status === 401 || response.status === 403) return null;
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok || !contentType.includes("application/json")) {
    // 旧 8191 没有控制面：保持本地单用户形态。
    return { account_id: "local", display_name: "本地用户", role: "user", admin_only: false };
  }
  const body = (await response.json()) as Partial<Identity>;
  if (typeof body.account_id !== "string" || typeof body.role !== "string") return null;
  return {
    account_id: body.account_id,
    display_name: typeof body.display_name === "string" ? body.display_name : body.account_id,
    role: body.role === "admin" ? "admin" : "user",
    admin_only: body.admin_only === true,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);
  const [adminOnly, setAdminOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      let meta: { admin_only?: boolean; cookie_namespace?: string; frontend_build?: string } = {};
      try {
        const response = await fetch("/control/v1/meta", { credentials: "same-origin" });
        if (response.ok) meta = (await response.json()) as {
          admin_only?: boolean;
          cookie_namespace?: string;
          frontend_build?: string;
        };
      } catch {
        // 没有控制面（旧实例）时按普通入口处理。
      }
      if (
        FRONTEND_BUILD !== "" &&
        meta.frontend_build !== undefined &&
        meta.frontend_build !== "" &&
        meta.frontend_build !== FRONTEND_BUILD
      ) {
        // 部署了新构建：旧标签页自动换一次，避免继续跑旧代码。
        try {
          if (sessionStorage.getItem(RELOAD_MARKER) !== meta.frontend_build) {
            sessionStorage.setItem(RELOAD_MARKER, meta.frontend_build);
            window.location.reload();
            return;
          }
        } catch {
          // 无 sessionStorage 时最多刷新一次，由 marker 缺失避免循环；直接继续。
        }
      }
      let value: Identity | null = null;
      try {
        value = await fetchIdentity();
      } catch {
        value = null;
      }
      if (cancelled) return;
      // 8210 与 8220 可能在同一浏览器各有一份会话；后续写请求只带当前入口的 CSRF。
      if (meta.cookie_namespace !== undefined) setCookieNamespace(meta.cookie_namespace);
      setAdminOnly(meta.admin_only === true);
      setIdentity(value);
      setLoading(false);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (accountId: string, password: string) => {
    const response = await fetch("/control/v1/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId, password }),
    });
    if (!response.ok) {
      const raw = await response.text();
      throw new Error(raw || `登录失败（${response.status}）`);
    }
    const next = await fetchIdentity();
    if (next === null) throw new Error("登录成功但会话未建立");
    setIdentity(next);
  }, []);

  const logout = useCallback(async () => {
    await fetch("/control/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-csrf-token": csrfToken() },
    }).catch(() => undefined);
    setIdentity(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ identity, loading, adminOnly, login, logout }),
    [identity, loading, adminOnly, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return value;
}

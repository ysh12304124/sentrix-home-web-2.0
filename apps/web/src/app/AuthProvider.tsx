import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { clearFeedbackDraft } from "../features/feedback/draft";
import { csrfToken, setCookieNamespace } from "../shared/csrf";

export interface Identity {
  account_id: string;
  display_name: string;
  role: "user" | "admin";
  admin_only: boolean;
  /** 引导：当前服务端版本与账号已见版本；旧控制面没有这两个字段。 */
  onboarding_version?: number;
  onboarding_seen_version?: number;
  onboarding_show?: boolean;
}

interface AuthValue {
  identity: Identity | null;
  loading: boolean;
  /** 认证服务不可用（网络/5xx/畸形响应）；与“未登录”必须分开。 */
  authError: string | null;
  adminOnly: boolean;
  login: (accountId: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

const FRONTEND_BUILD = (import.meta.env.VITE_BUILD_SHA as string | undefined) ?? "";
const LEGACY_LOCAL_AUTH = (import.meta.env.VITE_LEGACY_LOCAL_AUTH as string | undefined) === "1";
const RELOAD_MARKER = "sentrix.buildReloaded";

type IdentityResult =
  | { kind: "identity"; identity: Identity }
  | { kind: "anonymous" }
  | { kind: "unavailable"; message: string };

async function fetchIdentity(): Promise<IdentityResult> {
  let response: Response;
  try {
    response = await fetch("/control/v1/auth/me", { credentials: "same-origin" });
  } catch {
    return { kind: "unavailable", message: "认证服务暂时无法连接" };
  }
  if (response.status === 401 || response.status === 403) return { kind: "anonymous" };
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok || !contentType.includes("application/json")) {
    if (LEGACY_LOCAL_AUTH) {
      // 只有明确打上旧版构建标志时才允许无控制面运行；默认入口不自动回退。
      return {
        kind: "identity",
        identity: {
          account_id: "local",
          display_name: "本地用户",
          role: "user",
          admin_only: false,
        },
      };
    }
    return {
      kind: "unavailable",
      message: response.ok ? "认证服务返回了无法识别的响应" : `认证服务暂不可用（${response.status}）`,
    };
  }
  let body: Partial<Identity>;
  try {
    body = (await response.json()) as Partial<Identity>;
  } catch {
    return { kind: "unavailable", message: "认证服务返回了无法识别的响应" };
  }
  if (typeof body.account_id !== "string" || typeof body.role !== "string") {
    return { kind: "unavailable", message: "认证服务返回了不完整的账号信息" };
  }
  return {
    kind: "identity",
    identity: {
      account_id: body.account_id,
      display_name: typeof body.display_name === "string" ? body.display_name : body.account_id,
      role: body.role === "admin" ? "admin" : "user",
      admin_only: body.admin_only === true,
      onboarding_version:
        typeof body.onboarding_version === "number" ? body.onboarding_version : undefined,
      onboarding_seen_version:
        typeof body.onboarding_seen_version === "number" ? body.onboarding_seen_version : undefined,
      onboarding_show: body.onboarding_show === true,
    },
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [adminOnly, setAdminOnly] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setAuthError(null);
      let meta: { admin_only?: boolean; cookie_namespace?: string; frontend_build?: string } = {};
      try {
        const response = await fetch("/control/v1/meta", { credentials: "same-origin" });
        if (response.ok) meta = (await response.json()) as {
          admin_only?: boolean;
          cookie_namespace?: string;
          frontend_build?: string;
        };
      } catch {
        // 控制面不可用时下面由 /auth/me 给出“认证暂不可用”；这里只少一个构建校验。
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
      const result = await fetchIdentity();
      if (cancelled) return;
      // 8210 与 8220 可能在同一浏览器各有一份会话；后续写请求只带当前入口的 CSRF。
      if (meta.cookie_namespace !== undefined) setCookieNamespace(meta.cookie_namespace);
      setAdminOnly(meta.admin_only === true);
      if (result.kind === "identity") {
        setIdentity(result.identity);
        setAuthError(null);
      } else if (result.kind === "anonymous") {
        setIdentity(null);
        setAuthError(null);
      } else {
        // 认证服务故障不能解释成“未登录”或旧版实例；保留身份为空并显示重试。
        setIdentity(null);
        setAuthError(result.message);
      }
      setLoading(false);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

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
    const result = await fetchIdentity();
    if (result.kind === "identity") {
      setIdentity(result.identity);
      setAuthError(null);
      return;
    }
    if (result.kind === "anonymous") throw new Error("登录成功但会话未建立");
    throw new Error(result.message);
  }, []);

  const logout = useCallback(async () => {
    const accountId = identity?.account_id;
    await fetch("/control/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-csrf-token": csrfToken() },
    }).catch(() => undefined);
    if (accountId !== undefined && accountId !== "local") clearFeedbackDraft(accountId);
    setIdentity(null);
  }, [identity]);

  const value = useMemo<AuthValue>(
    () => ({ identity, loading, authError, adminOnly, login, logout, retry }),
    [identity, loading, authError, adminOnly, login, logout, retry],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return value;
}

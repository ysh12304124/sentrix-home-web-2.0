import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export interface Identity {
  account_id: string;
  display_name: string;
  role: "user" | "admin";
}

interface AuthValue {
  identity: Identity | null;
  loading: boolean;
  login: (accountId: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)sentrix_control_csrf=([^;]*)/);
  return match === null ? "" : decodeURIComponent(match[1]);
}

async function fetchIdentity(): Promise<Identity | null> {
  const response = await fetch("/control/v1/auth/me", { credentials: "same-origin" });
  if (response.status === 401 || response.status === 403) return null;
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok || !contentType.includes("application/json")) {
    // 旧 8191 没有控制面：保持本地单用户形态。
    return { account_id: "local", display_name: "本地用户", role: "user" };
  }
  const body = (await response.json()) as Partial<Identity>;
  if (typeof body.account_id !== "string" || typeof body.role !== "string") return null;
  return {
    account_id: body.account_id,
    display_name: typeof body.display_name === "string" ? body.display_name : body.account_id,
    role: body.role === "admin" ? "admin" : "user",
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchIdentity()
      .then((value) => {
        if (!cancelled) setIdentity(value);
      })
      .catch(() => {
        if (!cancelled) setIdentity(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
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
    () => ({ identity, loading, login, logout }),
    [identity, loading, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return value;
}

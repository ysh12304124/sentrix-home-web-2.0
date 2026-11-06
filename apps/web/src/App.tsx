/** 应用入口：认证门 → 用户路由或独立管理台。 */

import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";

import { ThemeProvider } from "./app/theme/ThemeProvider";
import { AuthProvider, useAuth } from "./app/AuthProvider";
import { NotAvailable, ROUTES, SHELL } from "./app/router";
import { ScopeProvider } from "./app/ScopeProvider";
import { AdminStandalone } from "./features/admin/AdminStandalone";
import { LoginScreen } from "./features/auth/LoginScreen";
import { ToastProvider } from "./shared/ui/Toast";

function AdminPortNotice() {
  const { logout } = useAuth();
  const target = `${window.location.protocol}//${window.location.hostname}:8220/`;
  return (
    <main className="admin-port-notice">
      <div>
        <p className="admin-eyebrow">管理员账号</p>
        <h1>请使用独立管理入口</h1>
        <p>体验用户与管理员使用两个互不影响的会话，避免切换账号后丢失当前用户状态。</p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <a className="primary-action" href={target}>
            打开管理台
          </a>
          <button type="button" className="ghost-action" onClick={() => void logout()}>
            退出管理员账号
          </button>
        </div>
      </div>
    </main>
  );
}

function AuthUnavailable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main className="login-screen loading">
      <section className="login-card" style={{ maxWidth: 420 }}>
        <h2>暂时无法确认登录状态</h2>
        <p role="alert">{message}</p>
        <p className="login-sub">服务恢复后重试即可，不会丢失你已有的相册和资料。</p>
        <button type="button" onClick={onRetry}>重试</button>
      </section>
    </main>
  );
}

function AppRoutes() {
  const { identity, loading, adminOnly, authError, retry } = useAuth();
  const location = useLocation();
  if (loading) {
    return <main className="login-screen loading">正在载入…</main>;
  }
  if (authError !== null) {
    return <AuthUnavailable message={authError} onRetry={retry} />;
  }
  if (adminOnly) {
    if (identity === null || identity.role !== "admin") {
      return <LoginScreen adminOnly />;
    }
    return <AdminStandalone />;
  }
  if (identity === null) {
    return <LoginScreen />;
  }
  if (identity.role === "admin") {
    return <AdminPortNotice />;
  }
  // 第一次登录（还没看过引导）先落资料导入页：这时候回忆页必然是空的，
  // 让用户先看到“怎么添加”比先看到空回忆更有用。老用户仍从回忆页进入。
  if (identity.onboarding_seen_version === 0 && location.pathname === "/") {
    return <Navigate to="/imports" replace />;
  }
  return (
    <ScopeProvider>
      <Routes>
        <Route element={<SHELL />}>
          {Object.entries(ROUTES).map(([path, Page]) => (
            <Route key={path} path={path} element={<Page />} />
          ))}
          <Route path="*" element={<NotAvailable />} />
        </Route>
      </Routes>
    </ScopeProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <ThemeProvider>
            <AppRoutes />
          </ThemeProvider>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}

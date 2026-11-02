/** 应用入口：认证门 → 路由宿主。 */

import { BrowserRouter, Route, Routes } from "react-router";

import { AuthProvider, useAuth } from "./app/AuthProvider";
import { NotAvailable, ROUTES, SHELL } from "./app/router";
import { ScopeProvider } from "./app/ScopeProvider";
import { LoginScreen } from "./features/auth/LoginScreen";
import { ToastProvider } from "./shared/ui/Toast";

function AppRoutes() {
  const { identity, loading } = useAuth();
  if (loading) {
    return <main className="login-screen">正在载入…</main>;
  }
  if (identity === null) {
    return <LoginScreen />;
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
          <AppRoutes />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}

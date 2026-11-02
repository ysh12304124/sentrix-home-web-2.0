import { useAuth } from "../../app/AuthProvider";
import { AdminPage } from "./AdminPage";

/** 独立管理入口（8220）：不加载用户壳层，也不依赖普通账号会话。 */
export function AdminStandalone() {
  const { identity, logout } = useAuth();
  return (
    <div className="admin-standalone">
      <header className="admin-standalone-bar">
        <div className="admin-standalone-brand">
          <span>S</span>
          <div>
            <strong>Sentrix 管理台</strong>
            <small>独立入口 · 仅管理员</small>
          </div>
        </div>
        <div className="admin-standalone-user">
          <span>{identity?.display_name ?? "管理员"}</span>
          <button type="button" className="ghost-action" onClick={() => void logout()}>
            退出
          </button>
        </div>
      </header>
      <main className="admin-standalone-body">
        <AdminPage />
      </main>
    </div>
  );
}

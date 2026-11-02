/**
 * 壳层：240px 侧边栏 + 72px 顶栏 + 内容区。
 *
 * **逐块移植 1.0 `src/app.js:237-238` 的 `shell()` 模板串。** 三处行为直接来自那两行，
 * 不是我自己定的：
 *
 * 1. 面包屑里的「返回」**只在非概览页出现**（`:238` 的三元 `state.view !== "overview"`）；
 * 2. 当前项高亮走 `.nav-item.active`（1.0 `styles.css:44`：浅色字 + lime 底）；
 * 3. 后端不可用时底部脉冲文案是「本地服务不可用」（`:238` 的 `backendError` 三元）。
 */

import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router";

import { NAV_ITEMS, SYSTEM_ITEMS, activeNavItem } from "./nav";
import { useScope } from "./ScopeProvider";
import { CommandPalette } from "./CommandPalette";
import { FeedbackPanel } from "../features/feedback/FeedbackPanel";
import { useAuth } from "./AuthProvider";
import { fetchHealth } from "../shared/api/client";
import { useToast } from "../shared/ui/Toast";

function NavButton({ icon, label, active, onClick }: { icon: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className={active ? "nav-item active" : "nav-item"} onClick={onClick}>
      <span className="icon" aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </button>
  );
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const { scopeId, spaces, setScopeId } = useScope();
  const { notify } = useToast();
  const { identity, logout } = useAuth();
  const [backendState, setBackendState] = useState<"ok" | "degraded" | "down">("ok");
  const [overlay, setOverlay] = useState<"command" | "feedback" | null>(null);

  /**
   * 切换相册。1.0 的 `select-space` 直接写 state——但写 `localStorage` 会抛
   * （Safari 隐私模式、存储配额满），而抛出来时范围已经变了、侧边栏却停在旧值。
   * 这里把失败接成 Toast，并且**不吞掉异常后继续**：切换失败就保持原范围。
   */
  function switchScope(next: string) {
    if (next !== "" && !spaces.some((space) => space.id === next)) {
      notify("相册切换失败：该相册已不在列表中", "error");
      return;
    }
    try {
      setScopeId(next);
    } catch (cause) {
      notify(`相册切换失败：${(cause as Error).message}`, "error");
    }
  }

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((health) => {
        if (cancelled) return;
        setBackendState(health.status === "ok" ? "ok" : "degraded");
      })
      .catch(() => !cancelled && setBackendState("down"));
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  const current = activeNavItem(location.pathname);
  const activeSpace = spaces.find((space) => space.id === scopeId);
  const spaceLabel = activeSpace?.name ?? "全部相册";

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark">S</span>
          <div>
            <strong>Sentrix</strong>
            <small>Home Memory</small>
          </div>
        </div>

        <label className="space-switcher">
          <span className="avatar tiny">S</span>
          <span>
            <b>当前相册</b>
            <select
              aria-label="切换全部相册或独立相册"
              value={scopeId}
              onChange={(event) => switchScope(event.target.value)}
            >
              <option value="">全部相册</option>
              {spaces.map((space) => (
                <option key={space.id} value={space.id}>{space.name}</option>
              ))}
            </select>
            <small>{scopeId === "" ? "正在查看全部相册" : `正在查看 ${spaceLabel}`}</small>
          </span>
        </label>

        <div className="side-label">家庭记忆</div>
        <nav className="main-nav">
          {NAV_ITEMS.map((item) => (
            <NavButton
              key={item.id}
              icon={item.icon}
              label={item.label}
              active={current?.id === item.id}
              onClick={() => navigate(item.path)}
            />
          ))}
        </nav>

        {identity?.role === "admin" ? (
          <>
            <div className="side-label lower">管理</div>
            <nav className="main-nav">
              {SYSTEM_ITEMS.map((item) => (
                <NavButton
                  key={item.id}
                  icon={item.icon}
                  label={item.label}
                  active={current?.id === item.id}
                  onClick={() => navigate(item.path)}
                />
              ))}
            </nav>
          </>
        ) : null}

        <div className="sidebar-footer">
          <div className="local-pulse">
            <i />
            <span>
              {backendState === "down"
                ? "本地服务不可用"
                : backendState === "degraded"
                  ? "部分功能暂不可用"
                  : "服务正常"}
            </span>
          </div>
          <small>Sentrix Home · 0.2.0</small>
        </div>
      </aside>

      <div className="main-content">
        <header className="topbar">
          <div className="breadcrumbs">
            {current !== undefined && current.id !== "memories" ? (
              <>
                <button type="button" className="crumb-back" onClick={() => navigate(-1)}>←返回</button>
                <b>/</b>
              </>
            ) : null}
            <Link to="/" style={{ color: "inherit" }}>Sentrix Home</Link>
            {current ? (
              <>
                <b>/</b>
                <strong>{current.label}</strong>
              </>
            ) : null}
          </div>
          <div className="top-actions">
            <button
              type="button"
              className="text-button"
              onClick={() => setOverlay("feedback")}
            >
              反馈问题
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="打开命令搜索"
              onClick={() => setOverlay("command")}
            >
              ⌘
            </button>
            <span className="top-user">
              <span className="avatar tiny">{identity?.display_name.slice(0, 1) ?? "S"}</span>
              <span>{identity?.display_name ?? spaceLabel}</span>
              {identity?.account_id !== "local" ? (
                <button type="button" className="crumb-back" onClick={() => void logout()}>
                  退出
                </button>
              ) : null}
            </span>
          </div>
        </header>
        <div className="view-root">
          <Outlet />
        </div>
      </div>

      {overlay === "command" ? (
        <CommandPalette onClose={() => setOverlay(null)} />
      ) : null}
      {overlay === "feedback" ? <FeedbackPanel onClose={() => setOverlay(null)} /> : null}
    </main>
  );
}

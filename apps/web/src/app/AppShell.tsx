/**
 * 壳层：顶部横向导航 + 全局动作 + 内容区。
 *
 * 六个用户入口按“回忆、提问、时间线、人物、资料库、添加资料”排列；宽屏一行放下，
 * 中等宽度把后三项收进有文字的“更多”，窄屏全部收进有文字的“菜单”。管理入口只在
 * 独立 8220 端口出现，不进入普通用户导航。
 */

import { useEffect, useRef, useState, type RefObject } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { NAV_ITEMS } from "./nav";
import { useTheme } from "./theme/ThemeProvider";
import { ThemePicker } from "./theme/ThemePicker";
import { GuidedTour } from "./GuidedTour";
import { TOURS } from "./tours";
import { FeedbackPanel } from "../features/feedback/FeedbackPanel";
import { UploadsProvider } from "../features/imports/UploadsProvider";
import { Onboarding } from "./Onboarding";
import { OverlayProvider } from "./OverlayContext";
import { useAuth } from "./AuthProvider";
import { fetchHealth } from "../shared/api/client";
import { newOperationId, reportClientEvent } from "../shared/clientEvents";
import { useToast } from "../shared/ui/Toast";

type HealthState = "ok" | "degraded" | "unknown";

function NavLinkItem({ path, label, onNavigate }: { path: string; label: string; onNavigate?: () => void }) {
  return (
    <NavLink
      to={path}
      end={path === "/"}
      className="top-nav-link"
      onClick={() => {
        void reportClientEvent({
          eventName: "nav.click",
          result: "ok",
          operationId: newOperationId(),
          scopeId: "",
        });
        onNavigate?.();
      }}
    >
      {label}
    </NavLink>
  );
}

export function AppShell() {
  const { enabled: appearanceEnabled } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const { notify } = useToast();
  const { identity, logout, markOnboardingSeen } = useAuth();
  const [health, setHealth] = useState<HealthState>("unknown");
  const [lastHealthAt, setLastHealthAt] = useState<number | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [overlay, setOverlay] = useState<"guide" | "feedback" | "appearance-feedback" | "theme" | null>(null);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const autoOpenedOnboarding = useRef(false);
  const moreRef = useRef<HTMLDetailsElement | null>(null);
  const menuRef = useRef<HTMLDetailsElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const result = await fetchHealth();
        if (cancelled) return;
        setHealth(result.status === "ok" ? "ok" : "degraded");
        setLastHealthAt(Date.now());
      } catch {
        if (!cancelled) setHealth("unknown");
      }
    };
    void check();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void check();
    }, 30_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void check();
    };
    // 时间流逝本身也要触发重渲染，否则旧的绿色状态会一直留在屏幕上。
    const ticker = window.setInterval(() => setClock(Date.now()), 15_000);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.clearInterval(ticker);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    void reportClientEvent({
      eventName: "page.view",
      result: "ok",
      operationId: newOperationId(),
    });
    // 页面切换关掉展开的导航菜单。
    moreRef.current?.removeAttribute("open");
    menuRef.current?.removeAttribute("open");
  }, [location.pathname]);

  useEffect(() => {
    if (autoOpenedOnboarding.current) return;
    if (identity?.onboarding_show !== true) return;
    autoOpenedOnboarding.current = true;
    setOnboardingOpen(true);
    void reportClientEvent({ eventName: "onboarding.open", result: "ok" });
  }, [identity]);

  const finishOnboarding = async (kind: "skip" | "complete") => {
    setOnboardingOpen(false);
    void reportClientEvent({
      eventName: kind === "skip" ? "onboarding.skip" : "onboarding.complete",
      result: "ok",
    });
    try {
      await markOnboardingSeen();
    } catch {
      notify("这次选择尚未保存，下次登录可能再次出现。", "error");
    }
  };

  const current = NAV_ITEMS.find(
    (item) => item.path === location.pathname || (item.path !== "/" && location.pathname.startsWith(`${item.path}/`)),
  );
  const stale = lastHealthAt === null || clock - lastHealthAt > 90_000;
  const healthLabel = stale || health === "unknown"
    ? "状态待确认"
    : health === "ok"
      ? "服务正常"
      : "部分功能暂不可用";
  const healthDetail = lastHealthAt === null
    ? "还没有确认过服务状态"
    : `最后确认 ${new Date(lastHealthAt).toLocaleTimeString("zh-CN", { hour12: false })}`;

  const closeDetails = (ref: RefObject<HTMLDetailsElement | null>) => {
    ref.current?.removeAttribute("open");
    ref.current?.querySelector("summary")?.focus();
  };

  return (
    <main className="app-shell">
      <header className="app-topbar">
        <Link to="/" className="topbar-brand">
          <span className="brand-mark">S</span>
          <span>
            <strong>Sentrix</strong>
            <small>家庭记忆</small>
          </span>
        </Link>

        <nav className="top-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLinkItem key={item.id} path={item.path} label={item.label} />
          ))}

          <details
            className="nav-more"
            ref={moreRef}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeDetails(moreRef);
            }}
          >
            <summary>更多</summary>
            <div className="nav-popover">
              {NAV_ITEMS.slice(3).map((item) => (
                <NavLinkItem
                  key={item.id}
                  path={item.path}
                  label={item.label}
                  onNavigate={() => closeDetails(moreRef)}
                />
              ))}
            </div>
          </details>

          <details
            className="nav-menu"
            ref={menuRef}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeDetails(menuRef);
            }}
          >
            <summary>菜单</summary>
            <div className="nav-popover">
              {NAV_ITEMS.map((item) => (
                <NavLinkItem
                  key={item.id}
                  path={item.path}
                  label={item.label}
                  onNavigate={() => closeDetails(menuRef)}
                />
              ))}
              <div className="nav-popover-divider" />
              {appearanceEnabled ? <button type="button" className="nav-popover-action" onClick={() => { closeDetails(menuRef); setOverlay("theme"); }}>外观</button> : null}
              <button
                type="button"
                className="nav-popover-action"
                onClick={() => {
                  closeDetails(menuRef);
                  setOverlay("guide");
                  void reportClientEvent({ eventName: "help.open", result: "ok" });
                }}
              >
                如何使用
              </button>
              <button
                type="button"
                className="nav-popover-action"
                onClick={() => {
                  closeDetails(menuRef);
                  setOverlay("feedback");
                  void reportClientEvent({ eventName: "feedback.open", result: "ok" });
                }}
              >
                反馈问题
              </button>
            </div>
          </details>
        </nav>

        <div className="topbar-actions">
          {appearanceEnabled ? <button type="button" className="text-button topbar-action" onClick={() => setOverlay("theme")}>外观</button> : null}
          <span
            className="health-pill"
            data-state={stale ? "unknown" : health}
            title={healthDetail}
          >
            <i aria-hidden="true" />
            <span>{healthLabel}</span>
          </span>
          <button
            type="button"
            className="text-button topbar-action"
            onClick={() => {
              setOverlay("guide");
              void reportClientEvent({ eventName: "help.open", result: "ok" });
            }}
          >
            如何使用
          </button>
          <button
            type="button"
            className="text-button topbar-action"
            onClick={() => {
              setOverlay("feedback");
              void reportClientEvent({ eventName: "feedback.open", result: "ok" });
            }}
          >
            反馈
          </button>
          <span className="top-user">
            <span className="avatar tiny">{identity?.display_name.slice(0, 1) ?? "S"}</span>
            <span className="top-user-name">{identity?.display_name ?? "家庭"}</span>
            {identity?.account_id !== "local" ? (
              <button
                type="button"
                className="text-button logout-action"
                onClick={() => {
                  void reportClientEvent({ eventName: "auth.logout", result: "ok" });
                  void logout();
                }}
              >
                退出
              </button>
            ) : null}
          </span>
        </div>
      </header>

      <div className="main-content">
        <UploadsProvider>
          <OverlayProvider blocked={overlay !== null || onboardingOpen}>
            <div
              className={location.pathname === "/" ? "view-root memory-view" : "view-root"}
              data-page={current?.id ?? "unknown"}
            >
              <Outlet />
            </div>
          </OverlayProvider>
        </UploadsProvider>
      </div>

      {overlay === "guide" ? (
        <GuidedTour
          pageLabel={current?.label ?? "回忆"}
          steps={TOURS[current?.id ?? "memories"] ?? TOURS.memories}
          onClose={() => setOverlay(null)}
          onOpenOnboarding={() => {
            setOnboardingOpen(true);
            void reportClientEvent({ eventName: "onboarding.open", result: "ok" });
          }}
        />
      ) : null}
      {overlay === "theme" ? <ThemePicker onClose={() => setOverlay(null)} onFeedback={() => setOverlay("appearance-feedback")} /> : null}
      {overlay === "feedback" || overlay === "appearance-feedback" ? <FeedbackPanel appearanceFeedback={overlay === "appearance-feedback"} onClose={() => setOverlay(null)} /> : null}
      {onboardingOpen ? (
        <Onboarding
          onSkip={() => void finishOnboarding("skip")}
          onComplete={() => void finishOnboarding("complete")}
          onNavigate={(path) => navigate(path)}
        />
      ) : null}
    </main>
  );
}

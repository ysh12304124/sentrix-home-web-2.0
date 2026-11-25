import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../shared/api/client", async (original) => ({
  ...await original<typeof import("../shared/api/client")>(),
  fetchHealth: vi.fn(),
}));

import * as client from "../shared/api/client";
import { AppShell } from "./AppShell";
import { AuthProvider } from "./AuthProvider";
import { ScopeProvider } from "./ScopeProvider";
import { ToastProvider } from "../shared/ui/Toast";

function renderShell() {
  return render(
    <ToastProvider>
      <AuthProvider>
        <ScopeProvider>
          <MemoryRouter initialEntries={["/"]}>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<div>回忆内容</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </ScopeProvider>
      </AuthProvider>
    </ToastProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL) => {
    if (url === "/control/v1/meta") return Response.json({ admin_only: false });
    if (url === "/control/v1/auth/me") return Response.json({
      account_id: "local", display_name: "体验用户", role: "user", admin_only: false,
    });
    if (url === "/control/v1/client-events") return new Response(null, { status: 204 });
    throw new Error(`Unexpected request: ${String(url)}`);
  }));
  vi.mocked(client.fetchHealth).mockResolvedValue({
    status: "ok", version: "0.2.0", database: "ok", embeddings: "ok",
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("AppShell 壳层入口", () => {
  it("顶栏提供六个用户入口，移除旧命令、测评和管理入口", async () => {
    renderShell();
    await screen.findByText("体验用户");
    const nav = within(screen.getByRole("navigation", { name: "主导航" }));
    for (const [label, path] of [
      ["回忆", "/"], ["提问", "/assistant"], ["时间线", "/timeline"],
      ["人物", "/people"], ["资料库", "/library"], ["添加资料", "/imports"],
    ]) {
      expect(nav.getAllByRole("link", { name: label })[0].getAttribute("href")).toBe(path);
    }
    expect(screen.queryByLabelText("打开命令搜索")).toBeNull();
    expect(screen.queryByText("QA 测评")).toBeNull();
    expect(nav.queryByRole("link", { name: "管理" })).toBeNull();
  });

  it("如何使用打开当前页指引，Escape 关闭", async () => {
    renderShell();
    await screen.findByText("体验用户");
    fireEvent.click(screen.getAllByRole("button", { name: "如何使用" }).at(-1)!);
    const dialog = screen.getByRole("dialog", { name: "回忆如何使用：回忆会自动翻页" });
    expect(dialog.textContent).toContain("画面加载完成后才开始计时");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("顶栏反馈打开原反馈表单，不打开外部窗口", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderShell();
    await screen.findByText("体验用户");
    fireEvent.click(screen.getByRole("button", { name: "反馈" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByLabelText("发生了什么")).toBeTruthy();
    expect(open).not.toHaveBeenCalled();
  });

  it("清除旧相册范围，存储不可用时仍能渲染全部资料入口", async () => {
    localStorage.setItem("sentrix.scopeId", "album-1");
    window.history.replaceState(null, "", "/?scope=album-1&date=2026-01-02");
    const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("storage denied");
    });
    renderShell();
    await screen.findByText("体验用户");
    expect(remove).toHaveBeenCalledWith("sentrix.scopeId");
    expect(window.location.search).toBe("?date=2026-01-02");
    expect(screen.queryByLabelText("切换全部相册或独立相册")).toBeNull();
    await waitFor(() => expect(screen.getByText("服务正常")).toBeTruthy());
    expect(screen.getByText("回忆内容")).toBeTruthy();
  });
});

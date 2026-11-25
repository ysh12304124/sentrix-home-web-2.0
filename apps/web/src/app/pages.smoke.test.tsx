import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../shared/api/client")>();
  return {
    ...actual,
    listAssetPage: vi.fn(),
    listImportJobs: vi.fn(),
    fetchImportProgress: vi.fn(),
    fetchRotation: vi.fn(),
    fetchTimelineCalendar: vi.fn(),
    getFamilyGraph: vi.fn(),
    listMediaDeletions: vi.fn(),
    listConversations: vi.fn(),
    createConversation: vi.fn(),
  };
});

import * as client from "../shared/api/client";
import { AuthProvider } from "./AuthProvider";
import { UploadsProvider } from "../features/imports/UploadsProvider";
import { ScopeProvider } from "../app/ScopeProvider";
import { ROUTES } from "../app/router";
import { ToastProvider } from "../shared/ui/Toast";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: true, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL) => {
    if (url === "/control/v1/meta") return Response.json({ admin_only: false });
    if (url === "/control/v1/auth/me") return Response.json({
      account_id: "smoke-admin", display_name: "测试管理员", role: "admin", admin_only: false,
    });
    if (url === "/control/v1/admin/overview") return Response.json({
      sampled_at: "2026-01-01T00:00:00Z", stale: false, services: { users: {} }, units: {},
    });
    if (String(url).startsWith("/control/v1/admin/")) return Response.json({ items: [] });
    if (url === "/control/v1/client-events") return new Response(null, { status: 204 });
    throw new Error(`Unexpected request: ${String(url)}`);
  }));
});

function mockClient(hasData: boolean) {
  const items = hasData ? [{
    id: "asset1", file_name: "a.jpg", media_type: "image", mime_type: "image/jpeg", size_bytes: 1,
    status: "processed", captured_at: "2026-01-01T00:00:00+00:00", gps: null,
  }] : [];
  vi.mocked(client.listAssetPage).mockResolvedValue({
    items, total: items.length, offset: 0, limit: 200, counts: { all: items.length, image: items.length },
  });
  vi.mocked(client.listImportJobs).mockResolvedValue([]);
  vi.mocked(client.fetchImportProgress).mockResolvedValue({
    library: { total: hasData ? 1 : 0, completed: hasData ? 1 : 0, processing: 0, failed: 0, percent: hasData ? 100 : 0 },
    postprocess: { state: "idle", stage: "", events_done: 0, events_total: 0, updated_at: null, next_attempt_at: null },
  });
  vi.mocked(client.fetchRotation).mockResolvedValue({
    sequence_id: "rotation-1", day: "2026-01-01", cursor: 0, has_more: false,
    empty_reason: hasData ? "" : "no_media",
    media: hasData ? [{
      event_id: "event1", asset_id: "asset1", media_kind: "image", title: "一次出行",
      summary: "旅行摘要", time_label: "2026-01-01", place_label: "某地",
      related_asset_count: 1, poster_url: "/poster.jpg", play_url: null,
    }] : [],
  });
  vi.mocked(client.fetchTimelineCalendar).mockResolvedValue({
    scope_id: "", unknown_count: 0, years: hasData ? [{
      year: 2026, count: 1, months: [{ month: 1, count: 1, days: [{ date: "2026-01-01", count: 1 }] }],
    }] : [],
  });
  vi.mocked(client.getFamilyGraph).mockResolvedValue({ scope_id: "", scope_ids: [], run: null, people: [], relationships: [] });
  vi.mocked(client.listMediaDeletions).mockResolvedValue([]);
  vi.mocked(client.listConversations).mockResolvedValue([]);
  vi.mocked(client.createConversation).mockResolvedValue({
    id: "conv1",
    scope_id: "album1",
    title: "新对话",
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-01T00:00:00+00:00",
    last_message_at: "2026-01-01T00:00:00+00:00",
  });
}

function renderPage(path: string) {
  const Page = ROUTES[path];
  return render(
    <ToastProvider>
      <MemoryRouter>
        <AuthProvider>
          <ScopeProvider>
            <UploadsProvider><Page /></UploadsProvider>
          </ScopeProvider>
        </AuthProvider>
      </MemoryRouter>
    </ToastProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("每页渲染冒烟", () => {
  const paths = Object.keys(ROUTES);

  it("路由表只包含六个体验入口和管理页", () => {
    expect(paths.sort()).toEqual(
      ["/", "/assistant", "/imports", "/library", "/people", "/timeline", "/admin"].sort(),
    );
  });

  for (const path of paths) {
    it(`${path} 在空数据下能渲染`, async () => {
      mockClient(false);
      renderPage(path);
      await assertPageReady(path, false);
    });

    it(`${path} 在有数据下能渲染`, async () => {
      mockClient(true);
      renderPage(path);
      await assertPageReady(path, true);
    });
  }
});

async function assertPageReady(path: string, hasData: boolean) {
  const headings: Record<string, string> = {
    "/": hasData ? "一次出行" : "这里还没有回忆",
    "/assistant": "你想找回哪一段记忆？", "/imports": "添加资料", "/people": "人物与关系",
    "/library": "所有资料", "/timeline": "时间线", "/admin": "运行监控",
  };
  expect(await screen.findByRole("heading", { name: headings[path] })).toBeTruthy();
  if (path === "/") expect(document.exitFullscreen).toBeUndefined();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/control/v1/auth/me", { credentials: "same-origin" }));
  if (path === "/imports") await waitFor(() => expect(client.fetchImportProgress).toHaveBeenCalledWith(""));
  if (path === "/timeline") {
    await waitFor(() => expect(screen.queryByText("正在读取有记录的日期…")).toBeNull());
    expect(screen.getByText(hasData ? "1 个事件 · 1 天有记录" : "还没有事件")).toBeTruthy();
    expect(client.fetchTimelineCalendar).toHaveBeenCalledWith("", "", false);
  }
  if (path === "/library") {
    await waitFor(() => expect(client.listAssetPage).toHaveBeenCalled());
    if (hasData) {
      expect((await screen.findByRole("img", { name: "资料图片" })).getAttribute("src")).toBe("/api/v1/assets/asset1/thumbnail");
    } else {
      expect(await screen.findByText("还没有资料")).toBeTruthy();
    }
  }
  if (path === "/admin") await waitFor(() => expect(fetch).toHaveBeenCalledWith("/control/v1/admin/overview", { credentials: "same-origin" }));
  expect(screen.queryByRole("alert")).toBeNull();
}

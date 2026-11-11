/**
 * 每页渲染冒烟：**空数据与有数据两态**。
 *
 * 这条门禁补的是 `router.test.tsx` 够不到的那一段。那三条断言保证「页面文件存在、
 * 且被路由挂上了」，但不保证**它能渲染**——一个页面可以在路由表里好好地躺着，
 * 一渲染就因为解构了 undefined 而白屏。
 *
 * 两态都要跑，因为这两条路径常常走的是不同分支：空数据走空状态分支，有数据走列表
 * 分支，而其中一条往往从没被执行过（本项目「声称在检查、其实没有」那一族里，
 * 「测试每次都显式传参导致默认路径从未被执行」就是这个形状）。
 */

import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../shared/api/client")>();
  return {
    ...actual,
    listAssets: vi.fn(),
    listMemorySpaces: vi.fn(),
    listPeople: vi.fn(),
    listConversations: vi.fn(),
    createConversation: vi.fn(),
    fetchTimeline: vi.fn(),
    fetchHealth: vi.fn(),
    searchAssets: vi.fn(),
    listFaceClusters: vi.fn(),
    importAsset: vi.fn(),
    listFacts: vi.fn(),
    confirmFact: vi.fn(),
    rejectFact: vi.fn(),
    getVideo: vi.fn(),
    reprocessVideo: vi.fn(),
  };
});

import * as client from "../shared/api/client";
import { ScopeProvider } from "../app/ScopeProvider";
import { ROUTES } from "../app/router";
import { ToastProvider } from "../shared/ui/Toast";

const EMPTY_SPACES = [{ id: "album1", name: "第一册", kind: "benchmark", status: "active" }];

// jsdom 没有 IntersectionObserver，而时间线用它做无限滚动。桩成「什么都不观察」——
// 冒烟测的是「这一页能不能渲染」，不是滚动加载那一段。
// 不写成 `implements IntersectionObserver`：那个接口有几个 jsdom 用不到的成员
// （scrollMargin 之类），逐一对齐只会让这条桩随 DOM 类型定义一起腐烂。
const NoopIntersectionObserver = class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): never[] {
    return [];
  }
};
vi.stubGlobal("IntersectionObserver", NoopIntersectionObserver);

function mockClient(hasData: boolean) {
  const spaces = hasData ? EMPTY_SPACES : [];
  vi.mocked(client.listMemorySpaces).mockResolvedValue(spaces);
  vi.mocked(client.listAssets).mockResolvedValue(
    hasData
      ? [
          {
            id: "asset1",
            file_name: "a.jpg",
            media_type: "image",
            mime_type: "image/jpeg",
            size_bytes: 1,
            status: "processed",
            captured_at: "2026-01-01T00:00:00+00:00",
            gps: null,
          },
        ]
      : [],
  );
  vi.mocked(client.listPeople).mockResolvedValue([]);
  vi.mocked(client.listFaceClusters).mockResolvedValue([]);
  vi.mocked(client.listConversations).mockResolvedValue([]);
  vi.mocked(client.createConversation).mockResolvedValue({
    id: "conv1",
    scope_id: "album1",
    title: "新对话",
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-01T00:00:00+00:00",
    last_message_at: "2026-01-01T00:00:00+00:00",
  });
  vi.mocked(client.fetchHealth).mockResolvedValue({
    status: "ok",
    version: "0.1.0",
    database: "ok",
    embeddings: "ok",
  });
  vi.mocked(client.listFacts).mockResolvedValue([]);
  vi.mocked(client.fetchTimeline).mockResolvedValue(
    hasData
      ? {
          days: [
            {
              date: "2026-01-01",
              events: [
                {
                  id: "event1",
                  title: "一次出行",
                  event_type: "outing",
                  time_start: "2026-01-01T10:00:00+00:00",
                  time_end: null,
                  admin_district: "",
                  place: "某地",
                  activity: "",
                  summary: "摘要",
                  confidence: 0.8,
                  photos: [],
                },
              ],
            },
          ],
          scope_id: "album1",
          offset: 0,
          limit: 5,
          total: 1,
        }
      : { scope_id: "album1", offset: 0, limit: 5, total: 0, days: [] },
  );
  vi.mocked(client.searchAssets).mockResolvedValue({
    query: "测试",
    backend: "test",
    degraded: false,
    degraded_reason: "",
    authoritative_vectors: 0,
    items: [],
  });
}

function renderPage(path: string) {
  const Page = ROUTES[path];
  return render(
    <ToastProvider>
      <MemoryRouter>
        <ScopeProvider>
          <Page />
        </ScopeProvider>
      </MemoryRouter>
    </ToastProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("每页渲染冒烟", () => {
  const paths = Object.keys(ROUTES);

  it("路由表覆盖了计划里的十条路径", () => {
    expect(paths.sort()).toEqual(
      ["/", "/assistant", "/imports", "/knowledge", "/library", "/people", "/search", "/settings", "/stories", "/timeline"].sort(),
    );
  });

  for (const path of paths) {
    it(`${path} 在空数据下能渲染`, async () => {
      mockClient(false);
      renderPage(path);
      // 不抛异常即通过；再等一拍让挂载时的取数落地，异步里的抛错也能被抓到。
      await waitFor(() => expect(document.body.textContent).not.toBe(""));
    });

    it(`${path} 在有数据下能渲染`, async () => {
      mockClient(true);
      renderPage(path);
      await waitFor(() => expect(document.body.textContent).not.toBe(""));
    });
  }
});

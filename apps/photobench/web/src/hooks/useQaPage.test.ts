import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ItemDetailResponse, ItemPage, Judge } from "../api/types.ts";
import type { QaFilters } from "./useQaPage.ts";

// `api` 全量换成 stub：这些用例要验的是 hook 自己的编排（何时重拉详情、URL 怎么拼、
// Set 怎么换引用），不是 HTTP 层。夹具越少越不容易把 stub 的行为当成 hook 的行为。
const { mockApi } = vi.hoisted(() => ({ mockApi: vi.fn() }));
vi.mock("../api/client.ts", () => ({ api: mockApi, post: vi.fn() }));

import { useQaPage } from "./useQaPage.ts";

function pageOf(items: ItemPage["items"], overrides: Partial<ItemPage> = {}): ItemPage {
  return { items, page: 1, page_size: 20, total: items.length, pages: 1, ...overrides };
}

function detailOf(index: number, judge: Judge): ItemDetailResponse {
  return { index, item: { index, judge } };
}

/** 把 `/items?` 与 `/items/{index}` 两条路径分开喂夹具。 */
function route(handlers: {
  page: (url: string) => ItemPage;
  detail?: (index: number) => ItemDetailResponse;
}): void {
  mockApi.mockImplementation((path: string) => {
    const detail = /\/items\/(\d+)$/.exec(path);
    if (detail) {
      if (!handlers.detail) return Promise.reject(new Error(`没有 detail 夹具：${path}`));
      return Promise.resolve(handlers.detail(Number(detail[1])));
    }
    if (path.includes("/items?")) return Promise.resolve(handlers.page(path));
    return Promise.reject(new Error(`未知路径：${path}`));
  });
}

beforeEach(() => {
  mockApi.mockReset();
});

describe("useQaPage · judge 增量刷新（1.0 App.vue:2269-2283）", () => {
  // 判据：`newJudge.rejudge_id && (rejudge_id 变了 || (新 status==="completed" 且旧 !== "completed"))`
  // → 重拉；否则就地合并。下面两组的**唯一差别**就是 judge，答案必须不同。

  it("rejudge_id 未变：不重拉详情，只把新 judge 合并进详情", async () => {
    const oldJudge: Judge = { rejudge_id: "r1", status: "completed", score: 1 };
    const newJudge: Judge = { rejudge_id: "r1", status: "completed", score: 2 };
    let detailCalls = 0;
    route({
      page: () => pageOf([{ index: 5, judge: newJudge }]),
      detail: (index) => {
        detailCalls += 1;
        return detailOf(index, oldJudge);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      await result.current.loadQaDetail(5, { force: true });
    });
    expect(detailCalls).toBe(1);
    expect(result.current.qaDetails[5].judge?.score).toBe(1);

    await act(async () => {
      await result.current.loadQaPage(1);
    });

    expect(detailCalls).toBe(1); // 没有重拉
    expect(result.current.qaDetails[5].judge?.score).toBe(2); // 合并生效
  });

  it("rejudge_id 变了：重拉详情", async () => {
    const oldJudge: Judge = { rejudge_id: "r1", status: "completed", score: 1 };
    const refetched: Judge = { rejudge_id: "r2", status: "running", score: null };
    let currentJudge = oldJudge;
    let detailCalls = 0;
    route({
      page: () => pageOf([{ index: 5, judge: { rejudge_id: "r2", status: "running" } }]),
      detail: (index) => {
        detailCalls += 1;
        return detailOf(index, currentJudge);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      await result.current.loadQaDetail(5, { force: true });
    });
    expect(detailCalls).toBe(1);

    currentJudge = refetched;
    await act(async () => {
      await result.current.loadQaPage(1);
    });

    expect(detailCalls).toBe(2); // 重拉
    expect(result.current.qaDetails[5].judge?.rejudge_id).toBe("r2");
    expect(result.current.qaDetails[5].judge?.score).toBeNull();
  });

  it("status 由非 completed 变 completed（rejudge_id 不变）：也重拉", async () => {
    const oldJudge: Judge = { rejudge_id: "r1", status: "running", score: null };
    let currentJudge = oldJudge;
    let detailCalls = 0;
    route({
      page: () => pageOf([{ index: 5, judge: { rejudge_id: "r1", status: "completed" } }]),
      detail: (index) => {
        detailCalls += 1;
        return detailOf(index, currentJudge);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      await result.current.loadQaDetail(5, { force: true });
    });
    currentJudge = { rejudge_id: "r1", status: "completed", score: 2 };
    await act(async () => {
      await result.current.loadQaPage(1);
    });

    expect(detailCalls).toBe(2);
    expect(result.current.qaDetails[5].judge?.score).toBe(2);
  });

  it("新 judge 没有 rejudge_id：一律不重拉，只合并（1.0 :2275 的前置判空）", async () => {
    const oldJudge: Judge = { rejudge_id: "r1", status: "running", score: 1 };
    let detailCalls = 0;
    route({
      page: () => pageOf([{ index: 5, judge: { status: "completed", score: 2 } }]),
      detail: (index) => {
        detailCalls += 1;
        return detailOf(index, oldJudge);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      await result.current.loadQaDetail(5, { force: true });
    });
    await act(async () => {
      await result.current.loadQaPage(1);
    });

    expect(detailCalls).toBe(1); // 没有重拉，尽管 status 变成了 completed
    expect(result.current.qaDetails[5].judge?.status).toBe("completed");
    expect(result.current.qaDetails[5].judge?.rejudge_id).toBe("r1");
  });
});

describe("useQaPage · 两个 Set 的响应式", () => {
  it("toggleQa 之后 openQaItems 是新实例，内容正确，旧实例不被改写", async () => {
    route({ page: () => pageOf([]), detail: (index) => detailOf(index, { score: 1 }) });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    const before = result.current.openQaItems;
    expect([...before]).toEqual([]);

    await act(async () => {
      await result.current.toggleQa({ index: 7 });
    });

    const after = result.current.openQaItems;
    expect(after).not.toBe(before); // 直接 mutate 旧 Set 会红
    expect([...after]).toEqual([7]);
    expect([...before]).toEqual([]);

    await act(async () => {
      await result.current.toggleQa({ index: 7 });
    });
    expect(result.current.openQaItems).not.toBe(after);
    expect([...result.current.openQaItems]).toEqual([]);
  });

  it("loadingQaItems 在请求挂起时是新实例且含该 index，结束后清空", async () => {
    let release: () => void = () => {};
    mockApi.mockImplementation((path: string) => {
      const detail = /\/items\/(\d+)$/.exec(path);
      if (detail) {
        const index = Number(detail[1]);
        return new Promise((resolve) => {
          release = () => resolve(detailOf(index, { score: 1 }));
        });
      }
      if (path.includes("/items?")) return Promise.resolve(pageOf([]));
      return Promise.reject(new Error(path));
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    const before = result.current.loadingQaItems;
    let pending: Promise<void> | undefined;
    await act(async () => {
      pending = result.current.toggleQa({ index: 9 });
      await Promise.resolve();
    });

    expect(result.current.loadingQaItems).not.toBe(before);
    expect([...result.current.loadingQaItems]).toEqual([9]);

    await act(async () => {
      release();
      await pending;
    });
    expect([...result.current.loadingQaItems]).toEqual([]);
  });
});

describe("useQaPage · 筛选与分页", () => {
  it("8 个下拉 + 搜索同时生效：只有全组合才命中的夹具", async () => {
    const filters: QaFilters = {
      search: "电池",
      score: "2",
      task_type: "event_memory_qa",
      tag: "夜晚",
      angle: "object",
      difficulty: "hard",
      answerability: "answerable",
      agent_status: "completed",
      primary: "tool",
    };
    const seen: string[] = [];
    route({
      page: (url) => {
        seen.push(url);
        const params = new URL(url, "http://localhost").searchParams;
        const all = (Object.keys(filters) as Array<keyof QaFilters>).every(
          (key) => params.get(key) === filters[key],
        );
        return pageOf(all ? [{ index: 1, question: "电池续航" }] : []);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      for (const key of Object.keys(filters) as Array<keyof QaFilters>) {
        result.current.setQaFilter(key, filters[key]);
      }
    });
    await act(async () => {
      await result.current.applyQaFilters();
    });

    const last = seen[seen.length - 1] ?? "";
    for (const key of Object.keys(filters)) expect(last).toContain(`${key}=`);
    expect(last).toContain("search=%E7%94%B5%E6%B1%A0"); // 「电池」按 URLSearchParams 编码
    expect(result.current.visibleQaItems).toHaveLength(1);

    // 对照：少一个条件就不成立（证明命中的是「组合」而不是别的）
    await act(async () => {
      result.current.setQaFilter("difficulty", "");
    });
    await act(async () => {
      await result.current.applyQaFilters();
    });
    expect(result.current.visibleQaItems).toHaveLength(0);
  });

  it("resetQaFilters 清空全部条件再回第 1 页", async () => {
    const seen: string[] = [];
    route({
      page: (url) => {
        seen.push(url);
        return pageOf([]);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      result.current.setQaFilter("tag", "夜晚");
      result.current.setQaFilter("search", "车");
    });
    await act(async () => {
      await result.current.resetQaFilters();
    });

    expect(result.current.qaFilters).toEqual({
      search: "",
      score: "",
      task_type: "",
      question_type: "",
      tag: "",
      angle: "",
      difficulty: "",
      answerability: "",
      agent_status: "",
      primary: "",
    });
    const last = seen[seen.length - 1] ?? "";
    expect(last).not.toContain("tag=");
    expect(last).not.toContain("search=");
    expect(last).toContain("page=1");
  });

  it("changeQaPage 夹到 [1, pages]；同页不发请求", async () => {
    const seen: string[] = [];
    route({
      page: (url) => {
        seen.push(url);
        const page = Number(new URL(url, "http://localhost").searchParams.get("page"));
        return pageOf([], { page, pages: 3 });
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      await result.current.loadQaPage(1);
    });
    expect(seen).toHaveLength(1);

    await act(async () => {
      await result.current.changeQaPage(99); // 夹到 3
    });
    expect(seen).toHaveLength(2);
    expect(seen[1]).toContain("page=3");

    await act(async () => {
      await result.current.changeQaPage(3); // 同页
    });
    expect(seen).toHaveLength(2);

    await act(async () => {
      await result.current.changeQaPage(-5); // 夹到 1
    });
    expect(seen).toHaveLength(3);
    expect(seen[2]).toContain("page=1");
  });

  it("activeRunId 为空时 loadQaPage 不发请求", async () => {
    const seen: string[] = [];
    route({
      page: (url) => {
        seen.push(url);
        return pageOf([]);
      },
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: null }));
    await act(async () => {
      await result.current.loadQaPage(1);
    });
    expect(seen).toHaveLength(0);
  });
});

describe("useQaPage · resetQaState（1.0 :2322-2326）", () => {
  it("清 qaPage/qaDetails/openQaItems，且**保留** qaFilters/qaPageSize", async () => {
    route({
      page: () => pageOf([{ index: 1 }]),
      detail: (index) => detailOf(index, { score: 1 }),
    });
    const { result } = renderHook(() => useQaPage({ activeRunId: "run1" }));

    await act(async () => {
      await result.current.loadQaPage(1);
    });
    await act(async () => {
      await result.current.toggleQa({ index: 1 });
    });
    await act(async () => {
      result.current.setQaFilter("tag", "夜晚");
    });
    await act(async () => {
      await result.current.changeQaPageSize(50);
    });

    // 前置：三样都有值，两样非默认。
    expect(result.current.qaDetails[1]).toBeTruthy();
    expect([...result.current.openQaItems]).toEqual([1]);
    expect(result.current.qaFilters.tag).toBe("夜晚");
    expect(result.current.qaPageSize).toBe(50);

    act(() => {
      result.current.resetQaState();
    });

    expect(result.current.qaDetails).toEqual({});
    expect([...result.current.openQaItems]).toEqual([]);
    // 1.0 :2323 的 page_size 沿用当前值（不是 20）。
    expect(result.current.qaPage).toEqual({ items: [], page: 1, page_size: 50, total: 0, pages: 1 });
    // 这两样 1.0 不清。
    expect(result.current.qaFilters.tag).toBe("夜晚");
    expect(result.current.qaPageSize).toBe(50);
  });
});

describe("useQaPage · visibleQaItems（1.0 :355-364）", () => {
  it("重评进行中只留本轮的 completed/failed；completed 后不过滤", async () => {
    route({ page: () => pageOf([
      { index: 1, judge: { rejudge_id: "r1", status: "completed" } },
      { index: 2, judge: { rejudge_id: "r1", status: "running" } },
      { index: 3, judge: { rejudge_id: "r0", status: "completed" } },
    ]) });
    const { result, rerender } = renderHook(
      ({ rejudge }: { rejudge: { status: string; rejudge_id: string } | null }) =>
        useQaPage({ activeRunId: "run1", activeRejudge: rejudge }),
      { initialProps: { rejudge: { status: "running", rejudge_id: "r1" } } },
    );

    await act(async () => {
      await result.current.loadQaPage(1);
    });
    expect(result.current.visibleQaItems.map((item) => item.index)).toEqual([1]);

    rerender({ rejudge: { status: "completed", rejudge_id: "r1" } });
    expect(result.current.visibleQaItems.map((item) => item.index)).toEqual([1, 2, 3]);
  });
});

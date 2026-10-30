import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.ts", () => ({ api: vi.fn(), post: vi.fn() }));

import { api } from "../api/client.ts";
import type { RunPage } from "../api/types.ts";
import { useRuns } from "./useRuns.ts";

// 1.0 参考：`services/photobench/frontend/src/App.vue`（只读权威）。
// 轮询夹具要让「开」与「该关」给出不同答案：active_count / 行状态不同即可。

const apiMock = vi.mocked(api);

function pagePayload(activeCount: number, status = "done"): RunPage {
  return {
    runs: [{ run_id: "a", status }],
    page: 1,
    page_size: 20,
    total: 1,
    pages: 1,
    has_previous: false,
    has_next: false,
    active_count: activeCount,
  };
}

beforeEach(() => {
  apiMock.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("loadRuns / changeRunPage（1.0 :2233 / :2249）", () => {
  it("loadRuns 照搬 timeout 8s / retries 2", async () => {
    apiMock.mockResolvedValue(pagePayload(0));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      await result.current.loadRuns(3);
    });

    expect(apiMock).toHaveBeenCalledWith("/runs?page=3&page_size=20", {
      timeoutMs: 8000,
      retries: 2,
    });
    expect(result.current.runPage.page).toBe(1);
  });

  it("changeRunPage 滚的是 hook 返回的 ref，不是全局查询", async () => {
    apiMock.mockResolvedValue(pagePayload(0));
    const { result } = renderHook(() => useRuns());
    const scrollIntoView = vi.fn();
    result.current.runsRegionRef.current = { scrollIntoView } as unknown as HTMLElement;

    await act(async () => {
      await result.current.changeRunPage(2);
    });

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });
});

describe("startPolling 的 2 秒轮询（1.0 :2697-2708）", () => {
  it("调用方重建详情 loader 时仍遵守两秒间隔", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const firstLoader = vi.fn().mockResolvedValue(undefined);
    const secondLoader = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderHook(
      ({ loader }) => useRuns({ loadQaPage: loader }),
      { initialProps: { loader: firstLoader } },
    );
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);
    await act(async () => { rerender({ loader: secondLoader }); });
    expect(apiMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(apiMock).toHaveBeenCalledTimes(2);
  });

  it("下一轮使用新的 QA loader，渲染不会让旧请求重叠", async () => {
    apiMock.mockImplementation(async (path) => {
      if (path.startsWith("/runs?")) return pagePayload(1, "running");
      return { run_id: "a", status: "running", summary: {} };
    });
    const firstLoader = vi.fn().mockResolvedValue(undefined);
    const secondLoader = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderHook(
      ({ loader }) => useRuns({ loadQaPage: loader }),
      { initialProps: { loader: firstLoader } },
    );
    await act(async () => {
      result.current.selectRun({ run_id: "a", status: "running" });
      await vi.advanceTimersByTimeAsync(0);
    });
    firstLoader.mockClear();
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(firstLoader).toHaveBeenCalledTimes(1);
    await act(async () => { rerender({ loader: secondLoader }); });
    expect(secondLoader).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(firstLoader).toHaveBeenCalledTimes(1);
    expect(secondLoader).toHaveBeenCalledTimes(1);
  });

  it("有任务在跑：每 2 秒一轮", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(apiMock).toHaveBeenCalledTimes(1); // 还没到 2s

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(apiMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(apiMock).toHaveBeenCalledTimes(3);
  });

  it("没有任务在跑：跑完一轮就停，不再续期", async () => {
    apiMock.mockResolvedValue(pagePayload(0, "done"));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    // 停了之后还能再拉起（1.0 `pollTimer` 归零后可再 startPolling）。
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(2);
  });

  it("卸载后不再触发", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const { result, unmount } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it("重复 startPolling 不会起第二条定时器", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(apiMock).toHaveBeenCalledTimes(2);
  });
});

describe("hasRunning（1.0 :352）", () => {
  it("active_count 或行状态任一命中即为真", async () => {
    apiMock.mockResolvedValue(pagePayload(0, "pending"));
    const { result } = renderHook(() => useRuns());
    await act(async () => {
      await result.current.loadRuns(1);
    });
    expect(result.current.hasRunning).toBe(true);
  });
});


it("评分不足满额但已完成且有错误时停止轮询", async () => {
  const payload = pagePayload(0, "completed_with_errors");
  payload.runs[0] = { ...payload.runs[0]!, qa_count: 10, summary: { judge_valid_count: 9 } };
  apiMock.mockResolvedValue(payload);
  const { result } = renderHook(() => useRuns());
  await act(async () => {
    result.current.startPolling();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.hasRunning).toBe(false);
  expect(result.current.runs[0]?.summary?.judge_valid_count).toBe(9);
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(apiMock).toHaveBeenCalledTimes(1);
});
